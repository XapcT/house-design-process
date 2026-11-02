"""Building assistant: read a building that stands in the game, check it, propose improvements.

The Custodian looks at what is already built (a World Editor capture of the base around the
altar, a ``worlds/*.json`` capture, a scene file or a design), finds what makes it hard to live
in, and writes a list of proposals with the exact spot of each one. The web viewer shows the list
and highlights each spot in 3D and on the floor plan. Nothing is changed: the assistant only
proposes; the player (or a designer) does the work.

  python scripts/enshrouded_assist.py <capture-or-design> [-o advice.json] [--md advice.md] [--level 2]

Sources:
  * bundle folder (``fit.json`` + ``.sfbp``), or a whole-area capture of an altar
    (``capture.json`` + ``part-*.sfbp`` + ``props.sfbp``): what is built in the world;
  * ``worlds/*.json``: a capture of a real base (voxel runs + props);
  * ``custodian.enshrouded.scene.v1``: an imported piece;
  * ``custodian.enshrouded.house.v1``: a design (checked as it is).

A captured building has no design ops, so the assistant first reads its structure from the game
data: doors from the door props, stations and chests (with their use side) from their props, and
flights of stairs from the voxels. Then it runs the same livability checks as
``enshrouded_house.py check`` and turns each finding into a proposal in Russian.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enshrouded_house as eh  # noqa: E402

ADVICE_SCHEMA = "custodian.enshrouded.advice.v1"
VOXEL_M = eh.VOXEL_M
#: A world capture's origin is the altar box bottom corner; the house frame's is the plate top.
CAPTURE_Z = -4
#: Steps of a flight found in the voxels: at least 3 m of rise, at least 1.5 m wide, under a roof
#: (lower runs are seats, shelves and ramps in real bases more often than stairs).
MIN_STEPS = 6
MIN_STAIR_WIDTH = 3
ROOF_SEARCH = 24
#: Share of a cell a captured prop must cover to count as standing in it (voxels, 10 cm).
SLACK = 0.2
#: Floor props a player cannot walk through, besides stations and chests: beds, tables, chairs,
#: and any other floor prop at least 1 m tall on at least 1 m x 0.5 m (cabinets, shelves, barrels).
BLOCKING_GROUPS = {"bed", "table", "chair"}
OBSTACLE_HEIGHT, OBSTACLE_AREA = 2, 2
DIRS = {(1, 0): "+x", (-1, 0): "-x", (0, 1): "+y", (0, -1): "-y"}
SIDE_RU = {"+x": "восточной", "-x": "западной", "+y": "северной", "-y": "южной"}


# --- reading what is in the game ------------------------------------------------------------

def _material_names():
    path = eh.GEOMETRY_PATH.parent / "materials.json"
    try:
        return {m["debug_name"]: m.get("name_ru") or m["debug_name"]
                for m in json.loads(path.read_text(encoding="utf-8"))["materials"]}
    except (OSError, ValueError, KeyError):
        return {}


def load_source(source, level=None):
    """Any supported source -> (design spec, info). ``info`` says what the data can prove."""
    source = Path(source)
    info = {"source": str(source), "kind": None, "terrain": True, "level_known": True, "altar": True}
    if source.is_dir():
        import enshrouded_bundle as eb
        scene = eb.read_bundle(source)
        entry = {}
        if (source / "catalog-entry.json").exists():
            entry = json.loads((source / "catalog-entry.json").read_text(encoding="utf-8"))
        tmp = Path(tempfile.mkdtemp()) / f"{source.name}.scene.json"
        tmp.write_text(json.dumps(scene), encoding="utf-8")
        info.update(kind="bundle", terrain=any(str(m).startswith("Terrain_") for m in scene["materials"].values()))
        level = level or entry.get("altar_level_needed") or scene.get("altar_level")
        spec = {"schema": eh.SCHEMA, "name": source.name, "title": entry.get("label") or source.name,
                "ground": not info["terrain"], "parts": [{"op": "fragment", "file": str(tmp)}]}
    else:
        data = json.loads(source.read_text(encoding="utf-8"))
        if data.get("schema") == eh.SCHEMA:
            info["kind"] = "design"
            return data, info
        if data.get("schema") == eh.SCENE_SCHEMA:
            info.update(kind="scene", terrain=any(str(m).startswith("Terrain_") for m in data["materials"].values()))
            at = [0, 0, 0]
        elif "voxel_runs_x_y_z_len_material" in data:
            # Captures of real bases hold the built blocks only, not the hill they stand on.
            info.update(kind="capture", terrain=False, altar=bool(data.get("altar_count_in_capture", 1)))
            # Without an altar the capture's origin is its lowest built corner: that is ground level.
            at = [0, 0, CAPTURE_Z if info["altar"] else 0]
        else:
            raise eh.DesignError(f"{source.name}: not a design, scene, world capture or bundle folder")
        spec = {"schema": eh.SCHEMA, "name": source.stem, "title": data.get("base") or data.get("name") or source.stem,
                "ground": not info["terrain"],
                "parts": [{"op": "fragment", "file": str(source.resolve()), "at": at}]}
    if not level:
        info["level_known"] = False
    spec["altar"] = {"at": [0, 0, 0], "level": level or 4}
    return spec, info


def _yaw_of(q):
    """Turn (0/90/180/270) of a captured prop, or None when it is not a quarter turn about up."""
    for rot in (0, 90, 180, 270):
        r = eh._yaw_quat(rot, [0, 0, 0, 1])
        if abs(sum(a * b for a, b in zip(r, q))) > 0.996:   # within about 10 degrees
            return rot
    return None


def dig_basements(house):
    """A capture without terrain: air below altar level over a block of the same column is dug.

    The flat ground stand-in would otherwise fill cellars and lower halls with earth.
    """
    low = {}
    for x, y, z in house.voxels:
        if z < low.get((x, y), 1):
            low[(x, y)] = z
    for (x, y), z0 in low.items():
        for z in range(z0 + 1, 0):
            if (x, y, z) not in house.voxels:
                house.dug.add((x, y, z))


def infer_structure(house, terrain=True):
    """Doors, stations, chests and flights of a captured building, so the checks see them."""
    found = Counter()
    if not terrain:
        dig_basements(house)
    keep, floor_props = [], []
    furniture = list(house.spec.get("furniture") or [])
    for q in house.placed:
        name = q.get("prop") or ""
        try:
            rec = eh.prop(name)
        except eh.DesignError:
            keep.append(q)
            continue
        group = q.get("group") or rec.get("group")
        rot = _yaw_of(q.get("rot_xyzw") or [0, 0, 0, 1])
        if group == "door" and rot is not None and "Secret" not in name:
            m = rec.get("size_m") or {}
            default = {v[1]: v[0] for v in eh.OPENINGS.values()}
            w, h = default.get(name) or (max(2, round(m.get("x", 2) / VOXEL_M)), max(4, round(m.get("y", 2.5) / VOXEL_M)))
            x, y, z = q["pos"]
            along = "x" if rot in (0, 180) else "y"
            at = (round(x - w / 2), math.floor(y), round(z)) if along == "x" else (math.floor(x), round(y - w / 2), round(z))
            size = [w, 1, h] if along == "x" else [1, w, h]
            label = q.get("name_ru") or rec.get("name_ru") or name
            house.openings.append({"at": at, "w": w, "h": h, "wall": along, "inferred": True,
                                   "note": f"{label} {list(at)}"})
            for c in eh._box(list(at), size):
                if c not in house.voxels:
                    house.doors.add(c)
            found["doors"] += 1
            keep.append(q)
            continue
        if rot is not None and eh.mount_of(name) == "floor" and (group in eh.USED_GROUPS or group in BLOCKING_GROUPS
                                                                  or group == "other_placeable"):
            at, size = footprint(q, rec, rot)
            floor_props.append((q, rec, group, rot, at, size))
        keep.append(q)
    # Every floor prop's footprint first, so a stack is found whatever order the capture lists it in.
    def under(at, size):
        cx, cy = at[0] + size[0] // 2, at[1] + size[1] // 2
        return any(o[4][2] < at[2] and o[4][0] <= cx < o[4][0] + o[5][0] and o[4][1] <= cy < o[4][1] + o[5][1]
                   and o[4][2] + o[5][2] >= at[2] for o in floor_props)
    for q, rec, group, rot, at, size in floor_props:
        name = q["prop"]
        label = q.get("name_ru") or rec.get("name_ru") or name
        on_floor = house.solid(at[0] + size[0] // 2, at[1] + size[1] // 2, at[2] - 1)
        if group in eh.USED_GROUPS:
            # Checked from its exact game footprint; the captured prop itself stays for display.
            if not on_floor and under(at, size):
                found["stacked"] += 1
                continue   # a chest on a chest: used from the floor below, not checked on its own
            furniture.append({"id": f"{label} {at[0]},{at[1]}", "item": label, "game_prop": name, "group": group,
                              "at": at, "size": size, "rot": rot, "use": DIRS[eh.FRONT[rot]],
                              "inferred": True, "check_only": True})
            found["stations" if group != "chest" else "chests"] += 1
        elif on_floor and (group in BLOCKING_GROUPS or (size[2] >= OBSTACLE_HEIGHT and size[0] * size[1] >= OBSTACLE_AREA)):
            # Beds, tables, cabinets: nobody walks through them, so doors and routes must go round.
            furniture.append({"id": f"{label} {at[0]},{at[1]}", "item": label, "game_prop": name, "group": group,
                              "at": at, "size": size, "rot": rot, "inferred": True, "check_only": True})
            found["obstacles"] += 1
    house.placed = keep
    house.spec = dict(house.spec, furniture=furniture)
    for f in find_flights(house):
        house.flights.append(f)
        found["flights"] += 1
    house._stand = None
    return dict(found)


def footprint(q, rec, rot):
    """Cells a captured floor prop really covers: its game size about the pivot, minus 10 cm slack.

    Players set props anywhere, not on the 0.5 m grid, and model bounds overhang a little; a cell
    counts only when the prop covers more than ``SLACK`` of it.
    """
    m = rec.get("size_m") or {"x": 1, "y": 1, "z": 1}
    w, d = m["x"] / VOXEL_M, m["z"] / VOXEL_M
    if rot in (90, 270):
        w, d = d, w
    x, y, z = q["pos"]
    lo = lambda c, s: math.floor(c - s / 2 + SLACK)
    hi = lambda c, s: math.ceil(c + s / 2 - SLACK)
    z0 = round(z) if abs(z - round(z)) < 0.1 else math.floor(z)
    h = max(1, math.ceil(m["y"] / VOXEL_M - SLACK))
    ax, ay = lo(x, w), lo(y, d)
    return [ax, ay, z0], [max(1, hi(x, w) - ax), max(1, hi(y, d) - ay), h]


def find_flights(house):
    """Flights of stairs read from the voxels: standing height rising one voxel per cell."""
    stand = house._standing()
    tops = {k: set(v) for k, v in stand.items()}
    lanes = []
    for (dx, dy) in DIRS:
        for (x, y), zs in stand.items():
            for z in zs:
                # (x, y) is the foot cell at height z, the next cell is step 1 (stand z + 1).
                if (z + 1) not in tops.get((x + dx, y + dy), ()) or (z - 1) in tops.get((x - dx, y - dy), ()):
                    continue
                n = 0
                while (z + n + 1) in tops.get((x + dx * (n + 1), y + dy * (n + 1)), ()):
                    n += 1
                if n >= MIN_STEPS:
                    lanes.append(((dx, dy), x + dx, y + dy, z, n))
    flights = []
    used = set()
    lanes.sort(key=lambda t: (t[0], t[3], t[1], t[2]))
    index = {(d, x, y, z): n for d, x, y, z, n in lanes}
    for d, x, y, z, n in lanes:
        if (d, x, y, z) in used:
            continue
        dx, dy = d
        ax, ay = (0, 1) if dx else (1, 0)
        if (d, x - ax, y - ay, z) in index:
            continue   # not the first lane of its flight
        width, steps = 0, n
        while (d, x + ax * width, y + ay * width, z) in index:
            used.add((d, x + ax * width, y + ay * width, z))
            steps = min(steps, index[(d, x + ax * width, y + ay * width, z)])
            width += 1
        if width < MIN_STAIR_WIDTH:
            continue
        cells = [(x + dx * i + ax * k, y + dy * i + ay * k, z + i) for i in range(steps) for k in range(width)]
        roofed = sum(any((cx, cy, cz + h) in house.voxels for h in range(eh.PASSAGE_HEAD, ROOF_SEARCH)) for cx, cy, cz in cells)
        if roofed * 2 < len(cells):
            continue   # a roof slope or a hillside under the sky, not a staircase
        flights.append({"at": (x, y, z), "d": d, "width": width, "steps": steps, "inferred": True,
                        "note": f"лестница {[x, y, z]}"})
    return flights


# --- ideas: what to build next, what to rebuild, what to optimise ------------------------------

#: Stations of one craftsperson further apart than this (voxels, 12 m) or on different storeys are
#: worth gathering into one hall: the player walks between them on every craft.
SPREAD = 24
#: A missing station is worth proposing when the craftsperson has this many recipes on it.
MISSING_RECIPES = 10
#: A facade made of one block for more than this share looks flat (real bases: 60-70 % main stone).
MONO_SHARE = 0.85
SECTION_RU = {"build": "Достроить", "rebuild": "Перестроить", "optimise": "Оптимизировать"}


def _npc_data():
    path = eh.GEOMETRY_PATH.parent / "npc-stations.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [], {}
    names = {w["debug_name"]: w.get("name_ru") or w.get("name_en") or w["debug_name"]
             for w in data.get("station_workshops") or [] if w.get("debug_name")}
    return data.get("npc_workshops") or [], names


def _props_placed(house):
    """Every game prop in the building once: (debug name, group, at, size)."""
    out = []
    for f in house.furniture():
        if f.get("check_only"):
            continue
        name = f.get("prop") or ""
        try:
            group = f.get("group") or eh.prop(name).get("group")
        except eh.DesignError:
            group = None
        out.append((name, group, f["at"], f.get("size", [1, 1, 1])))
    return out


def ideas(house, info):
    """Proposals beyond the rules: rooms and stations to add, halls to regroup, room to grow."""
    items = []
    props = _props_placed(house)
    names = {n for n, *_ in props}
    npcs, station_ru = _npc_data()
    def label(n):
        return station_ru.get(n) or (eh.prop(n).get("name_ru") if _known(n) else None) or n

    # Craftspeople: present when their own tool station stands in the base.
    present = []
    for w in npcs:
        own = [st for st in w.get("stations_used") or {} if st.startswith("Prop_NPC_")]
        if own and any(st in names for st in own):
            present.append(w)
    for w in present:
        missing = [(st, n) for st, n in sorted(w["stations_used"].items(), key=lambda kv: -kv[1])
                   if n >= MISSING_RECIPES and st not in names]
        for st, n in missing:
            at = next((a for nm, _, a, _ in props if nm in w["stations_used"]), None)
            items.append(_idea("build", "missing_station", f"Поставить: {label(st)}",
                               f"Для ремесленника {w['name_ru']} нет станка «{label(st)}»: на нём {n} его рецептов. "
                               f"Поставьте его в зал к остальным его станкам.", at))
        # Each station the craftsperson really uses, nearest copy to their own tool station: shared
        # stations (the forge serves three crafts) count where they are closest.
        own = [(a, sz) for nm, _, a, sz in props if nm.startswith("Prop_NPC_") and nm in w["stations_used"]]
        if not own:
            continue
        ax, ay, az = own[0][0][0] + own[0][1][0] / 2, own[0][0][1] + own[0][1][1] / 2, own[0][0][2]
        far = []
        for st, n in w["stations_used"].items():
            if n < MISSING_RECIPES or st.startswith("Prop_NPC_"):
                continue
            copies = [(a, sz) for nm, _, a, sz in props if nm == st]
            if not copies:
                continue
            a, sz = min(copies, key=lambda c: abs(c[0][0] + c[1][0] / 2 - ax) + abs(c[0][1] + c[1][1] / 2 - ay)
                        + 4 * abs(c[0][2] - az))
            d = max(abs(a[0] + sz[0] / 2 - ax), abs(a[1] + sz[1] / 2 - ay))
            if d > SPREAD or abs(a[2] - az) > 3:
                far.append((st, a, "на другом этаже" if abs(a[2] - az) > 3 else f"в {d * VOXEL_M:.0f} м"))
        for st, a, where in far:
            items.append(_idea("rebuild", "spread_stations", f"Перенести ближе: {label(st)}",
                               f"«{label(st)}» стоит {where} от инструментов ремесленника {w['name_ru']}, а он делает "
                               f"на нём {w['stations_used'][st]} рецептов. Перенесите его в зал ремесленника: один зал "
                               f"около 8 × 8 м, не больше 3 больших станков на 7 м.",
                               list(a), cells=[list(a), [round(ax), round(ay), az]]))

    # Beds: every craftsperson needs a sheltered one.
    beds = [(a, s) for _, g, a, s in props if g == "bed"]
    sheltered = []
    for a, s in beds:
        cells = list(eh._box(a, s))
        bare = [c for c in cells if c[2] == a[2] and not house.covered(*c)]
        # Below altar level a capture without terrain cannot tell a cellar wall from open air.
        guess = not info.get("terrain") and a[2] <= 0
        if bare or (not guess and house.open_to_outside(cells)):
            items.append(_idea("rebuild", "bed_shelter", "Кровать без укрытия",
                               "Похоже, над кроватью нет крыши или комната открыта наружу на уровне пола: тогда "
                               "ремесленник на ней не получит укрытие. Закройте комнату стенами, дверью и крышей.", list(a)))
        else:
            sheltered.append(a)
    if present and len(sheltered) < len(present):
        need = len(present) - len(sheltered)
        who = ", ".join(w["name_ru"] for w in present)
        items.append(_idea("build", "beds", f"Достроить спальни: ещё {need}",
                           f"В базе работают ремесленники ({who}), а кроватей под крышей {len(sheltered)}. "
                           f"Каждому нужна своя закрытая комната с кроватью; NPC без кровати занимает свободную в 10 м. "
                           f"Спальня 3 × 4 м: изголовье к наружной стене, тумбочка у изголовья, сундук у двери.", None))

    # Storage: magic chests feed every station in the base.
    stations = [p for p in props if p[1] in ("crafting_station", "workbench")]
    chests = [p for p in props if p[1] == "chest"]
    if len(stations) >= 6 and len(chests) < 2:
        at = list(stations[0][2])
        items.append(_idea("build", "storage", "Достроить склад",
                           f"Станков {len(stations)}, сундуков {len(chests)}. Магические сундуки кормят все станки базы: "
                           f"выделите зал под склад рядом со входом и поставьте туда сундуки в ряд с проходом 2 м.", at))

    # Looks: one block everywhere.
    built = Counter(house.materials.get(m, m) for m in house.voxels.values())
    built = Counter({k: v for k, v in built.items() if not str(k).startswith("Terrain_")})
    total = sum(built.values())
    if total > 2000:
        top, n = built.most_common(1)[0]
        if n / total > MONO_SHARE:
            mats = _material_names()
            items.append(_idea("optimise", "materials", "Разнообразить материалы",
                               f"«{mats.get(top, top)}» — {n * 100 // total} % блоков. В лучших базах основной камень "
                               f"занимает 60–70 %: добавьте тёмный цоколь, светлые рёбра и карнизы, полированный пол "
                               f"и один материал кровли; узоры из светящихся блоков на стенах.", None))
        if not any("glow" in str(k).lower() or "illumin" in str(k).lower() for k in built):
            items.append(_idea("optimise", "glow", "Добавить светящиеся узоры",
                               "В постройке нет светящихся блоков. Руны и узоры из них на стенах (не точки) "
                               "оживляют фасад и подсвечивают входы.", None))

    # Room to grow inside the altar cube.
    if info.get("altar", True) and house.voxels:
        (x0, y0, z0), (x1, y1, z1) = house.bbox()
        level = (house.spec.get("altar") or {}).get("level", 1)
        if not info.get("level_known"):
            # Unknown level: the smallest altar whose cube holds what is built.
            span = max(x1 + 1 - eh.ALTAR_BOX[0] / 2, eh.ALTAR_BOX[0] / 2 - x0, y1 + 1 - eh.ALTAR_BOX[1] / 2,
                       eh.ALTAR_BOX[1] / 2 - y0, z1 + 1) * 2 * VOXEL_M
            level = next((lv for lv, m in sorted(eh.ALTAR_CUBE_M.items()) if m >= span), max(eh.ALTAR_CUBE_M))
        half = eh.ALTAR_CUBE_M[level] / VOXEL_M / 2
        cx, cy = eh.ALTAR_BOX[0] / 2, eh.ALTAR_BOX[1] / 2
        free = {"на восток": cx + half - 1 - x1, "на запад": x0 - (cx - half), "на север": cy + half - 1 - y1,
                "на юг": y0 - (cy - half), "вверх": half - 1 - z1}
        room = [(k, v * VOXEL_M) for k, v in free.items() if v * VOXEL_M >= 8]
        if room:
            best = max(room, key=lambda kv: kv[1])
            lvl = f"уровень алтаря {level}" if info.get("level_known") else f"если алтарь {level} уровня"
            items.append(_idea("build", "room_to_grow", "Есть место достроить",
                               f"Внутри куба алтаря ({lvl}, {eh.ALTAR_CUBE_M[level]} м) свободно: "
                               + ", ".join(f"{k} {v:.0f} м" for k, v in room)
                               + f". Больше всего — {best[0]}: туда удобно вынести новый зал, сад или двор.", None))
    return items


def _known(name):
    try:
        eh.prop(name)
        return True
    except eh.DesignError:
        return False


def _idea(section, kind, title, advice, at, cells=None):
    return {"kind": kind, "severity": "idea", "section": section, "title": title, "advice": advice,
            "subject": SECTION_RU[section], "at": list(at) if at else None,
            "cells": cells or ([list(at)] if at else []), "detail": ""}


# --- findings -> proposals ------------------------------------------------------------------

TRIPLE = re.compile(r"[\[(](-?\d+), (-?\d+), (-?\d+)[\])]")

#: (pattern, kind, title, what to do). ``{what}`` is the thing in the way, ``{side}`` a side.
RULES = [
    (r"free floor on its (\S+) side \(block at", "door_block", "Дверь упирается в блоки",
     "С {side} стороны двери меньше 3 м свободного пола: в проходе {what}. Уберите эти блоки "
     "(колонна, стена, ступень) или перенесите дверь, чтобы створка открывалась на 3 м пустого пола."),
    (r"free floor on its (\S+) side \(furniture at", "door_furniture", "Мебель перед дверью",
     "С {side} стороны двери стоит {what}. Отодвиньте её хотя бы на 3 м от проёма: открытая створка "
     "и проход должны быть свободны."),
    (r"free floor on its (\S+) side \(no floor at", "door_drop", "За дверью нет пола",
     "С {side} стороны двери нет пола (обрыв или яма). Положите пол на 3 м перед дверью или "
     "закройте проём."),
    (r"open doors hit each other", "door_swing", "Двери бьются друг о друга",
     "Две двери так близко, что открытые створки задевают друг друга. Разнесите их минимум на 2 м."),
    (r"landing at the top", "stair_top", "Лестница упирается наверху",
     "Над последней ступенью меньше 2 м ровной площадки: {what}. Освободите площадку или "
     "продлите пол, чтобы лестница вела в комнату, а не в стену."),
    (r"landing at the bottom", "stair_foot", "Тесно у подножия лестницы",
     "У нижней ступени меньше 3 м свободного пола: {what}. Уберите мебель или стену от подножия, "
     "чтобы можно было подойти и развернуться."),
    (r"step \d+ is missing", "stair_step", "Не хватает ступени",
     "Срез пола съел ступень. Верните её: последняя ступень — часть верхнего пола."),
    (r"headroom over step", "stair_head", "Низко над лестницей",
     "Над ступенями меньше 2 м: игрок бьётся головой. Поднимите потолок или прорежьте проём над лестницей."),
    (r"hole\(s\) in floors", "hole", "Дыра в полу",
     "В полу или проходе нет блока. Заделайте дыру тем же материалом, что и пол вокруг."),
    (r"floating piece", "floating", "Висит в воздухе",
     "Этот кусок не касается ни постройки, ни земли. Подставьте опору или соедините его с соседними блоками."),
    (r"route from the entrance reaches", "route", "Не пройти",
     "Сюда не ведёт проход шириной 2 м и высотой 2,5 м от входа или алтаря. Расширьте коридор, "
     "уберите мебель из прохода или добавьте дверь."),
    (r"clear floor on its", "use", "Станок не подойти",
     "Перед {what} меньше 1 м свободного пола со стороны, с которой им пользуются. Отодвиньте его от "
     "стены или соседей, или разверните."),
    (r"overlaps blocks", "overlap_block", "Предмет в стене",
     "{what} стоит в блоках. Сдвиньте его на свободный пол."),
    (r": overlaps ", "overlap", "Предметы наложены",
     "{what} перекрывает другой предмет. Разнесите их."),
    (r"not fully standing on blocks", "prop_edge", "Предмет на краю",
     "{what} стоит на краю: часть его опоры висит над пустотой. Подвиньте его на сплошной пол."),
    (r"hangs in the air", "prop_floating", "Предмет висит",
     "Под {what} нет пола. Поставьте его на блок или землю."),
    (r"wall prop is not on a wall", "prop_wall", "Настенный предмет не на стене",
     "{what} висит не на стене. Повесьте его спиной к стене."),
    (r"ceiling prop has no ceiling", "prop_ceiling", "Нет потолка для подвеса",
     "Над {what} нет потолка в пределах 8 м. Подвесьте его под потолок."),
    (r"open sky above", "shelter_roof", "Нет крыши",
     "Над {what} открытое небо: для отдыха и ремесленников нужна крыша."),
    (r"room is open to outside", "shelter_open", "Комната не закрыта",
     "Комната с {what} открыта наружу: в стене дыра или проём без двери. Закройте её, иначе укрытие не засчитается."),
    (r"space over the altar is not free", "altar_space", "Над алтарём занято",
     "Над плитой алтаря стоит блок. Освободите место над алтарём."),
    (r"free around the altar", "altar_ring", "Тесно у алтаря",
     "Вокруг алтаря меньше 1,5 м свободного места. Уберите блоки рядом с плитой."),
    (r"hole at the altar", "altar_hole", "Дыра у алтаря",
     "Под плитой алтаря и в 1,5 м вокруг нет пола. Положите блоки пола вровень с плитой."),
    (r"no walkable route .* to the altar", "altar_route", "К алтарю не подойти",
     "К алтарю нет прохода шириной 1 м. Откройте путь к нему."),
    (r"outside the level \d altar cube", "altar_cube", "Выходит за куб алтаря",
     "Часть постройки за пределами зоны алтаря: в игре её не построить. Уменьшите постройку или поднимите уровень алтаря."),
    (r"ground is off", "ground", "Нет земли под постройкой",
     "Чертёж не пишет землю: на неровном месте пол повиснет. Включите ground."),
    (r"headroom at", "headroom", "Низкий потолок",
     "Над {what} меньше 2 м."),
    (r"below \d", "door_low", "Низкая дверь",
     "Проём ниже 2 м."),
]
#: Findings a capture without terrain cannot prove: the real hill or floor below is not in it.
NEEDS_TERRAIN = {"floating", "altar_hole", "ground", "hole"}
SEVERITY_ORDER = {"error": 0, "unsure": 1, "warning": 2, "idea": 3}


def _what_at(house, cell, names):
    """Name of the block or furniture in a cell, for the proposal text."""
    if cell is None:
        return "препятствие"
    c = tuple(cell)
    for f in house.furniture():
        if f.get("placed"):
            continue
        box = set(eh._box(f["at"], f.get("size", [1, 1, 1])))
        if any((c[0], c[1], c[2] + k) in box for k in range(eh.PASSAGE_HEAD)):
            return f"«{f.get('item') or f.get('prop')}»"
    for k in range(eh.PASSAGE_HEAD):
        m = house.voxels.get((c[0], c[1], c[2] + k))
        if m:
            block = house.materials.get(m, m)
            return f"блок «{names.get(block, block)}»"
    return "препятствие"


def _subject(message):
    """The thing a finding is about: the text before the first colon (a station, a door, a flight)."""
    head = message.split(": ", 1)[0]
    return re.sub(r"\s*-?\d+,-?\d+$", "", head).strip()


def proposals(house, errors, warnings, info):
    names = _material_names()
    captured = {f["id"]: f for f in house.furniture() if f.get("check_only")}
    out = []
    for severity, items in (("error", errors), ("warning", warnings)):
        for msg in items:
            if msg.startswith("..."):
                continue
            kind, title, advice, m = "other", "Проверить", msg, None
            for pattern, k, t, a in RULES:
                m = re.search(pattern, msg)
                if m:
                    kind, title, advice = k, t, a
                    break
            cells = [[int(a), int(b), int(c)] for a, b, c in TRIPLE.findall(msg)]
            focus = cells[-1] if cells else None
            sev = severity
            item_of = captured.get(msg.split(": ", 1)[0])
            if kind == "ground" and info.get("kind") != "design":
                continue   # a design rule: a capture stands on the world's own ground
            if (kind.startswith("altar") or msg.startswith("no altar")) and not info.get("altar"):
                continue   # the capture holds no altar, so there is nothing to measure from
            if item_of and kind in ("overlap", "overlap_block"):
                # In game a prop may sink into a wall a little; report only when it is mostly inside.
                cells = list(eh._box(item_of["at"], item_of["size"]))
                inside = sum(c in house.voxels for c in cells)
                if kind == "overlap" or inside * 3 < len(cells):
                    continue
                sev = "warning"
            if kind in NEEDS_TERRAIN and not info.get("terrain"):
                sev = "unsure"
            if not info.get("terrain") and info.get("kind") != "design" and not kind.startswith("altar"):
                if (focus and focus[2] <= 0) or (item_of and item_of["at"][2] <= 0):
                    sev = "unsure"   # at or below altar level the real ground is not in the capture
            if not info.get("altar") and kind in ("route", "use", "door_drop", "prop_floating", "prop_edge"):
                sev = "unsure"       # no altar: where the ground is under this capture is a guess
            if kind.startswith("stair_") and info.get("kind") != "design" and sev == "error":
                sev = "unsure"       # the flight itself was read from the blocks, not from a design
            if kind == "altar_cube" and not info.get("level_known"):
                sev = "unsure"
            side = SIDE_RU.get(m.group(1), "") if m and m.groups() else ""
            subject = _subject(msg)
            if kind in ("door_block", "door_furniture", "stair_top", "stair_foot"):
                what = _what_at(house, focus, names)
            elif kind.startswith(("door_", "stair_")) or kind in ("hole", "floating", "route") or kind.startswith("altar"):
                what = subject
            else:
                what = f"«{subject}»"
            item = {"kind": kind, "severity": sev, "title": title,
                    "advice": advice.format(what=what, side=side), "subject": subject,
                    "at": focus, "cells": cells, "detail": msg}
            if kind == "floating" and focus:
                item["box"] = _component_box(house, tuple(focus))
            if kind == "hole" and focus:
                n = re.match(r"(\d+) hole", msg)
                item["count"] = int(n.group(1)) if n else 1
            out.append(item)
    out = merge_nearby(out)
    out.sort(key=lambda p: (SEVERITY_ORDER[p["severity"]], p["kind"]))
    for i, p in enumerate(out, 1):
        p["n"] = i
    return out


#: Findings of one kind this close (voxels across, storeys up) are one place to fix.
MERGE_XY, MERGE_Z = 10, 3


def merge_nearby(items):
    """One proposal per place: a basement with ten unreachable chests is one fix, not ten."""
    out = []
    for p in items:
        if p["at"] and p["kind"] in ("route", "use", "stair_foot", "stair_top", "door_block", "door_drop",
                                       "door_furniture", "overlap_block", "prop_floating", "headroom"):
            for q in out:
                if (q["kind"] == p["kind"] and q["severity"] == p["severity"] and q["at"]
                        and max(abs(a - b) for a, b in zip(q["at"][:2], p["at"][:2])) <= MERGE_XY
                        and abs(q["at"][2] - p["at"][2]) <= MERGE_Z):
                    q["count"] = q.get("count", 1) + 1
                    q["cells"].append(p["at"])
                    if p["subject"] not in q["subjects"]:
                        q["subjects"].append(p["subject"])
                    q["detail"] += "\n" + p["detail"]
                    break
            else:
                out.append(dict(p, subjects=[p["subject"]]))
                continue
        else:
            out.append(dict(p, subjects=[p["subject"]]))
    for q in out:
        if q.get("count", 1) > 1 and q["kind"] in ("route", "use", "overlap_block", "prop_floating"):
            q["advice"] += f" Таких мест рядом: {q['count']} ({', '.join(q['subjects'][:4])}" + \
                           (", …" if len(q["subjects"]) > 4 else "") + ")."
    return out


def _component_box(house, start):
    seen, stack = {start}, [start]
    while stack and len(seen) < 20000:
        x, y, z = stack.pop()
        for i, j, k in eh.NEAR:
            n = (x + i, y + j, z + k)
            if n in house.voxels and n not in seen:
                seen.add(n)
                stack.append(n)
    xs, ys, zs = zip(*seen)
    return [[min(xs), min(ys), min(zs)], [max(xs) - min(xs) + 1, max(ys) - min(ys) + 1, max(zs) - min(zs) + 1]]


def analyze_house(house, info):
    """Check a ready House and return the advice report (structure must already be inferred)."""
    return check_and_advise(house, info)[2]


def prepare(spec, info):
    """House for a spec from ``load_source``, with a capture's doors, stations and stairs read in."""
    house = eh.House(spec)
    if info.get("kind") not in (None, "design"):
        info["inferred"] = infer_structure(house, info.get("terrain", True))
    return house


def check_and_advise(house, info):
    """(errors, warnings, advice report) of one check run."""
    errors, warnings = house.check()
    items = proposals(house, errors, warnings, info)
    items += [dict(p, subjects=[p["subject"]]) for p in ideas(house, info)]
    for i, p in enumerate(items, 1):
        p["n"] = i
    counts = Counter(p["severity"] for p in items)
    limits = []
    if not info.get("terrain"):
        limits.append("В снимке нет рельефа: «висит в воздухе» и «дыра у алтаря» отмечены как «проверить», "
                      "в игре их может держать склон. Ходы проверены по ровной земле на уровне алтаря.")
    if not info.get("altar"):
        limits.append("В снимке нет алтаря: проверки алтаря пропущены, уровень земли взят по самому нижнему блоку.")
    if not info.get("level_known"):
        limits.append("Уровень алтаря в снимке не записан: размер проверен по кубу 4 уровня (160 м).")
    if info.get("inferred", {}).get("flights") is not None:
        limits.append("Лестницы найдены по блокам под крышей; уличные лестницы и пандусы не проверены.")
    return errors, warnings, {
        "schema": ADVICE_SCHEMA, "name": house.spec.get("name"), "title": house.spec.get("title"),
        "source": info.get("source"), "kind": info.get("kind"),
        "mode": "suggest", "changes_world": False,
        "found": info.get("inferred") or {}, "blocks": len(house.voxels),
        "summary": {"errors": counts["error"], "unsure": counts["unsure"], "warnings": counts["warning"],
                    "ideas": counts["idea"]},
        "limits": limits, "proposals": items,
    }


def analyze(source, level=None):
    """Source path -> (House, advice report)."""
    spec, info = load_source(source, level)
    house = prepare(spec, info)
    return house, analyze_house(house, info)


def markdown(report):
    s = report["summary"]
    lines = [f"# Помощник: {report.get('title') or report['name']}", "",
             f"Источник: `{report['source']}` ({report['kind']}), блоков {report['blocks']}.",
             f"Найдено: исправить {s['errors']}, проверить {s['unsure']}, мелочи {s['warnings']}, "
             f"идеи {s.get('ideas', 0)}. "
             "Помощник ничего не меняет в мире, только предлагает.", ""]
    if report["found"]:
        f = report["found"]
        lines.append(f"Прочитано из игры: дверей {f.get('doors', 0)}, станков {f.get('stations', 0)}, "
                     f"сундуков {f.get('chests', 0)}, лестниц {f.get('flights', 0)}.")
        lines.append("")
    for lim in report["limits"]:
        lines.append(f"> {lim}")
    if report["limits"]:
        lines.append("")
    label = {"error": "исправить", "unsure": "проверить", "warning": "мелочь"}
    fixes = [p for p in report["proposals"] if p["severity"] != "idea"]
    lines.append("## Что мешает жить")
    lines.append("")
    for p in fixes:
        at = f" — клетка {p['at']} (высота {p['at'][2] * VOXEL_M:+.1f} м)" if p["at"] else ""
        lines.append(f"{p['n']}. **{p['title']}** [{label[p['severity']]}]{at}")
        lines.append(f"   {p['advice']}")
    if not fixes:
        lines.append("Замечаний нет: постройка проходит все проверки жизнеспособности.")
    for section, title in SECTION_RU.items():
        group = [p for p in report["proposals"] if p.get("section") == section]
        if not group:
            continue
        lines += ["", f"## {title}", ""]
        for p in group:
            at = f" — около клетки {p['at']}" if p["at"] else ""
            lines.append(f"{p['n']}. **{p['title']}**{at}")
            lines.append(f"   {p['advice']}")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("source", type=Path, help="bundle folder, worlds/*.json capture, scene or design")
    ap.add_argument("-o", "--out", type=Path, help="write the advice JSON here")
    ap.add_argument("--md", type=Path, help="write the proposals as Markdown here")
    ap.add_argument("--level", type=int, choices=sorted(eh.ALTAR_CUBE_M), help="altar level, if the capture lacks it")
    args = ap.parse_args(argv)
    try:
        _, report = analyze(args.source, args.level)
    except (eh.DesignError, KeyError, ValueError, OSError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    if args.out:
        args.out.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    text = markdown(report)
    if args.md:
        args.md.write_text(text, encoding="utf-8")
    if not args.out and not args.md:
        sys.stdout.write(text)
    return 1 if report["summary"]["errors"] else 0


if __name__ == "__main__":
    sys.exit(main())
