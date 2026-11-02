"""Build a self-contained 3D + plan viewer page for an Enshrouded house design.

The page shows every block with the game's own textures and every prop with its game model
(docs/enshrouded-houses/visual), a layer slider, a floor plan and the bill of blocks. All
textures and models are embedded, because the artifact frame does not load side files.

  python scripts/enshrouded_view.py docs/enshrouded-houses/castle.json -o castle.html
  python scripts/enshrouded_view.py castle.json estate.json -o both.html     # one page, picker
  python scripts/enshrouded_view.py <blueprints-folder> --site -o site/      # web server folder

A design is a ``custodian.enshrouded.house.v1`` file or a game blueprint bundle folder (fit.json);
a world capture (``worlds/*.json``) or a scene file works too. Every building gets the assistant's
proposals (``enshrouded_assist.py``): what to improve and where, highlighted on the page.
"""
from __future__ import annotations

import argparse
import base64
import colorsys
import hashlib
import io
import json
import struct
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enshrouded_house as eh  # noqa: E402
import enshrouded_assist as ea  # noqa: E402
from enshrouded_bundle import is_bundle as eb_is_bundle  # noqa: E402

HOUSES = Path(__file__).resolve().parents[1] / "docs" / "enshrouded-houses"
VISUAL = HOUSES / "visual"
TEMPLATE = HOUSES / "viewer" / "template.html"
CDN = "https://cdn.jsdelivr.net/npm/three@0.147.0/"


def shrink_glb(data: bytes, size: int = 256, quality: int = 80) -> bytes:
    """Re-encode the images embedded in a glb at most ``size`` px, repacking the binary chunk."""
    from PIL import Image

    jl = struct.unpack("<I", data[12:16])[0]
    gltf = json.loads(data[20:20 + jl])
    binl = struct.unpack("<I", data[20 + jl:24 + jl])[0]
    blob = data[28 + jl:28 + jl + binl]
    images = {im["bufferView"]: im for im in gltf.get("images", []) if "bufferView" in im}
    out = bytearray()
    for i, view in enumerate(gltf["bufferViews"]):
        start = view.get("byteOffset", 0)
        chunk = blob[start:start + view["byteLength"]]
        if i in images:
            im = Image.open(io.BytesIO(chunk))
            im.thumbnail((size, size))
            buf = io.BytesIO()
            if "A" in im.getbands():
                im.save(buf, "PNG", optimize=True)
                images[i]["mimeType"] = "image/png"
            else:
                im.convert("RGB").save(buf, "JPEG", quality=quality)
                images[i]["mimeType"] = "image/jpeg"
            chunk = buf.getvalue()
        out += b"\0" * (-len(out) % 4)
        view["byteOffset"], view["byteLength"] = len(out), len(chunk)
        out += chunk
    out += b"\0" * (-len(out) % 4)
    gltf["buffers"][0]["byteLength"] = len(out)
    js = json.dumps(gltf, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 28 + len(js) + len(out)
    return (b"glTF" + struct.pack("<II", 2, total) + struct.pack("<I", len(js)) + b"JSON" + js
            + struct.pack("<I", len(out)) + b"BIN\0" + bytes(out))


def material_colour(block: str) -> str:
    """Average colour of the block's albedo texture, or a stable colour from its name."""
    tex = VISUAL / f"{block}_side_albedo.jpg"
    if tex.exists():
        from PIL import Image
        r, g, b = Image.open(tex).convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        return f"#{r:02x}{g:02x}{b:02x}"
    h = int(hashlib.md5(block.encode()).hexdigest()[:4], 16) / 65535
    r, g, b = colorsys.hls_to_rgb(h, 0.55, 0.35)
    return "#%02x%02x%02x" % (int(r * 255), int(g * 255), int(b * 255))


def material_labels() -> dict:
    path = HOUSES / "materials.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = data.get("materials", data) if isinstance(data, dict) else data
    return {r["debug_name"]: r.get("name_ru") or r["debug_name"] for r in rows if isinstance(r, dict) and r.get("debug_name")}


def runs_of(cells, key):
    runs = []
    for c in sorted(cells, key=lambda c: (c[2], c[1], c[0])):
        r = runs[-1] if runs else None
        k = key(c)
        if r and r[1] == c[1] and r[2] == c[2] and r[0] + r[3] == c[0] and (len(r) < 5 or r[4] == k):
            r[3] += 1
        else:
            runs.append([c[0], c[1], c[2], 1] + ([] if k is None else [k]))
    return runs


def auto_levels(house, furniture):
    (_, _, z0), (_, _, z1) = house.bbox()
    floors = sorted({f["at"][2] for f in furniture if not f.get("placed")})
    levels = [[f"Слой {z}", z + 2] for z in floors][:6]
    return levels + [["Всё", z1]]


def design_spec(source: Path) -> dict:
    """A design file as is, or a game blueprint bundle folder wrapped as a one-fragment design."""
    source = Path(source)
    if source.is_file():
        spec = json.loads(source.read_text(encoding="utf-8"))
        if spec.get("schema") == eh.SCHEMA:
            return spec
        # A world capture or a scene: read what is built there and let the assistant check it.
        spec, info = ea.load_source(source)
        return dict(spec, _assist=info,
                    description=f"Снимок постройки из игры ({source.name}): помощник проверил, как в ней живётся")
    # A bundle that carries its editable design (source/*.json) is shown from the design: same blocks,
    # plus its notes, levels and room labels.
    for f in sorted((source / "source").glob("*.json")) if (source / "source").is_dir() else []:
        try:
            spec = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if spec.get("schema") == eh.SCHEMA:
            return dict(spec, name=source.name, _folder=str(source))
    import tempfile
    import enshrouded_bundle as eb
    scene = eb.read_bundle(source)
    entry = json.loads((source / "catalog-entry.json").read_text(encoding="utf-8")) \
        if (source / "catalog-entry.json").exists() else {}
    level = entry.get("altar_level_needed") or scene.get("altar_level")
    if not level:
        size = max((entry.get("dimensions_m") or {}).get("full_copy_envelope_xyz") or [40])
        level = next((lv for lv, cube in sorted(eh.ALTAR_CUBE_M.items()) if size <= cube), max(eh.ALTAR_CUBE_M))
    tmp = Path(tempfile.mkdtemp()) / f"{source.name}.scene.json"
    tmp.write_text(json.dumps(scene), encoding="utf-8")
    terrain = any(str(m).startswith("Terrain_") for m in scene["materials"].values())
    return {"schema": eh.SCHEMA, "name": source.name, "title": entry.get("label") or source.name,
            "description": f"Чертёж игры {source.name}" + (f", автор {entry['author']}" if entry.get("author") else ""),
            "altar": {"at": [0, 0, 0], "level": level}, "ground": not terrain,
            "parts": [{"op": "fragment", "file": str(tmp)}], "_folder": str(source),
            "_assist": {"source": str(source), "kind": "bundle", "terrain": terrain, "altar": True,
                        "level_known": bool(entry.get("altar_level_needed") or scene.get("altar_level"))}}


LAYER_ORDER = ("building", "furniture", "stations")


def layer_rules(folder: Path):
    """Layer selectors of a blueprint's layers.json (custodian.enshrouded.house-layers.v1), or None."""
    path = Path(folder) / "layers.json" if folder else None
    if not path or not path.exists():
        return None
    return {name: (layer.get("prop_selector") or {}) for name, layer in (json.loads(path.read_text(encoding="utf-8"))
                                                                         .get("layers") or {}).items()}


def layer_of(debug_name: str, rules: dict):
    """The one layer a prop belongs to: positive rules match, then ``exclude_layers`` drops overlaps."""
    hit = {n for n, r in rules.items()
           if any(s in debug_name for s in r.get("recipe_contains_any", []))
           or any(debug_name.startswith(s) for s in r.get("recipe_prefix_any", []))
           or debug_name in r.get("include_exact_recipes", [])}
    left = {n for n in hit if not hit & set(rules[n].get("exclude_layers", []))}
    return next((n for n in LAYER_ORDER if n in left), None) if len(left) == 1 else ("building" if not left else None)


def build_data(spec: dict) -> tuple[dict, set]:
    """Viewer data for one design and the names of the asset files it needs."""
    info = spec.get("_assist") or {"source": spec.get("name"), "kind": "design", "terrain": True,
                                    "level_known": True, "altar": True}
    house = ea.prepare(spec, info)
    errors, warnings, advice = ea.check_and_advise(house, info)
    keys = sorted(set(house.voxels.values()))
    index = {k: i for i, k in enumerate(keys)}
    blocks = {k: house.materials.get(k, k) for k in keys}
    readme = json.loads((VISUAL / "README.json").read_text(encoding="utf-8"))
    model_of = {m["item"]: m["file"] for m in readme["models"] if m.get("item")}
    furniture = [f for f in house.furniture() if not f.get("check_only")]   # check-only copies of captured props
    furn = []
    for f in furniture:
        item = {"id": f.get("id"), "prop": f.get("prop"), "item": f.get("item"), "at": f["at"], "size": f["size"],
                "rot": f.get("rot", 0), "group": f.get("group"), "shelter": bool(f.get("needs_shelter")),
                "model": model_of.get(f.get("prop"))}
        if f.get("prop"):
            # The same pivot and turn the bundle export writes, so the page shows what the game builds.
            item.update(pos=f["pos"], rot_xyzw=f["rot_xyzw"])
        furn.append(item)
    openings = [{"t": p["template"], "at": p["at"], "rot": p["rot"]} for p in house.placements
                if p["template"] in ("door_frame_2m", "window_frame_2m", "door_frame_4m", "window_frame_4m")]
    openings += [{"t": "cut_window", "at": p["at"], "size": p["size"]} for p in spec.get("parts", [])
                 if p.get("op") == "cut" and p.get("kind") == "window"]
    (x0, y0, z0), (x1, y1, z1) = house.bbox()
    labels = material_labels()
    counts = Counter(house.voxels.values())
    altar = spec.get("altar") or {}
    data = {
        "name": spec.get("name"), "title": spec.get("title") or spec.get("name"),
        "description": spec.get("description"), "notes": spec.get("notes"),
        "billNote": spec.get("bill_note"),
        "levels": spec.get("levels") or auto_levels(house, furniture),
        "mats": keys, "matNames": blocks,
        "matLabels": {k: labels.get(b, b.replace("Block_", "").replace("_", " ")) for k, b in blocks.items()},
        "matColor": {k: material_colour(b) for k, b in blocks.items()},
        "v": runs_of(house.voxels, lambda c: index[house.voxels[c]]),
        "d": runs_of(house.doors, lambda c: None),
        "furn": furn, "openings": openings,
        "altar": {"box": list(eh.ALTAR_BOX), "above": eh.ALTAR_ABOVE, "clear": eh.ALTAR_CLEARANCE,
                  "level": altar.get("level", 1), "present": bool(altar) and info.get("altar", True),
                  "model": f"flame_altar_t{min(5, max(0, altar.get('level', 1)))}.glb".replace("_t1.", "_t0.")},
        "bbox": [[x0, y0, min(z0, -eh.ALTAR_BOX[2])], [x1, y1, z1]],
        "templates": dict(Counter(p["template"] for p in house.placements)),
        "counts": {k: counts[k] for k in keys}, "total": len(house.voxels),
        "flights": [{"at": list(f["at"]), "d": list(f["d"]), "steps": f["steps"], "note": f["note"]} for f in house.flights],
        "errors": errors, "warnings": warnings,
        "advice": {k: advice[k] for k in ("kind", "mode", "found", "summary", "limits", "proposals")},
    }
    names = set()
    for b in blocks.values():
        for face in ("side", "top"):
            for kind in ("albedo", "normal"):
                if (VISUAL / f"{b}_{face}_{kind}.jpg").exists():
                    names.add(f"{b}_{face}_{kind}.jpg")
    models = {"door_wooden.glb", "window_wooden_frame.glb"} | {f["model"] for f in furn if f["model"]}
    if data["altar"]["present"]:
        models.add(data["altar"]["model"])
    names |= {m for m in models if (VISUAL / m).exists()}
    rules = layer_rules(spec.get("_folder"))
    if rules:
        data["hasLayers"] = True
        for f in furn:
            f["layer"] = layer_of(f.get("prop") or "", rules) or "building"
    data["assets"] = sorted(names)
    data["report"] = {"errors": errors, "warnings": len(warnings), "advice": advice["summary"],
                      "missing_models": sorted({f["prop"] for f in furn if f["prop"] and not f["model"]}),
                      "materials_without_texture": sorted(b for b in blocks.values()
                                                          if not (VISUAL / f"{b}_side_albedo.jpg").exists())}
    return data, names


def asset_bytes(name: str) -> bytes:
    raw = (VISUAL / name).read_bytes()
    return shrink_glb(raw) if name.endswith(".glb") else raw


def embedded(names) -> dict:
    return {n: (base64.b64encode(asset_bytes(n)).decode() if n.endswith(".glb")
                else "data:image/jpeg;base64," + base64.b64encode(asset_bytes(n)).decode()) for n in sorted(names)}


def _page(title: str, head_js: str, three_dir: str | None) -> str:
    page = TEMPLATE.read_text(encoding="utf-8").replace("__TITLE__", title)
    cut = page.index(f'<script src="{CDN}')
    page = page[:cut] + head_js + page[cut:]
    if three_dir is not None:
        for sub in ("build/", "examples/js/controls/", "examples/js/loaders/"):
            page = page.replace(CDN + sub, three_dir)
        page = '<meta charset="utf-8">\n' + page
    return page


def _js(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def build(design: Path, three_dir: str | None = None) -> tuple[str, dict]:
    """One design (file or bundle folder) as a single self-contained page."""
    return build_many([design], three_dir)


def build_many(sources, three_dir: str | None = None) -> tuple[str, dict]:
    """Several designs in one self-contained page with a picker (assets shared, embedded once)."""
    forts, names, report = {}, set(), {}
    for src in sources:
        spec = design_spec(src)
        data, need = build_data(spec)
        forts[spec["name"]] = data
        names |= need
        report[spec["name"]] = data.pop("report")
    order = list(forts)
    first = forts[order[0]]
    head = ("<script>window.ASSETS=" + json.dumps(embedded(names)) + ";</script>\n<script>window.FORTS="
            + _js(forts) + ";window.BUILDINGS=" + _js([{"id": k, "title": forts[k]["title"]} for k in order])
            + ";window.CURRENT=decodeURIComponent(location.hash.slice(1))in window.FORTS?decodeURIComponent(location.hash.slice(1)):"
            + _js(order[0]) + ";window.FORT=window.FORTS[window.CURRENT];"
            + "window.SWITCH=id=>{location.hash=encodeURIComponent(id);location.reload();};"
            + "document.title=window.FORT.title||document.title;</script>\n")
    page = _page(first["title"] or "Постройка", head, three_dir)
    out = dict(report[order[0]]) if len(order) == 1 else {"buildings": report}
    out["mb"] = round(len(page) / 1e6, 1)
    return page, out


def build_site(sources, out_dir: Path, three_dir: str | None = None) -> dict:
    """A folder for a web server: index.html + list.json + buildings/<id>.json + assets/ (files, not inlined).

    The page loads the building named in ?b= (or the first one) and switches by reloading.
    """
    out_dir = Path(out_dir)
    (out_dir / "buildings").mkdir(parents=True, exist_ok=True)
    (out_dir / "assets").mkdir(exist_ok=True)
    listing, report = [], {}
    for src in sources:
        try:
            spec = design_spec(src)
            data, need = build_data(spec)
        except Exception as exc:  # one broken folder must not take the others down
            report[Path(src).name] = {"error": str(exc)}
            continue
        report[spec["name"]] = data.pop("report")
        (out_dir / "buildings" / f"{spec['name']}.json").write_text(_js(data), encoding="utf-8")
        listing.append({"id": spec["name"], "title": data["title"]})
        for n in need:
            target = out_dir / "assets" / n
            if not target.exists():
                target.write_bytes(asset_bytes(n))
    (out_dir / "list.json").write_text(_js(listing), encoding="utf-8")
    # ?b=<id> picks the building; #<id> (links from the old gallery) still works.
    loader = ("<script>(function(){var q=new URLSearchParams(location.search).get('b')"
              "||decodeURIComponent(location.hash.slice(1));"
              "var x=new XMLHttpRequest();x.open('GET','list.json',false);x.send();var L=JSON.parse(x.responseText);"
              "var id=L.some(function(b){return b.id===q})?q:(L[0]||{}).id;"
              "var y=new XMLHttpRequest();y.open('GET','buildings/'+encodeURIComponent(id)+'.json',false);y.send();"
              "window.BUILDINGS=L;window.CURRENT=id;window.FORT=JSON.parse(y.responseText);window.ASSET_BASE='assets/';"
              "window.SWITCH=function(b){location.href=location.pathname+'?b='+encodeURIComponent(b)};"
              "document.title=window.FORT.title||document.title;})();</script>\n")
    (out_dir / "index.html").write_text(_page("Постройки", loader, three_dir), encoding="utf-8")
    return report


def sources_in(folder: Path):
    """Bundle folders (with fit.json) and design files (*.json with the house schema) in a folder."""
    out = []
    for p in sorted(Path(folder).iterdir()):
        if eb_is_bundle(p):
            out.append(p)
        elif p.suffix == ".json" and p.is_file():
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if d.get("schema") in (eh.SCHEMA, eh.SCENE_SCHEMA) or "voxel_runs_x_y_z_len_material" in d:
                    out.append(p)
            except (ValueError, UnicodeDecodeError):
                pass
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("design", type=Path, nargs="+",
                    help="design .json files, blueprint bundle folders, or (with --site) folders holding them")
    ap.add_argument("-o", "--out", type=Path, required=True, help="page .html, or the site folder with --site")
    ap.add_argument("--site", action="store_true",
                    help="write a web-server folder (assets as files, list of buildings) instead of one page")
    ap.add_argument("--three-dir", help="load three.js from this relative folder instead of the CDN (offline test)")
    args = ap.parse_args(argv)
    sources = []
    for d in args.design:
        sources += sources_in(d) if d.is_dir() and not eb_is_bundle(d) else [d]
    if args.site:
        report = build_site(sources, args.out, args.three_dir)
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    page, report = build_many(sources, args.three_dir)
    args.out.write_text(page, encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 1 if report.get("errors") else 0


if __name__ == "__main__":
    sys.exit(main())
