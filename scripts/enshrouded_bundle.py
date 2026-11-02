"""Read and write Enshrouded blueprint bundles (ShroudForge V7 ``.sfbp`` folders).

A bundle is what the Custodian's house builder places in the world (format:
docs/enshrouded-houses/blueprints/FORMAT.md). This module turns a bundle into a scene
(``custodian.enshrouded.scene.v1``) that ``enshrouded_house.py`` can paste as a fragment,
edit and check, and turns any design back into a bundle.

House axes: x = game X, y = game Z, z = game Y (up), 0.5 m voxels, origin = south-west corner
of the Flame Altar platform top (altar pivot + (-1.75, +1, -1.75) m).

  python scripts/enshrouded_bundle.py import <bundle-folder> -o piece.scene.json
  python scripts/enshrouded_bundle.py export <design.json> <bundle-folder> --label "..."
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enshrouded_house as eh  # noqa: E402

HOUSES = eh.GEOMETRY_PATH.parent
MAGIC = "SHROUDFORGE_WORLD_BLUEPRINT_V7"
PART_CELLS = 65536
ALTAR_ITEM = 3954785428
ALTAR_UUID = ("4e2560675bcf6d3e", "e34aed9ab4daf9a0")
#: Platform top corner relative to the altar pivot, metres (game X, Y, Z).
CORNER_FROM_PIVOT = (-1.75, 1.0, -1.75)


def _catalog():
    data = json.loads((HOUSES / "prop-catalog.json").read_text(encoding="utf-8"))["props"]
    return {p["item_id"]: p for p in data}, {p["debug_name"]: p for p in data}


def _materials():
    rows = json.loads((HOUSES / "materials.json").read_text(encoding="utf-8"))["materials"]
    return {r["material_id"]: r["debug_name"] for r in rows}, {r["debug_name"]: r["material_id"] for r in rows}


def _fmt(v):
    return repr(float(v)) if v != int(v) else str(int(v))


def parse_sfbp(text):
    lines = text.split("\n")
    if lines[0] != MAGIC:
        raise eh.DesignError(f"not a {MAGIC} file")
    out = {"extent": [float(v) for v in lines[2].split(",")], "kind": lines[3]}
    if out["kind"] == "voxel":
        out["dims"] = [int(v) for v in lines[4].split(",")]
        out["cells"] = [int(v) for v in lines[7].split(",")]
        out["coverage"] = [int(v) for v in lines[8].split(",")]
    n = int(lines[9])
    out["props"] = [lines[10 + i].split(",") for i in range(n)]
    return out


#: Margin kept round the built blocks when an area capture is cropped (cells: 8 m sideways,
#: 4 m of ground below, 4 m of air above): enough terrain for the ground, routes and yards.
AREA_MARGIN, AREA_BELOW, AREA_ABOVE = 16, 8, 8


def is_bundle(folder):
    """A blueprint bundle (fit.json) or a whole-area capture of an altar (capture.json + manifest)."""
    folder = Path(folder)
    return folder.is_dir() and ((folder / "fit.json").exists() or (folder / "capture.json").exists())


def _area_fit(folder):
    """fit-like view of an area capture, cropped to the built blocks plus a margin of terrain.

    The capture holds the whole altar cube (241^3 cells, mostly terrain and air). The first pass
    finds the player's blocks (material id >= 128), the second keeps only the tiles and cells
    near them, so a 120 m cube reads in seconds and fits in memory.
    """
    cap = json.loads((folder / "capture.json").read_text(encoding="utf-8"))
    lo, hi, parsed, missing = [None] * 3, [None] * 3, {}, []
    for t in cap["tiles"]:
        path = folder / t["file"]
        if not path.exists():
            missing.append(t["file"])
            continue
        text = path.read_text(encoding="utf-8")
        line = text.split("\n", 8)[7]
        if not any(int(v) & 255 >= 128 for v in line.split(",") if v != "0"):
            continue
        p = parse_sfbp(text)
        parsed[t["file"]] = p
        sx, sy, _ = p["dims"]
        ox, oy, oz = t["offset"]
        for i, v in enumerate(p["cells"]):
            if v and v & 255 >= 128:
                c = (ox + i % sx, oy + (i // sx) % sy, oz + i // (sx * sy))
                for k in range(3):
                    lo[k] = c[k] if lo[k] is None else min(lo[k], c[k])
                    hi[k] = c[k] if hi[k] is None else max(hi[k], c[k])
    if lo[0] is None:
        raise eh.DesignError(f"{folder.name}: no built blocks in the capture")
    box = ([lo[0] - AREA_MARGIN, lo[1] - AREA_BELOW, lo[2] - AREA_MARGIN],
           [hi[0] + AREA_MARGIN, hi[1] + AREA_ABOVE, hi[2] + AREA_MARGIN])
    parts = [{"file": t["file"], "offset": t["offset"]} for t in cap["tiles"]
             if (folder / t["file"]).exists() and all(
                 t["offset"][k] <= box[1][k] and t["offset"][k] + t["dims"][k] > box[0][k] for k in range(3))]
    entry = json.loads((folder / "catalog-entry.json").read_text(encoding="utf-8")) \
        if (folder / "catalog-entry.json").exists() else {}
    return {"parts": parts, "props_frame": "props.sfbp", "crop": box, "missing_parts": missing,
            "candidate": entry.get("label") or folder.name, "altar_level": cap.get("level")}


def read_bundle(folder):
    """Bundle folder -> scene dict (house axes, origin at the altar platform top corner).

    Also reads a whole-area capture (``capture.json``, ``part-*.sfbp``, ``props.sfbp``): then
    only the built blocks and the terrain round them are kept.
    """
    folder = Path(folder)
    if (folder / "fit.json").exists():
        fit = json.loads((folder / "fit.json").read_text(encoding="utf-8"))
    else:
        fit = _area_fit(folder)
    crop = fit.get("crop")
    by_id, _ = _catalog()
    mat_name, _ = _materials()
    frame = parse_sfbp((folder / fit["props_frame"]).read_text(encoding="utf-8"))
    altar = [r for r in frame["props"] if int(r[0]) == ALTAR_ITEM]
    if len(altar) != 1:
        raise eh.DesignError(f"{folder.name}: expected one altar row, found {len(altar)}")
    pivot = [float(v) for v in altar[0][1:4]]
    corner = [pivot[i] + CORNER_FROM_PIVOT[i] for i in range(3)]          # metres, game axes
    cx, cy, cz = (round(v * 2) for v in corner)                            # cells, game axes
    voxels, carved, ids, density = {}, set(), {}, {}
    for part in fit["parts"]:
        p = parse_sfbp((folder / part["file"]).read_text(encoding="utf-8"))
        sx, sy, sz = p["dims"]
        ox, oy, oz = part["offset"]
        for i, (v, cov) in enumerate(zip(p["cells"], p["coverage"])):
            if cov == 0:
                continue
            gx, gy, gz = ox + i % sx, oy + (i // sx) % sy, oz + i // (sx * sy)
            if crop and not (crop[0][0] <= gx <= crop[1][0] and crop[0][1] <= gy <= crop[1][1]
                             and crop[0][2] <= gz <= crop[1][2]):
                continue
            cell = (gx - cx, gz - cz, gy - cy)                             # house x, y, z
            if v:
                mid = v & 255
                name = mat_name.get(mid) if mid >= 128 else f"Terrain_{mid}"
                name = name or f"Material_{mid}"
                if mid < 128 and v >> 8 != 255:
                    density[cell] = v >> 8
                ids.setdefault(name, len(ids))
                voxels[cell] = name
            elif cov == 1:
                carved.add(cell)
    props = []
    for r in frame["props"]:
        item = int(r[0])
        if item == ALTAR_ITEM:
            continue
        x, y, z = (float(v) for v in r[1:4])
        rec = by_id.get(item, {})
        props.append({"prop": rec.get("debug_name", f"item_{item}"), "item_id": item,
                      "pos": [x * 2 - cx, z * 2 - cz, y * 2 - cy],
                      "rot_xyzw": [float(v) for v in r[4:8]], "scale": [float(v) for v in r[8:11]],
                      "template_uuid": [r[11], r[12]], "size_m": rec.get("size_m"),
                      "name_ru": rec.get("name_ru"), "group": rec.get("group")})
    runs = []
    for (x, y, z), m in sorted(voxels.items(), key=lambda kv: (kv[0][2], kv[0][1], kv[0][0])):
        r = runs[-1] if runs else None
        if r and r[1] == y and r[2] == z and r[4] == ids[m] and r[0] + r[3] == x:
            r[3] += 1
        else:
            runs.append([x, y, z, 1, ids[m]])
    return {"schema": eh.SCENE_SCHEMA, "name": fit.get("candidate") or folder.name, "voxel_m": eh.VOXEL_M,
            "axes": "x = game X, y = game Z, z = up; origin = altar platform top corner",
            "source": f"bundle {folder.name}", "materials": {str(i): m for m, i in ids.items()},
            "runs": runs, "props": props,
            "carved": [list(c) for c in sorted(carved)],
            **({"altar_level": fit["altar_level"]} if fit.get("altar_level") else {}),
            **({"missing_parts": len(fit["missing_parts"])} if fit.get("missing_parts") else {}),
            "terrain_density": [[*c, d] for c, d in sorted(density.items())]}


def _uuid(prop, by_name):
    if prop.get("template_uuid"):
        return prop["template_uuid"]
    rec = by_name.get(prop["prop"])
    if not rec:
        raise eh.DesignError(f"prop {prop['prop']!r} is not in prop-catalog.json")
    return [rec["template_uuid_high"], rec["template_uuid_low"]]


def write_bundle(house, folder, label, bundle_id=None, clear="above-ground"):
    """Write a design as a bundle folder the house builder can plan (still needs approval).

    ``clear`` decides what empty cells do in the world: ``above-ground`` writes air in every
    empty cell from floor level up (clears grass and trees in the crop) and keeps terrain
    below, except where the design dug; ``keep`` never touches the world outside blocks.
    """
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    by_id, by_name = _catalog()
    _, mat_id = _materials()
    bx, by, bz = eh.ALTAR_BOX
    (x0, y0, z0), (x1, y1, z1) = house.bbox()
    ground = house.ground_cells()
    ground_id = int(house.ground_material.split("_")[1])
    x0, y0, z0 = min(x0, 0), min(y0, 0), min(z0, -bz, min((c[2] for c in ground), default=0))
    x1, y1, z1 = max(x1, bx - 1), max(y1, by - 1), max(z1, eh.ALTAR_ABOVE - 1)
    sx, sy_game, sz = x1 - x0 + 1, z1 - z0 + 1, y1 - y0 + 1           # game dims: X, Y(up), Z
    if sx * sy_game * sz > 1_000_000:
        raise eh.DesignError(f"crop {sx}x{sy_game}x{sz} is over 1,000,000 cells")
    altar_cells = house.altar_cells()

    def value(gx, gy, gz):
        cell = (gx + x0, gz + y0, gy + z0)
        m = house.voxels.get(cell)
        if m:
            block = house.materials.get(m, m)
            if block.startswith("Terrain_"):
                return (house.density.get(cell, 255) << 8) | int(block.split("_")[1]), 2
            if block not in mat_id:
                raise eh.DesignError(f"material {block!r} has no voxel id in materials.json")
            return mat_id[block], 2
        if cell in ground:
            # Real terrain under the build: the floor and yard rest on it on any site.
            return (255 << 8) | ground_id, 2
        if (cell in altar_cells and cell[2] >= 0) or cell in house.dug:
            return 0, 1  # room for the altar bowl; under the floor the altar sits on blocks and ground
        if clear == "above-ground" and cell[2] >= 0:
            return 0, 1
        return 0, 0

    # Parts: horizontal slabs along game Y, at most PART_CELLS cells, each with a block.
    layer = sx * sz
    per = max(1, PART_CELLS // layer)
    if layer > PART_CELLS:
        raise eh.DesignError(f"one layer is {layer} cells; split the design across X/Z (not supported yet)")
    # Generated ground counts as occupied too: terrain-only layers are real parts, and merging
    # them as if empty would push a part over PART_CELLS.
    filled = {c[2] for c in house.voxels} | {c[2] for c in ground}
    occupied = [gy + z0 in filled for gy in range(sy_game)]
    slabs, start = [], 0
    while start < sy_game:
        end = min(sy_game, start + per)
        while not any(occupied[start:end]):     # an empty part is rejected: merge into the next or previous
            if end < sy_game and end - start < per:
                end += 1
            elif slabs:
                start = slabs.pop()[0]
            else:
                break
        slabs.append((start, end))
        start = end
    stem = folder.name.split("-v")[0] if "-v" in folder.name else folder.name
    parts, files = [], {}
    for n, (a, b) in enumerate(slabs, 1):
        h = b - a
        cells = [value(gx, gy, gz) for gz in range(sz) for gy in range(a, b) for gx in range(sx)]
        # index = x + sx * (y + sy * z): x fastest, then y, then z
        vals = [c[0] for c in cells]
        cov = [c[1] for c in cells]
        text = "\n".join([MAGIC, "y", f"{_fmt(sx / 2)},{_fmt(h / 2)},{_fmt(sz / 2)}", "voxel", f"{sx},{h},{sz}",
                          "0,0,0", "0.5,0.5,0.5", ",".join(map(str, vals)), ",".join(map(str, cov)), "0"])
        name = f"{stem}-part-{n}.sfbp"
        (folder / name).write_bytes(text.encode())
        parts.append({"file": name, "sha256": hashlib.sha256(text.encode()).hexdigest(), "dims": [sx, h, sz],
                      "offset": [0, a, 0], "cells": len(vals), "occupied": sum(1 for v in vals if v)})
    # Props frame: metres from the crop corner, game axes; the altar row last.
    rows, aabbs = [], []
    for f in house.furniture():
        if not f.get("prop"):
            continue
        # House.furniture() gives every prop its game pivot (floor, wall face or ceiling).
        px, py, pz, q = *f["pos"], f["rot_xyzw"]
        scale = f.get("scale") or [1, 1, 1]
        rec = by_name.get(f["prop"])
        if not rec:
            raise eh.DesignError(f"prop {f['prop']!r} is not in prop-catalog.json")
        hi, lo = f.get("template_uuid") or _uuid(f, by_name)
        gx, gy, gz = (px - x0) / 2, (pz - z0) / 2, (py - y0) / 2
        rows.append(",".join([str(rec["item_id"]), _fmt(gx), _fmt(gy), _fmt(gz), *(_fmt(v) for v in q),
                              *(_fmt(v) for v in scale), hi, lo]))
        aabbs.append({"item_id": rec["item_id"], "recipe": f["prop"], "local_pivot": [gx, gy, gz]})
    if len(rows) > 2000:
        raise eh.DesignError(f"{len(rows)} props; the builder takes at most 2000")
    pivot = [(-x0) / 2 - CORNER_FROM_PIVOT[0], (-z0) / 2 - CORNER_FROM_PIVOT[1], (-y0) / 2 - CORNER_FROM_PIVOT[2]]
    rows.append(",".join([str(ALTAR_ITEM), *(_fmt(v) for v in pivot), "0", "0", "0", "1", "1", "1", "1", *ALTAR_UUID]))
    frame_text = "\n".join([MAGIC, "y", f"{_fmt(sx / 2)},{_fmt(sy_game / 2)},{_fmt(sz / 2)}", "props",
                            "-", "-", "-", "-", "-", str(len(rows))] + rows)
    frame_name = f"{stem}-props-frame.sfbp"
    (folder / frame_name).write_bytes(frame_text.encode())
    bundle_id = bundle_id or folder.name
    fit = {"candidate": label, "variant": bundle_id, "acceptance_status": "prepared_offline",
           "source_origin_world": [0.0, 0.0, 0.0], "source_dims": [sx, sy_game, sz],
           "source_extent_m": [sx / 2, sy_game / 2, sz / 2],
           "source_crop_world_bounds": {"min": [0.0, 0.0, 0.0], "max": [sx / 2, sy_game / 2, sz / 2]},
           "source_anchor_world": pivot, "source_floor_top": (-z0) / 2,     # house z = 0, altar + 1 m
           "alt_item_id": ALTAR_ITEM, "alt_reference_only": True,
           "parts": parts, "props_frame": frame_name, "source_props_count": len(rows) - 1,
           "notes": ["Generated by scripts/enshrouded_bundle.py from a custodian.enshrouded.house.v1 design.",
                     "source_origin_world is the local crop corner; the planner places the crop from the live altar."]}
    (folder / "fit.json").write_text(json.dumps(fit, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "source-prop-aabbs.json").write_text(json.dumps({"props": aabbs, "overlap_count": None,
        "note": "pivots only; world AABBs are computed by the planner from live recipes"}, indent=1), encoding="utf-8")
    level = (house.spec.get("altar") or {}).get("level", 1)
    entry = {"id": bundle_id, "label": label, "entry_kind": "prepared_offline_variant", "catalog_status": "prepared_offline",
             "author": "Custodian design tool", "dimensions_m": {"full_copy_envelope_xyz": [sx / 2, sy_game / 2, sz / 2]},
             "altar_level_needed": level, "blocks": len(house.voxels), "props": len(rows) - 1}
    (folder / "catalog-entry.json").write_text(json.dumps(entry, ensure_ascii=False, indent=2), encoding="utf-8")
    for p in sorted(folder.iterdir()):
        if p.name != "manifest.json" and p.is_file():
            files[p.name] = {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
    manifest = {"id": bundle_id, "status": "prepared_offline", "label": label, "source_parts": len(parts),
                "source_reference_altar": pivot, "floor_offset_above_altar_origin_m": 1.0, "preserve_altar": True,
                "altar_body_open_square_m": [4, 4], "minimum_proven_altar_zone_m": [eh.ALTAR_CUBE_M[level]] * 3,
                "house_props": len(rows) - 1, "files": files}
    (folder / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"parts": len(parts), "dims": [sx, sy_game, sz], "props": len(rows) - 1, "blocks": len(house.voxels)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("import", help="bundle folder -> scene file (usable as a fragment)")
    a.add_argument("bundle", type=Path)
    a.add_argument("-o", "--out", type=Path, required=True)
    b = sub.add_parser("export", help="design -> bundle folder")
    b.add_argument("design", type=Path)
    b.add_argument("bundle", type=Path)
    b.add_argument("--label", required=True)
    b.add_argument("--clear", choices=("above-ground", "keep"), default="above-ground")
    args = ap.parse_args(argv)
    if args.cmd == "import":
        scene = read_bundle(args.bundle)
        args.out.write_text(json.dumps(scene, ensure_ascii=False), encoding="utf-8")
        print(f"{scene['name']}: {sum(r[3] for r in scene['runs'])} blocks, {len(scene['props'])} props, "
              f"{len(scene['materials'])} materials -> {args.out}")
        return 0
    house = eh.House(json.loads(args.design.read_text(encoding="utf-8")))
    errors, _ = house.check()
    if errors:
        print("\n".join(f"ERROR {e}" for e in errors))
        return 1
    print(json.dumps(write_bundle(house, args.bundle, args.label, clear=args.clear)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
