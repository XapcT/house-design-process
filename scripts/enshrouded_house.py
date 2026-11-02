"""Expand and check an Enshrouded house design before anyone builds it in game.

The design is a small JSON file (``custodian.enshrouded.house.v1``) made of voxel
operations on the game's 0.5 m building grid. This tool turns it into an exact
voxel list, counts blocks per material and checks the rules the game enforces
on beds and survivors: roof over every sheltered cell, closed walls at bed level,
everything inside the Flame Altar cube. See docs/enshrouded-house-design.md.

Examples:
  python scripts/enshrouded_house.py check docs/enshrouded-houses/starter-cottage.json
  python scripts/enshrouded_house.py plan docs/enshrouded-houses/starter-cottage.json
  python scripts/enshrouded_house.py export docs/enshrouded-houses/starter-cottage.json -o voxels.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter
from pathlib import Path

SCHEMA = "custodian.enshrouded.house.v1"
VOXEL_SCHEMA = "custodian.enshrouded.voxels.v1"
VOXEL_M = 0.5
#: Edge of the Flame Altar building cube per altar level, metres (enshrouded.wiki, build 1226065).
ALTAR_CUBE_M = {1: 40, 2: 80, 3: 120, 4: 160}
#: The Flame Altar is the fixed reference of every design. The model
#: (docs/enshrouded-houses/visual/flame_altar_t*.glb) is a flat stone platform 4 x 4 x 2 m
#: (Prop_Building_T0_FlameAltar_Small_Build_Base placementAABB) with a bowl on top, 0.86 m
#: high at level 0 and 1.27 m at level 5. House rule: the platform top is flush with the
#: floor, so the platform is sunk 2 m. The design origin (0,0,0) is the south-west corner of
#: the platform top, on the 0.5 m grid: the platform spans z -4..-1, the floor is z = -1 and
#: players stand at z = 0. The floor runs on UNDER the platform, as in every real base (the
#: Mayor Manor fills z -2..-1 there, Everspire down to z -5): a pit carved for the platform
#: showed as a hole in game (2026-10-31). Only the space above the floor stays free, for the
#: bowl. The altar's own world point (model pivot) is 1.75 m east, 1.75 m north and 1 m down
#: from that corner.
ALTAR_BOX = (8, 8, 4)
#: Kept free above the platform top: the bowl plus standing room (2 m).
ALTAR_ABOVE = 4
ALTAR_POINT_OFFSET_M = (1.75, 1.75, -1.0)
#: House rule for convenient access: free margin around the box and a walkable route
#: from outside the design that is at least ``ALTAR_ROUTE_WIDTH`` voxels wide.
ALTAR_CLEARANCE = 3
ALTAR_ROUTE_WIDTH = 2
#: Workbench output for the starter blocks: (raw item, raw count, blocks produced).
RAW_COST = {
    "Block_T1_Stone_Rough_CRAFTING": ("Stone", 2, 100),
    "Block_T1_Wood_Rough_CRAFTING": ("Wood Log", 2, 100),
    "Block_Roof_T1_PlantFibre": ("Plant Fiber", 5, 50),
}
#: Exact Construction Hammer template shapes extracted read-only from the game client.
GEOMETRY_PATH = Path(__file__).resolve().parents[1] / "docs" / "enshrouded-houses" / "enshrouded-geometry-templates.json"
#: Short names for the templates used most; any game ``debug_name`` from the geometry file works too.
TEMPLATES = {
    "foundation_2m": "Blueprint_Voxel_Block_Foundation_2m",
    "foundation_4m": "Blueprint_Voxel_Block_Foundation_4m",
    "ceiling_2m": "Blueprint_Voxel_Block_Ceiling_2m",
    "ceiling_4m": "Blueprint_Voxel_Block_Ceiling_4m",
    "wall_2m": "Blueprint_Voxel_Block_Wall_Straight_2m",
    "narrow_wall_2m": "Blueprint_Voxel_Block_Wall_Straight_2x4m",
    "wide_wall_4m": "Blueprint_Voxel_Block_Wall_Straight_4m",
    "door_frame_2m": "Blueprint_Voxel_Block_Wall_Door_2m",
    "door_frame_4m": "Blueprint_Voxel_Block_Wall_Door_4m",
    "window_frame_2m": "Blueprint_Voxel_Block_Wall_Window_2x4m",
    "window_frame_4m": "Blueprint_Voxel_Block_Wall_Window_4m",
    "door_beam_4m": "Blueprint_Voxel_Block_Wall_Door_4m_large_beam",
    "column_2m": "Blueprint_Voxel_Block_Column_2m",
    "column_4m": "Blueprint_Voxel_Block_Column_4m",
    "stepped_wall_2m": "Blueprint_Voxel_Block_Wall_Roof_2m",
    "stepped_wall_4m": "Blueprint_Voxel_Block_Wall_Roof_4m",
    "roof_side_4m": "Blueprint_Voxel_Roof_Straight_4m",
    "roof_ridge_4m": "Blueprint_Voxel_Roof_Top_Straight_4m",
    "stair_4m": "Blueprint_Voxel_Block_Stair_Straight_4m",
}
_SHAPES: dict[str, dict] = {}


def shape(name):
    """Template record in house axes: ``cells`` as (along, across, up), ``size`` and ``empty`` cells."""
    debug = TEMPLATES.get(name, name)
    if debug not in _SHAPES:
        if not _SHAPES:
            data = json.loads(GEOMETRY_PATH.read_text(encoding="utf-8"))
            for t in data["templates"]:
                gx, gy, gz = t["grid"]
                cells = {(x, z, y) for x, y, z in t["filled"]}  # source is X width, Y up, Z depth
                _SHAPES[t["debug_name"]] = {
                    "size": (gx, gz, gy), "cells": cells,
                    "empty": {c for c in _box([0, 0, 0], [gx, gz, gy])} - cells,
                    "name_ru": t.get("name_ru"), "debug": t["debug_name"],
                }
        if debug not in _SHAPES:
            raise DesignError(f"unknown template {name!r}; short names: {', '.join(TEMPLATES)}")
    return _SHAPES[debug]


#: Placeable objects from the same extraction, metres (width, depth, height) and the hole they need.
OBJECTS = {
    "door_single": {"size_m": (2.1, 0.3, 2.55), "hole": (4, 5), "fits": "door_frame_2m, door_frame_4m"},
    "door_double": {"size_m": (4.0, 0.4, 3.5), "hole": (8, 7), "fits": "cut 4 x 3.5 m, door_beam_4m on top"},
    "door_round": {"size_m": (3.1, 0.3, 3.0), "hole": (6, 6), "fits": "cut 3 x 3 m"},
    "window_1x1_5": {"size_m": (1.0, 0.2, 1.5), "hole": (2, 3), "fits": "window_frame_2m, window_frame_4m"},
    "bed": {"size_m": (1.5, 3.0, 1.0), "hole": None, "fits": "most beds 1.0-1.65 x 2.8-3.1 m"},
    "bed_kingsize": {"size_m": (2.6, 3.0, 1.75), "hole": None, "fits": ""},
}
#: Standard door object opening, 2 m wide and 2.5 m high (community measurement).
DOOR = (4, 5)
#: Player clearance in voxels (about 2 m); doors lower than this are reported.
HEADROOM = 4
#: Flat landing a flight of stairs needs at its top and bottom, voxels deep (2 m): the player
#: must be able to step off and turn without hitting a wall, a parapet or a drop.
STAIR_LANDING = 4       # 2 m of free floor past the top step
STAIR_FOOT = 6          # 3 m straight on past the bottom step: in game 2 m and a chest felt like a wall
#: Free strip in front of a crafting station or chest so the player can stand and use it (1 m).
USE_CLEARANCE = 2
#: Prop groups that are used from one side and need ``use`` and clear floor in front.
USED_GROUPS = {"crafting_station", "workbench", "chest", "fireplace"}
#: Free floor in front of every door, on both sides, across the whole hole (3 m): the door leaf
#: swings about 2 m and the player still needs to pass it. The Mayor Manor (the reference build)
#: keeps 4 m or more in front of every single door.
DOOR_CLEAR = 6
#: Part of ``DOOR_CLEAR`` the open leaf sweeps (2 m); two doors whose sweeps overlap hit each other.
DOOR_SWING = 4
#: Body used to prove the main routes are comfortable: 2 m wide, 2.5 m high. Every door, stair
#: landing, station and chest must be reachable from the entrance or the altar with it.
PASSAGE = 4
PASSAGE_HEAD = 5
#: Walls, rows of a direction vector, and the side the back of a prop faces for ``rot``.
FRONT = {0: (0, 1), 90: (-1, 0), 180: (0, -1), 270: (1, 0)}
#: Terrain written under the build when ``ground`` is on: Springlands dirt, full density.
GROUND_MATERIAL = "Terrain_1"
#: How deep the written ground goes below the floor (z = -1) unless ``ground.depth`` says otherwise.
GROUND_DEPTH = 8
_PROPS: dict[str, dict] = {}
_LIT: dict[str, list] = {}
#: Openings the ``door`` and ``window`` ops cut (width, height in voxels) and the prop they hang.
OPENINGS = {
    "door": ((4, 5), "Prop_Decoration_T2_Door_Ornamented_Wooden"),
    "double_door": ((7, 7), "Prop_Decoration_T2_Door_Ornamented_Wooden_Double"),
    "window": ((2, 3), "Prop_Decoration_T2_Window_Ornamented_Wooden_Frame"),
}


def lit_uuid(debug_name):
    """Template UUID that places a lamp switched on (lit-lamps.json), or None."""
    if not _LIT:
        path = GEOMETRY_PATH.parent / "lit-lamps.json"
        if path.exists():
            _LIT.update({k: v["template_uuid"] for k, v in json.loads(path.read_text(encoding="utf-8"))["props"].items()})
    return _LIT.get(debug_name)


def prop(debug_name):
    """Game prop record (size in metres: x width, y height, z depth) from the geometry file."""
    if not _PROPS:
        # The full prop catalogue (every placeable item) first, then the measured geometry on top.
        catalog = GEOMETRY_PATH.parent / "prop-catalog.json"
        if catalog.exists():
            for p in json.loads(catalog.read_text(encoding="utf-8"))["props"]:
                _PROPS[p["debug_name"]] = p
        for p in json.loads(GEOMETRY_PATH.read_text(encoding="utf-8"))["props"]:
            _PROPS[p["debug_name"]] = p
    if debug_name not in _PROPS:
        raise DesignError(f"unknown prop {debug_name!r}")
    return _PROPS[debug_name]


def prop_size(debug_name, rot=0):
    """Voxel footprint (east, north, up) of a prop, rounded up to the 0.5 m grid."""
    m = prop(debug_name)["size_m"]
    # Round up to whole voxels, ignoring the few centimetres that model bounds overhang (1.03 m -> 2).
    up = lambda v: max(1, -int(-(v / VOXEL_M - 0.15) // 1))
    w, d, h = up(m["x"]), up(m["z"]), max(1, -int(-m["y"] // VOXEL_M))  # height strictly: it must clear the ceiling
    return [d, w, h] if rot in (90, 270) else [w, d, h]


def mount_of(debug_name):
    """How a prop attaches in game: ``floor`` (pivot on the floor), ``wall`` or ``ceiling``.

    Read from the game placement box: wall props reach forward from a pivot on the wall face
    (min z 0), ceiling props hang below a pivot on the ceiling (max y <= 0). Checked against
    the wall candles, torches, paintings and chandeliers of the Mayor Manor and Everspire.
    """
    rec = prop(debug_name)
    box = rec.get("placement_aabb_m")
    name = debug_name.lower()
    if "ceiling" in name or "chandelier" in name:
        return "ceiling"
    wallish = "wall" in name and any(k in name for k in ("illumination", "candle", "painting", "banner", "trophy", "torch"))
    flat_back = box and box[0][2] >= -0.06 and box[1][2] > 0.05
    hangs = box and box[1][1] <= 0.05 and box[0][1] < -0.2   # flat rugs and plates lie at y 0
    if (wallish and (not box or flat_back)) or (flat_back and hangs):
        return "wall"  # wall shelves hang below a pivot on the wall face as well
    if hangs:
        return "ceiling"
    return "floor"


class DesignError(ValueError):
    pass


#: Neighbours that share a face or an edge with a cell (18): what holds blocks together.
NEAR = [(i, j, k) for i in (-1, 0, 1) for j in (-1, 0, 1) for k in (-1, 0, 1) if 0 < abs(i) + abs(j) + abs(k) <= 2]


def _vec(value, n, what):
    if not (isinstance(value, list) and len(value) == n and all(isinstance(v, int) for v in value)):
        raise DesignError(f"{what}: expected {n} integers, got {value!r}")
    return value


def _box(at, size):
    x0, y0, z0 = at
    sx, sy, sz = size
    if min(size) <= 0:
        raise DesignError(f"box size must be positive: {size}")
    return ((x, y, z) for x in range(x0, x0 + sx) for y in range(y0, y0 + sy) for z in range(z0, z0 + sz))


SCENE_SCHEMA = "custodian.enshrouded.scene.v1"


def load_fragment(path):
    """Blocks and game props of a saved piece: a world capture or a scene file.

    Returns ``(voxels, props, extra)``: voxels map (x, y, z) to a game block name, props are dicts
    with ``prop``, ``pos`` (voxel units, float), ``rot_xyzw`` (game frame) and ``size_m``.
    """
    path = Path(path)
    if not path.is_absolute() and not path.exists():
        path = GEOMETRY_PATH.parent / path
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") == SCENE_SCHEMA:
        ids, runs = data["materials"], data["runs"]
    elif "voxel_runs_x_y_z_len_material" in data:
        ids = {str(m["material_id"]): m["block"] for m in data["blocks_by_material"]}
        runs = data["voxel_runs_x_y_z_len_material"]
    else:
        raise DesignError(f"{path.name}: not a world capture or {SCENE_SCHEMA} file")
    voxels = {}
    for x, y, z, n, m in runs:
        block = ids.get(str(m), f"material_{m}")
        for i in range(n):
            voxels[(x + i, y, z)] = block
    props = [dict(p, prop=p.get("prop") or p.get("debug_name"), pos=list(p["pos"]),
                  rot_xyzw=list(p.get("rot_xyzw") or [0, 0, 0, 1])) for p in data.get("props") or []]
    extra = {"carved": [tuple(c) for c in data.get("carved") or []],
             "density": {tuple(r[:3]): r[3] for r in data.get("terrain_density") or []}}
    return voxels, props, extra


def _plan(x0, y0, n, shape="square"):
    """Cells of a square of side ``n``, or an octagon cut from it (corner cut ~0.29 n)."""
    cut = round(n * 0.29) if shape == "octagon" else 0
    m = n - 1
    return {(x0 + a, y0 + b) for a in range(n) for b in range(n) if min(a, m - a) + min(b, m - b) >= cut}


def _outline(plan):
    """Edge cells of a plan; diagonal edges come out as closed staircases (no see-through gaps)."""
    return {(x, y) for x, y in plan if any((x + i, y + j) not in plan for i in (-1, 0, 1) for j in (-1, 0, 1))}


def _turn_xy(x, y, rot):
    """Turn a point anticlockwise about the z axis by 0/90/180/270 degrees."""
    for _ in range(rot // 90 % 4):
        x, y = -y, x
    return x, y


def _yaw_quat(rot, q):
    """Compose a game-frame quaternion with a turn of ``rot`` degrees anticlockwise in house axes.

    House x/y are game X/Z with z up, so an anticlockwise turn here is a turn about game Y by -rot.
    """
    import math
    if rot % 360 == 0:
        return list(q)
    h = -math.radians(rot) / 2
    ay, aw = math.sin(h), math.cos(h)
    x, y, z, w = q
    return [round(v, 5) for v in (aw * x + ay * z, aw * y + ay * w, aw * z - ay * x, aw * w - ay * y)]


class House:
    def __init__(self, spec: dict):
        if spec.get("schema") != SCHEMA:
            raise DesignError(f"schema must be {SCHEMA}")
        self.spec = spec
        self.materials = dict(spec.get("materials") or {})
        self.voxels: dict[tuple[int, int, int], str] = {}
        self.doors: set[tuple[int, int, int]] = set()
        self.notes: list[str] = []
        self.placements: list[dict] = []
        self.flights: list[dict] = []
        #: Doorways (``door``/``double_door`` ops and door-frame templates): hole corner, width,
        #: height and the axis the wall runs along. Checked for clear floor on both sides.
        self.openings: list[dict] = []
        #: ``ground: true`` (or {depth, material}) means flat terrain fills z < 0 wherever no cut
        #: dug it out. The bundle export writes that terrain for real (``ground_cells``), so the
        #: building never hangs over a dip in the world.
        self.ground = bool(spec.get("ground"))
        g = spec.get("ground") if isinstance(spec.get("ground"), dict) else {}
        self.ground_depth = int(g.get("depth", GROUND_DEPTH))
        self.ground_material = g.get("material", GROUND_MATERIAL)
        self.dug: set[tuple[int, int, int]] = set()
        #: Game props that came with pasted fragments, at their exact captured positions.
        self.placed: list[dict] = []
        #: Terrain cells below full density, from imported pieces (cell -> 0..254).
        self.density: dict[tuple[int, int, int], int] = {}
        for i, part in enumerate(spec.get("parts") or []):
            try:
                self._apply(part)
            except DesignError as exc:
                raise DesignError(f"parts[{i}] {part.get('op')}: {exc}") from None

    def _mat(self, key):
        if key not in self.materials and str(key).startswith(("Block_", "Terrain_", "_Block_")):
            self.materials[key] = key  # a game block name can be used directly as a material
        if key not in self.materials:
            raise DesignError(f"unknown material key {key!r}")
        return key

    def _fill(self, cells, mat):
        for c in cells:
            self.voxels[c] = mat
            self.doors.discard(c)

    def _apply(self, p):
        op = p.get("op")
        if op == "box":
            self._fill(_box(_vec(p["at"], 3, "at"), _vec(p["size"], 3, "size")), self._mat(p["mat"]))
        elif op == "template":
            self._template(p)
        elif op == "walls":
            x0, y0, z0 = _vec(p["at"], 3, "at")
            sx, sy, sz = _vec(p["size"], 3, "size")
            cells = [c for c in _box([x0, y0, z0], [sx, sy, sz])
                     if c[0] in (x0, x0 + sx - 1) or c[1] in (y0, y0 + sy - 1)]
            self._fill(cells, self._mat(p["mat"]))
        elif op == "cut":
            kind = p.get("kind", "opening")
            if kind not in ("door", "window", "opening"):
                raise DesignError(f"cut kind must be door, window or opening, got {kind!r}")
            size = _vec(p["size"], 3, "size")
            for c in _box(_vec(p["at"], 3, "at"), size):
                self.voxels.pop(c, None)
                if c[2] < 0:
                    self.dug.add(c)
                if kind == "door":
                    self.doors.add(c)
            if kind == "door":
                # A door cut through a wall gets the same clearance and route checks as a door op:
                # the wall runs along the longer horizontal side of the hole.
                along = "x" if size[0] >= size[1] else "y"
                self.openings.append({"at": tuple(_vec(p["at"], 3, "at")), "w": size[0] if along == "x" else size[1],
                                      "h": size[2], "wall": along, "d": size[1] if along == "x" else size[0],
                                      "note": p.get("note") or f"door at {p['at']}"})
            if kind == "door" and size[2] < HEADROOM:
                self.notes.append(f"door at {p['at']} is {size[2] * VOXEL_M} m high, below {HEADROOM * VOXEL_M} m")
        elif op == "gable":
            self._gable(p)
        elif op == "battlement":
            # Merlons along the perimeter of a rectangle: ``merlon`` filled, ``gap`` empty.
            x0, y0, z0 = _vec(p["at"], 3, "at")
            sx, sy, sz = _vec(p["size"], 3, "size")
            period = p.get("merlon", 2) + p.get("gap", 2)
            cells = []
            for c in _box([x0, y0, z0], [sx, sy, sz]):
                on_x, on_y = c[0] in (x0, x0 + sx - 1), c[1] in (y0, y0 + sy - 1)
                if not (on_x or on_y):
                    continue
                pos = (c[0] - x0) if on_y and not on_x else (c[1] - y0)
                if pos % period < p.get("merlon", 2) or (on_x and on_y):
                    cells.append(c)
            self._fill(cells, self._mat(p["mat"]))
        elif op == "stairs":
            # Straight 45° flight: each step one voxel forward and one up, ``width`` across.
            x, y, z = _vec(p["at"], 3, "at")
            dirs = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}
            if p.get("dir") not in dirs:
                raise DesignError(f"dir must be one of {', '.join(dirs)}")
            dx, dy = dirs[p["dir"]]
            width = p.get("width", 2)
            self.flights.append({"at": (x, y, z), "d": (dx, dy), "width": width, "steps": p["steps"],
                                 "note": p.get("note") or f"stairs at {p['at']}"})
            for i in range(p["steps"]):
                sx, sy = x + dx * i, y + dy * i
                size = [1, width, 1] if dx else [width, 1, 1]
                self._fill(_box([sx, sy, z + i], size), self._mat(p["mat"]))
        elif op == "flight":
            self._flight(p)
        elif op in ("door", "double_door", "window"):
            self._opening(op, p)
        elif op == "tower":
            # Hollow tower of ``height`` layers on a square or octagon (corners cut) of side ``size``.
            x0, y0, z0 = _vec(p["at"], 3, "at")
            ring = _outline(_plan(x0, y0, p["size"], p.get("shape", "octagon")))
            self._fill([(x, y, z0 + k) for x, y in ring for k in range(p["height"])], self._mat(p["mat"]))
            if p.get("floor"):
                self._fill([(x, y, z0 - 1) for x, y in _plan(x0, y0, p["size"], p.get("shape", "octagon"))],
                           self._mat(p["floor"]))
        elif op == "spire":
            # Pointed roof: the plan shrinks by one cell a side every ``steep`` layers (2 = 63°).
            x0, y0, z = _vec(p["at"], 3, "at")
            n, steep, shape = p["size"], p.get("steep", 2), p.get("shape", "octagon")
            mat, trim = self._mat(p["mat"]), self._mat(p.get("trim", p["mat"]))
            i = 0
            while n - 2 * i > 0:
                plan = _plan(x0 + i, y0 + i, n - 2 * i, shape if n - 2 * i > 3 else "square")
                for k in range(steep):
                    cells = plan if n - 2 * i <= 2 else _outline(plan)
                    self._fill([(x, y, z) for x, y in cells], trim if i == 0 and k == 0 else mat)
                    z += 1
                i += 1
            if p.get("finial"):
                self._fill([(x0 + (n - 1) // 2, y0 + (n - 1) // 2, z + k) for k in range(p["finial"])],
                           self._mat(p.get("finial_mat", p.get("trim", p["mat"]))))
        elif op == "shed":
            # One-slope roof over a rectangle, falling toward ``down``; ``run`` cells across per step up.
            x0, y0, z0 = _vec(p["at"], 3, "at")
            sx, sy = _vec(p["size"], 2, "size")
            run, oh = p.get("run", 1), p.get("overhang", 1)
            down = p.get("down", "-x")
            roof, gable = self._mat(p["roof"]), self._mat(p.get("gable", p["roof"]))
            along_x = down in ("+x", "-x")
            width = sx if along_x else sy
            for j in range(-oh, width):
                # j counts from the low eave; the slope rises one layer every ``run`` cells.
                k = max(0, j) // run
                for a in range(-oh, (sy if along_x else sx) + oh):
                    b = (x0 + width - 1 - j if down == "+x" else x0 + j) if along_x else \
                        (y0 + width - 1 - j if down == "+y" else y0 + j)
                    cell = (b, y0 + a, z0 + k) if along_x else (x0 + a, b, z0 + k)
                    self._fill([cell], roof)
                    # Close the two ends under the slope.
                    if 0 <= j and a in (0, (sy if along_x else sx) - 1):
                        self._fill([(c[0], c[1], c[2] - h) for c in [cell] for h in range(1, k + 1)], gable)
        elif op == "inlay":
            # Pixel picture in a wall plane: ``rows`` top to bottom, '#' = ``mat``; runs along +x or +y.
            x0, y0, z0 = _vec(p["at"], 3, "at")
            rows = p["rows"]
            cells = []
            for r, row in enumerate(rows):
                for i, ch in enumerate(row):
                    if ch == "#":
                        z = z0 + len(rows) - 1 - r
                        cells.append((x0 + i, y0, z) if p.get("wall", "x") == "x" else (x0, y0 + i, z))
            self._fill(cells, self._mat(p["mat"]))
        elif op == "fragment":
            self._fragment(p)
        elif op == "replace":
            # Swap one material for another, optionally only inside a box.
            src, dst = self._mat(p["from"]), self._mat(p["to"])
            box = set(_box(_vec(p["at"], 3, "at"), _vec(p["size"], 3, "size"))) if p.get("at") else None
            for c, m in list(self.voxels.items()):
                if m == src and (box is None or c in box):
                    self.voxels[c] = dst
        elif op == "pattern":
            # Repeating inlay: inside the box, a cell gets ``mat`` where its offset modulo ``step``
            # falls inside ``cell`` (e.g. step [4,1,4], cell [1,1,1] = a dot every 2 m). With
            # ``only_on`` it recolours existing blocks of that material instead of filling air.
            x0, y0, z0 = _vec(p["at"], 3, "at")
            step, cell = p.get("step", [2, 2, 2]), p.get("cell", [1, 1, 1])
            mat, only = self._mat(p["mat"]), p.get("only_on")
            only = self._mat(only) if only else None
            cells = [c for c in _box([x0, y0, z0], _vec(p["size"], 3, "size"))
                     if all((c[i] - (x0, y0, z0)[i]) % step[i] < cell[i] for i in range(3))
                     and (only is None or self.voxels.get(c) == only)]
            self._fill(cells, mat)
        else:
            raise DesignError(f"unknown op {op!r}")

    def _fragment(self, p):
        """Paste a saved piece (world capture or scene file) with its blocks and game props.

        ``crop`` {at, size} keeps part of the source (source coordinates); ``origin`` is the
        source point that lands on ``at``; ``rot`` turns it anticlockwise about z.
        """
        voxels, props, extra = load_fragment(p["file"])
        if p.get("crop"):
            keep = set(_box(_vec(p["crop"]["at"], 3, "crop.at"), _vec(p["crop"]["size"], 3, "crop.size")))
            voxels = {c: m for c, m in voxels.items() if c in keep}
            (cx0, cy0, cz0), (sx, sy, sz) = p["crop"]["at"], p["crop"]["size"]
            props = [q for q in props if cx0 <= q["pos"][0] < cx0 + sx and cy0 <= q["pos"][1] < cy0 + sy
                     and cz0 <= q["pos"][2] < cz0 + sz]
        ox, oy, oz = p.get("origin", [0, 0, 0])
        ax, ay, az = _vec(p.get("at", [0, 0, 0]), 3, "at")
        rot = p.get("rot", 0)
        if rot not in (0, 90, 180, 270):
            raise DesignError("rot must be 0, 90, 180 or 270")
        swap = {k: v for k, v in (p.get("materials") or {}).items()}
        def place(x, y, z):
            # Voxel centres turn about the origin; shift back to the cell's corner afterwards.
            tx, ty = _turn_xy(x - ox + .5, y - oy + .5, rot)
            return ax + round(tx - .5), ay + round(ty - .5), az + z - oz

        keep = set(_box(_vec(p["crop"]["at"], 3, "crop.at"), _vec(p["crop"]["size"], 3, "crop.size"))) if p.get("crop") else None
        for c in extra["carved"]:
            if keep is None or c in keep:
                cell = place(*c)
                self.voxels.pop(cell, None)
                self.dug.add(cell)
        for c, block in voxels.items():
            cell = place(*c)
            self.voxels[cell] = self._mat(swap.get(block, block))
            self.doors.discard(cell)
            if c in extra["density"]:
                self.density[cell] = extra["density"][c]
        if p.get("props", True):
            for q in props:
                tx, ty = _turn_xy(q["pos"][0] - ox, q["pos"][1] - oy, rot)
                self.placed.append(dict(q, pos=[ax + tx, ay + ty, az + q["pos"][2] - oz],
                                        rot_xyzw=_yaw_quat(rot, q["rot_xyzw"]), source=p["file"]))

    def _opening(self, kind, p):
        """Cut a door or window hole in a 1-voxel wall and hang the game's door/window prop in it.

        ``at`` is the hole's lowest south-west cell, ``wall`` the axis the wall runs along (x or y).
        The prop pivot is the hole's bottom centre in the middle of the wall (as in the game's
        own manor blueprints); ``flip`` turns it 180°. ``prop: null`` cuts the hole only.
        """
        (w, h), default = OPENINGS[kind]
        w, h = p.get("width", w), p.get("height", h)
        x, y, z = _vec(p["at"], 3, "at")
        along = p.get("wall", "x")
        if along not in ("x", "y"):
            raise DesignError("wall must be 'x' or 'y'")
        size = [w, 1, h] if along == "x" else [1, w, h]
        for c in _box([x, y, z], size):
            self.voxels.pop(c, None)
            if kind != "window":
                self.doors.add(c)
        if kind != "window":
            self.openings.append({"at": (x, y, z), "w": w, "h": h, "wall": along,
                                  "note": p.get("note") or f"{kind} at {p['at']}"})
        if kind != "window" and h < HEADROOM:
            self.notes.append(f"door at {p['at']} is {h * VOXEL_M} m high, below {HEADROOM * VOXEL_M} m")
        name = p.get("prop", default)
        if not name:
            return
        rot = (0 if along == "x" else 90) + (180 if p.get("flip") else 0)
        pos = [x + w / 2, y + .5, z] if along == "x" else [x + .5, y + w / 2, z]
        rec = prop(name)
        self.placed.append({"prop": name, "pos": pos, "rot_xyzw": _yaw_quat(rot, [0, 0, 0, 1]),
                            "size_m": rec.get("size_m"), "name_ru": rec.get("name_ru"), "group": rec.get("group"),
                            "source": kind})

    def _flight(self, p):
        """One storey of stairs from the game's Stair 4m template plus a first step of single blocks.

        ``at`` is the first step (lowest, at floor level z), its south-west corner across the
        4 m width; ``dir`` is the way up. The template rises 4 m over 4 m in 0.5 m steps and
        sits one voxel up, so the flight climbs the 9-voxel storey pitch (8 wall + 1 slab).
        """
        x, y, z = _vec(p["at"], 3, "at")
        # ``rise`` is the storey pitch climbed; everything over the template's 8 steps becomes
        # starter steps of solid single-block columns in front of it.
        n = p.get("rise", 9) - 8
        if n < 1:
            raise DesignError("rise must be at least 9 voxels")
        # Unturned template rises toward -x; rot maps that to each direction (see _template.turn).
        spec = {"+x": (180, x + n, y), "-x": (0, x - n - 7, y), "+y": (270, x, y + n), "-y": (90, x, y - n - 7)}
        if p.get("dir") not in spec:
            raise DesignError(f"dir must be one of {', '.join(spec)}")
        rot, tx, ty = spec[p["dir"]]
        dx, dy = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}[p["dir"]]
        mat = self._mat(p["mat"])
        for i in range(n):
            self._fill(_box([x + dx * i, y + dy * i, z], [1, 8, i + 1] if dx else [8, 1, i + 1]), mat)
        self._template({"name": "stair_4m", "mat": p["mat"], "at": [tx, ty, z + n], "rot": rot})
        self.flights.append({"at": (x, y, z), "d": (dx, dy), "width": 8, "steps": n + 8,
                             "note": p.get("note") or f"flight at {p['at']}"})

    def _template(self, p):
        """Place a template ``count`` times; ``dir`` x/y or ``rot`` 0/90/180/270 turns it about z."""
        name = p.get("name")
        t = shape(name)
        mat = self._mat(p["mat"])
        w, d, h = t["size"]
        x0, y0, z0 = _vec(p["at"], 3, "at")
        rot = p.get("rot", 90 if p.get("dir") == "y" else 0)
        if rot not in (0, 90, 180, 270):
            raise DesignError("rot must be 0, 90, 180 or 270")
        count = p.get("count", 1)
        nx, ny = count if isinstance(count, list) else (count, 1)
        # Footprint after rotation, and the step between copies along the run.
        fw, fd = (w, d) if rot in (0, 180) else (d, w)
        kind = "door" if "Wall_Door" in t["debug"] and "beam" not in t["debug"] else None

        def turn(a, b):
            if rot == 0:
                return a, b
            if rot == 90:
                return d - 1 - b, a
            if rot == 180:
                return w - 1 - a, d - 1 - b
            return b, w - 1 - a

        for i in range(nx):
            for j in range(ny):
                if isinstance(count, list) or rot in (0, 180):
                    ox, oy = x0 + i * fw, y0 + j * fd
                else:  # a single run of a turned template goes along y
                    ox, oy = x0, y0 + i * fd
                cells = [(ox + turn(a, b)[0], oy + turn(a, b)[1], z0 + c) for a, b, c in t["cells"]]
                self._fill(cells, mat)
                if kind:
                    hole = []
                    for a, b, c in t["empty"]:
                        tx, ty = turn(a, b)
                        cell = (ox + tx, oy + ty, z0 + c)
                        self.voxels.pop(cell, None)
                        self.doors.add(cell)
                        hole.append(cell)
                    xs, ys, zs = zip(*hole)
                    along = "x" if max(xs) - min(xs) >= max(ys) - min(ys) else "y"
                    self.openings.append({"at": (min(xs), min(ys), min(zs)), "wall": along,
                                          "w": (max(xs) - min(xs) if along == "x" else max(ys) - min(ys)) + 1,
                                          "h": max(zs) - min(zs) + 1, "note": p.get("note") or f"{name} at {[ox, oy, z0]}"})
                self.placements.append({"template": name, "game": t["debug"], "at": [ox, oy, z0],
                                        "rot": rot, "mat": p["mat"]})

    def _gable(self, p):
        """45° two-slope roof over a rectangle; ridge runs along ``ridge`` axis."""
        x0, y0, z0 = _vec(p["at"], 3, "at")
        sx, sy = _vec(p["size"], 2, "size")
        oh = p.get("overhang", 1)
        ridge = p.get("ridge", "x")
        roof, gable = self._mat(p["roof"]), self._mat(p.get("gable", p["roof"]))
        # Work in (along, across) and map back so both ridge directions share one loop.
        if ridge == "x":
            along0, along_n, across0, across_n = x0, sx, y0, sy
            put = lambda a, b, z: (a, b, z)
        elif ridge == "y":
            along0, along_n, across0, across_n = y0, sy, x0, sx
            put = lambda a, b, z: (b, a, z)
        else:
            raise DesignError("ridge must be 'x' or 'y'")
        lo, hi = across0 - oh, across0 + across_n - 1 + oh
        k = 0
        while lo + k <= hi - k:
            z = z0 + k
            for a in range(along0 - oh, along0 + along_n + oh):
                self._fill([put(a, lo + k, z), put(a, hi - k, z)], roof)
            # Gable triangles close the two end walls under the slope.
            inner = range(max(lo + k + 1, across0), min(hi - k, across0 + across_n - 1) + 1)
            for a in (along0, along0 + along_n - 1):
                self._fill([put(a, b, z) for b in inner if b not in (lo + k, hi - k)], gable)
            k += 1
        self.ridge_top = z0 + k - 1
        # An even span ends in two roof cells side by side. The game shapes each roof voxel by
        # its free sides, so two of them at the top turn into two slopes with a slot between
        # (seen in game along the castle nave). A ridge beam of plain blocks closes it.
        k -= 1
        if lo + k != hi - k:
            cap = self._mat(p.get("cap", p.get("gable", p["roof"])))
            for a in range(along0 - oh, along0 + along_n + oh):
                self._fill([put(a, lo + k, z0 + k), put(a, hi - k, z0 + k)], cap)

    # --- checks -------------------------------------------------------------------

    def bbox(self):
        if getattr(self, "_bbox", None) is None or self._bbox[0] != len(self.voxels):
            xs, ys, zs = zip(*self.voxels) if self.voxels else ((0,), (0,), (0,))
            self._bbox = (len(self.voxels), ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))))
        return self._bbox[1]

    def covered(self, x, y, z):
        top = self.bbox()[1][2]
        return any((x, y, zz) in self.voxels for zz in range(z + 1, top + 1))

    def open_to_outside(self, cells):
        """Flood the floor layer from outside the bounding box; doors count as closed."""
        z = min(c[2] for c in cells)
        return any((x, y) in self._outside(z) for x, y, cz in cells if cz == z)

    def _outside(self, z):
        """Cells of layer ``z`` reachable from beyond the bounding box (cached per layer)."""
        cache = self.__dict__.setdefault("_outside_cache", {})
        if (z, len(self.voxels)) not in cache:
            (x0, y0, _), (x1, y1, _) = self.bbox()
            start = (x0 - 1, y0 - 1)
            seen, stack = {start}, [start]
            while stack:
                x, y = stack.pop()
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if not (x0 - 1 <= nx <= x1 + 1 and y0 - 1 <= ny <= y1 + 1) or (nx, ny) in seen:
                        continue
                    if (nx, ny, z) in self.voxels or (nx, ny, z) in self.doors:
                        continue
                    seen.add((nx, ny))
                    stack.append((nx, ny))
            cache[(z, len(self.voxels))] = seen
        return cache[(z, len(self.voxels))]

    def altar_cells(self, margin=0):
        """The sunk platform and the air above it; with ``margin``, the walkable ring at z >= 0."""
        bx, by, bz = ALTAR_BOX
        if margin:
            return set(_box([-margin, -margin, 0], [bx + 2 * margin, by + 2 * margin, ALTAR_ABOVE]))
        return set(_box([0, 0, -bz], [bx, by, bz + ALTAR_ABOVE]))

    def check_altar(self):
        """The altar is mandatory, sits at the origin, stays free and can be walked to."""
        altar = self.spec.get("altar")
        if not altar:
            return ["no altar: every design is laid out from its Flame Altar at (0,0,0)"]
        errors = []
        if altar.get("at", [0, 0, 0]) != [0, 0, 0]:
            errors.append("altar.at must be [0, 0, 0]: coordinates are measured from the altar box corner")
        level = altar.get("level", 1)
        if level not in ALTAR_CUBE_M:
            return errors + [f"altar level must be one of {sorted(ALTAR_CUBE_M)}"]
        # Build cube is centred on the altar (box centre on the 0.5 m grid).
        half = ALTAR_CUBE_M[level] / VOXEL_M / 2
        cx, cy = ALTAR_BOX[0] / 2, ALTAR_BOX[1] / 2
        outside = [c for c in self.voxels if not (cx - half <= c[0] < cx + half and cy - half <= c[1] < cy + half
                                                  and -half <= c[2] < half)]
        if outside:
            errors.append(f"{len(outside)} voxels outside the level {level} altar cube, e.g. {outside[0]}")
        inside = [c for c in self.altar_cells() if c in self.voxels and c[2] >= 0]
        if inside:
            errors.append(f"space over the altar is not free: block at {inside[0]}")
        crowded = [c for c in self.altar_cells(ALTAR_CLEARANCE) - self.altar_cells() if c in self.voxels]
        if crowded:
            errors.append(f"less than {ALTAR_CLEARANCE * VOXEL_M} m free around the altar: block at {crowded[0]}")
        ring = {(x, y, -1) for x, y, z in self.altar_cells(ALTAR_CLEARANCE) if z == 0}
        gap = sorted(c for c in ring if c not in self.voxels)
        if gap:
            under = [c for c in gap if 0 <= c[0] < ALTAR_BOX[0] and 0 <= c[1] < ALTAR_BOX[1]]
            errors.append(f"hole at the altar: the floor (z = -1) must be solid blocks under the altar platform and "
                          f"{ALTAR_CLEARANCE * VOXEL_M} m around it, e.g. {list((under or gap)[0])}; do not carve a pit "
                          f"for the platform, the game shows it as a hole")
        if not self.altar_reachable():
            errors.append(f"no walkable route at least {ALTAR_ROUTE_WIDTH * VOXEL_M} m wide from outside to the altar")
        return errors

    def altar_reachable(self):
        """Walk at altar floor level with a square body of ``ALTAR_ROUTE_WIDTH`` and ``HEADROOM``."""
        (x0, y0, _), (x1, y1, _) = self.bbox()
        x0, y0, x1, y1 = min(x0, -1), min(y0, -1), max(x1, ALTAR_BOX[0]), max(y1, ALTAR_BOX[1])
        w = ALTAR_ROUTE_WIDTH
        clear = lambda x, y: all((x + i, y + j, h) not in self.voxels
                                 for i in range(w) for j in range(w) for h in range(HEADROOM))
        goal = {(x, y) for x, y, z in self.altar_cells(1) - self.altar_cells() if z == 0}
        start = (x0 - w, y0 - w)
        seen, stack = {start}, [start]
        while stack:
            x, y = stack.pop()
            if any((x + i, y + j) in goal for i in range(w) for j in range(w)):
                return True
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if (nx, ny) in seen or not (x0 - w <= nx <= x1 + 1 and y0 - w <= ny <= y1 + 1):
                    continue
                if any((nx + i, ny + j) in goal for i in range(w) for j in range(w)) or clear(nx, ny):
                    seen.add((nx, ny))
                    stack.append((nx, ny))
        return False

    def solid(self, x, y, z):
        return (x, y, z) in self.voxels or (self.ground and z < 0 and (x, y, z) not in self.dug)

    def walkable(self, x, y, z):
        """A player can stand with feet at ``z``: solid floor below and ``HEADROOM`` of air."""
        return self.solid(x, y, z - 1) and all((x, y, z + h) not in self.voxels for h in range(HEADROOM))

    def check_stairs(self):
        """Headroom over every step and a flat landing at both ends of every flight."""
        errors = []
        for f in self.flights:
            (x, y, z), (dx, dy), w, n = f["at"], f["d"], f["width"], f["steps"]
            across = [(0, j) if dx else (j, 0) for j in range(w)]
            cell = lambda i, k: (x + dx * i + across[k][0], y + dy * i + across[k][1])
            low = [i for i in range(n) for k in range(w)
                   if any((*cell(i, k), z + i + 1 + h) in self.voxels for h in range(HEADROOM))]
            if low:
                errors.append(f"{f['note']}: less than {HEADROOM * VOXEL_M} m headroom over step {low[0] + 1}")
            gone = [i for i in range(n) for k in range(w) if not self.solid(*cell(i, k), z + i)]
            if gone:
                errors.append(f"{f['note']}: step {gone[0] + 1} is missing at {list(cell(gone[0], 0)) + [z + gone[0]]} "
                              f"(a cut over the stairs took it: keep the last step, it is part of the floor above)")
            furn = self._furniture_cells()
            free = lambda c, zz: self.walkable(*c, zz) and not any((*c, zz + h) in furn for h in range(HEADROOM))
            top = [i for i in range(n, n + STAIR_LANDING) for k in range(w) if not free(cell(i, k), z + n)]
            if top:
                errors.append(f"{f['note']}: no {STAIR_LANDING * VOXEL_M} m landing at the top "
                              f"(step {top[0] - n + 1} past the last stair at {list(cell(top[0], 0)) + [z + n]})")
            bottom = [i for i in range(-1, -STAIR_FOOT - 1, -1) for k in range(w) if not free(cell(i, k), z)]
            if bottom:
                errors.append(f"{f['note']}: no {STAIR_FOOT * VOXEL_M} m landing at the bottom "
                              f"(at {list(cell(bottom[0], 0)) + [z]})")
        return errors

    def furniture(self):
        """Furniture with ``prop`` (game debug name) gets its size from the game data."""
        out = []
        for f in self.spec.get("furniture") or []:
            f = dict(f)
            if f.get("prop"):
                rec = prop(f["prop"])
                f.setdefault("item", rec.get("name_ru") or f["prop"])
                f.setdefault("mount", mount_of(f["prop"]))
                if f["mount"] == "wall" and "rot" not in f:
                    f["rot"] = self._wall_rot(f)
                f["size"] = prop_size(f["prop"], f.get("rot", 0))
                if f["mount"] == "wall":
                    # ``at`` is the cell against the wall; the prop reaches out from it, away from the wall.
                    fx, fy = FRONT[f.get("rot", 0)]
                    x, y, z = f["at"]
                    f["at"] = [x - (f["size"][0] - 1 if fx < 0 else 0), y - (f["size"][1] - 1 if fy < 0 else 0), z]
                f.setdefault("group", rec["group"])
                if f.get("lit"):
                    f.setdefault("template_uuid", lit_uuid(f["prop"]))
                if f["mount"] != "floor":
                    f["walkable"] = True  # hangs on a wall or ceiling: people walk under or past it
                f["pos"], f["rot_xyzw"] = self._pivot(f)
            out.append(f)
        for q in self.placed:
            # Captured game props keep their exact position; they are shown but not re-checked.
            m = q.get("size_m") or {"x": 1, "y": 1, "z": 1}
            w, d, h = (max(1, round(m[k] / VOXEL_M)) for k in ("x", "z", "y"))
            x, y, z = q["pos"]
            out.append({"id": q.get("name_ru") or q["prop"], "item": q.get("name_ru") or q["prop"], "prop": q["prop"],
                        "pos": q["pos"], "rot_xyzw": q["rot_xyzw"], "group": q.get("group"), "placed": True,
                        "template_uuid": q.get("template_uuid"), "scale": q.get("scale"),
                        "at": [int(x - w / 2), int(y - d / 2), int(z)], "size": [w, d, h]})
        return out

    def _wall_rot(self, f):
        """Snap a wall prop: turn it so its back is on the first wall found beside ``at``."""
        x, y, z = f["at"]
        for rot, (fx, fy) in FRONT.items():
            if (x - fx, y - fy, z) in self.voxels and (x, y, z) not in self.voxels:
                return rot
        return 0

    def _pivot(self, f):
        """Game pivot (voxel units, house axes) and rotation of a design prop, by its mount.

        floor: bottom centre of the footprint. wall: on the wall face behind the footprint (the
        game measures wall lamps, torches and paintings from the face, 0-5 cm off it), at the
        height of ``at`` plus ``lift`` voxels. ceiling: under the first block above ``at``.
        """
        (x, y, z), (w, d, h) = f["at"], f["size"]
        rot = f.get("rot", 0)
        q = _yaw_quat(rot, [0, 0, 0, 1])
        mount = f.get("mount", "floor")
        if mount == "wall":
            fx, fy = FRONT[rot]
            px = x + w / 2 if not fx else (x if fx > 0 else x + w)
            py = y + d / 2 if not fy else (y if fy > 0 else y + d)
            return [px, py, z + f.get("lift", 0)], q
        if mount == "ceiling":
            cx, cy = x + w // 2, y + d // 2
            top = next((k for k in range(z, z + 17) if (cx, cy, k) in self.voxels), z + h)
            return [x + w / 2, y + d / 2, top], q
        return [x + w / 2, y + d / 2, z], q

    # --- buildability: what goes wrong when the blueprint is placed in the real world ----------

    def ground_cells(self):
        """Terrain the bundle writes under the build when ``ground`` is on.

        Every column of the crop gets solid dirt from z = -1 down ``ground_depth`` voxels, except
        blocks and dug cells. Under the altar too: the floor runs under its platform. Without it the floor slab at z = -1 hangs over every
        dip of the real site and the yard, the altar ring and the garden stand in the air.
        """
        if not self.ground:
            return set()
        (x0, y0, z0), (x1, y1, _) = self.bbox()
        x0, y0, x1, y1 = min(x0, 0), min(y0, 0), max(x1, ALTAR_BOX[0] - 1), max(y1, ALTAR_BOX[1] - 1)
        return {(x, y, z) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)
                for z in range(min(-self.ground_depth, z0), 0)
                if (x, y, z) not in self.voxels and (x, y, z) not in self.dug}

    def _standing(self):
        """Cells a player can stand in: floor below, ``PASSAGE_HEAD`` free above (cached)."""
        if getattr(self, "_stand", None) is not None and self._stand[0] == len(self.voxels):
            return self._stand[1]
        (x0, y0, z0), (x1, y1, z1) = self.bbox()
        cols = {}
        for (x, y, z) in self.voxels:
            cols.setdefault((x, y), set()).add(z)
        stand = {}
        pad = PASSAGE + 2
        for x in range(x0 - pad, x1 + pad + 1):
            for y in range(y0 - pad, y1 + pad + 1):
                col = cols.get((x, y), ())
                tops = {z + 1 for z in col if z + 1 not in col}
                if self.ground:  # the terrain surface: the top of the ground, or the bottom of a dug pit
                    tops |= {z for z in range(min(0, z0), 1) if self.solid(x, y, z - 1) and not self.solid(x, y, z)}
                ok = sorted(t for t in tops if not any(self.solid(x, y, t + h) for h in range(PASSAGE_HEAD))
                            and self.solid(x, y, t - 1))
                if ok:
                    stand[(x, y)] = ok
        self._stand = (len(self.voxels), stand)
        return stand

    def _hung_cells(self, f):
        """Cells a ceiling prop really fills once hung from its pivot (game placement box)."""
        if f.get("mount") != "ceiling" or not f.get("prop") or "pos" not in f:
            return []
        box = prop(f["prop"]).get("placement_aabb_m")
        if not box:
            return []
        (x, y, _), (w, d, _) = f["at"], f["size"]
        top = f["pos"][2]
        lo, hi = math.floor(top + box[0][1] / VOXEL_M), math.ceil(top + box[1][1] / VOXEL_M)
        return list(_box([x, y, lo], [w, d, max(1, hi - lo)]))

    def _furniture_cells(self):
        cells = set()
        for f in self.furniture():
            if f.get("placed") or f.get("walkable"):
                continue
            cells |= set(_box(f["at"], f.get("size", [1, 1, 1])))
        return cells

    def check_doors(self):
        """Clear floor on both sides of every door; no column, wall, furniture or drop in it."""
        errors, warnings, sweeps = [], [], []
        furn = self._furniture_cells()
        for o in self.openings:
            (x, y, z), w, h = o["at"], o["w"], o["h"]
            for side in (-1, 1):
                sweep = set()
                bad = None
                for depth in range(1, DOOR_CLEAR + 1):
                    for i in range(w):
                        off = side * depth + (o.get("d", 1) - 1 if side > 0 else 0)   # thick walls: past the far face
                        cx, cy = (x + i, y + off) if o["wall"] == "x" else (x + off, y + i)
                        if depth <= DOOR_SWING:
                            sweep.add((cx, cy))
                        if bad:
                            continue
                        hit = next(((cx, cy, z + k) for k in range(min(h, PASSAGE_HEAD)) if (cx, cy, z + k) in self.voxels), None)
                        if hit:
                            bad = f"block at {list(hit)}"
                        elif any((cx, cy, z + k) in furn for k in range(min(h, PASSAGE_HEAD))):
                            bad = f"furniture at {[cx, cy, z]}"
                        elif not self.solid(cx, cy, z - 1):
                            bad = f"no floor at {[cx, cy, z - 1]}"
                if bad:
                    where = ("-y" if side < 0 else "+y") if o["wall"] == "x" else ("-x" if side < 0 else "+x")
                    errors.append(f"{o['note']}: less than {DOOR_CLEAR * VOXEL_M:g} m of free floor on its {where} side "
                                  f"({bad}); keep columns, walls, stairs and furniture out of the door's way")
                sweeps.append((o["note"], z, sweep))
        for i, (na, za, a) in enumerate(sweeps):
            for nb, zb, b in sweeps[i + 1:]:
                if za == zb and na != nb and a & b:
                    warnings.append(f"{na} and {nb}: open doors hit each other at {list(sorted(a & b)[0]) + [za]}")
                    break
        return errors, warnings

    def check_holes(self):
        """Holes in floors and walkways: an air cell with no floor, ringed by floor or wall."""
        stand = self._standing()
        stairs = set()
        for f in self.flights:
            (x, y, z), (dx, dy), w, n = f["at"], f["d"], f["width"], f["steps"]
            for i in range(-1, n + 1):
                for k in range(w):
                    cx, cy = (x + dx * i, y + k) if dx else (x + k, y + dy * i)
                    stairs.add((cx, cy))
        on = {(x, y, z) for (x, y), zs in stand.items() for z in zs}
        n4 = lambda x, y: ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
        # A floor cell has floor on at least three sides: wall tops and lintels (one cell wide) do not count.
        floor = {(x, y, z) for x, y, z in on if sum((a, b, z) in on for a, b in n4(x, y)) >= 3}
        holes = []
        pit = {(x, y) for x, y, _ in self.altar_cells()}
        for (x, y, z) in floor:
            for nx, ny in n4(x, y):
                c = (nx, ny, z)
                if c in on or c in self.voxels or (nx, ny) in pit or (nx, ny) in stairs:
                    continue
                if self.solid(nx, ny, z - 1) or self.solid(nx, ny, z - 2):
                    continue  # a half-block step, not a hole
                # Floor on the far side too, or a wall right behind: a pit or a slot along a wall.
                ox, oy = 2 * nx - x, 2 * ny - y
                ring = sum((a, b, z) in floor or (a, b, z) in self.voxels for a, b in n4(nx, ny))
                if (ox, oy, z) in floor or (ox, oy, z) in self.voxels or ring >= 3:
                    holes.append(c)
        # Wider pits: an enclosed patch of unsupported air at floor level, ringed by floor or walls.
        def open_air(c):
            x, y, z = c
            return (c not in on and c not in self.voxels and (x, y) not in pit and (x, y) not in stairs
                    and not self.solid(x, y, z - 1) and not self.solid(x, y, z - 2))
        done = set()
        for (x, y, z) in floor:
            for nx, ny in n4(x, y):
                c = (nx, ny, z)
                if c in done or not open_air(c):
                    continue
                comp, stack, closed = {c}, [c], True
                while stack and closed:
                    cx, cy, cz = stack.pop()
                    for a, b in n4(cx, cy):
                        m = (a, b, cz)
                        if m in comp or m in on or m in self.voxels or (a, b) in pit or (a, b) in stairs:
                            continue
                        if not open_air(m) or len(comp) >= 64:
                            closed = False     # it opens onto lower ground or is too big: a yard, not a pit
                            break
                        comp.add(m)
                        stack.append(m)
                done |= comp
                if closed:
                    holes += sorted(comp)
        holes = sorted(set(holes))
        if holes:
            return [f"{len(holes)} hole(s) in floors or walkways, e.g. at {list(holes[0])} (no block under a cell ringed by floor)"]
        return []

    def check_floating(self):
        """Every block must hang together with the ground: a piece that touches nothing floats in game."""
        voxels = self.voxels
        (_, _, zmin), _ = self.bbox()
        seen, floating = set(), []
        for start in voxels:
            if start in seen:
                continue
            comp, stack, held = [start], [start], False
            seen.add(start)
            while stack:
                x, y, z = stack.pop()
                # Held by the ground only where it touches terrain that is still there (not dug out).
                if z == zmin or self.ground and any(
                        n[2] < 0 and n not in voxels and n not in self.dug
                        for n in ((x, y, z - 1), (x + 1, y, z), (x - 1, y, z), (x, y + 1, z), (x, y - 1, z))):
                    held = True
                # Edge contact counts: 45° roofs and stairs are cubes touching along an edge.
                for n in ((x + i, y + j, z + k) for i, j, k in NEAR):
                    if n in voxels and n not in seen:
                        seen.add(n)
                        stack.append(n)
                        comp.append(n)
            if not held:
                floating.append((len(comp), min(comp, key=lambda c: (c[2], c))))
        return [f"floating piece of {n} block(s) at {list(c)}: it touches neither the building nor the ground"
                for n, c in sorted(floating, key=lambda t: -t[0])[:8]] + (
                [f"... and {len(floating) - 8} more floating pieces"] if len(floating) > 8 else [])

    def check_routes(self):
        """A 2 m wide, 2.5 m high walker must reach every door, landing, station and chest."""
        stand = self._standing()
        w = PASSAGE
        furn = self._furniture_cells()
        if furn:   # furniture in the way is as solid as a wall for the walker
            stand = {c: [z for z in zs if not any((*c, z + k) in furn for k in range(PASSAGE_HEAD))]
                     for c, zs in stand.items()}
        on = {(x, y, z) for (x, y), zs in stand.items() for z in zs}

        def body(x, y, z):
            # Square of w x w standing cells; heights may climb one voxel per cell (stairs), and
            # neighbouring cells differ by one voxel at most: standing on a rail or a wall top
            # beside a narrow gap does not make the gap wide.
            got = {}
            for i in range(w):
                for j in range(w):
                    zs = stand.get((x + i, y + j))
                    if not zs:
                        return None
                    ref = got[(i - 1, j)] if i else got[(i, j - 1)] if j else z
                    zz = min(zs, key=lambda t: (abs(t - ref), abs(t - z)))
                    if abs(zz - z) > i + j:
                        return None
                    if (i and abs(got[(i - 1, j)] - zz) > 1) or (j and abs(got[(i, j - 1)] - zz) > 1):
                        return None
                    got[(i, j)] = zz
            return [(x + i, y + j, zz) for (i, j), zz in got.items()]

        starts = []
        (x0, y0, _), (x1, y1, _) = self.bbox()
        for (x, y, z) in on:
            if z == 0 and (x < x0 or x > x1 - w + 1 or y < y0 or y > y1 - w + 1 or
                           (-ALTAR_CLEARANCE <= x < ALTAR_BOX[0] + ALTAR_CLEARANCE - w + 1 and
                            -ALTAR_CLEARANCE <= y < ALTAR_BOX[1] + ALTAR_CLEARANCE - w + 1)):
                starts.append((x, y, z))
        seen, covered, stack = set(), set(), []
        for a in starts:
            b = body(*a)
            if b:
                seen.add(a)
                stack.append(a)
                covered.update(b)
        while stack:
            x, y, z = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                for nz in (z, z + 1, z - 1):
                    n = (nx, ny, nz)
                    if n in seen or n not in on:
                        continue
                    b = body(*n)
                    if b:
                        seen.add(n)
                        stack.append(n)
                        covered.update(b)
                        break
        targets = []
        for o in self.openings:
            (x, y, z), ow = o["at"], o["w"]
            mid = ow // 2
            for side in (-1, 1):
                t = (x + mid, y + side, z) if o["wall"] == "x" else (x + side, y + mid, z)
                targets.append((o["note"], t))
        for f in self.flights:
            (x, y, z), (dx, dy), fw, n = f["at"], f["d"], f["width"], f["steps"]
            mid = (0, fw // 2) if dx else (fw // 2, 0)
            targets.append((f["note"] + " (bottom)", (x - dx + mid[0], y - dy + mid[1], z)))
            targets.append((f["note"] + " (top)", (x + dx * n + mid[0], y + dy * n + mid[1], z + n)))
        dirs = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}
        for f in self.furniture():
            if f.get("placed") or f.get("group") not in USED_GROUPS or f.get("use") not in dirs:
                continue
            (x, y, z), (sw, sd, _) = f["at"], f["size"]
            dx, dy = dirs[f["use"]]
            t = (x + sw // 2 + (dx * (sw // 2 + 1) if dx else 0), y + sd // 2 + (dy * (sd // 2 + 1) if dy else 0), z)
            if dx > 0:
                t = (x + sw, y + sd // 2, z)
            elif dx < 0:
                t = (x - 1, y + sd // 2, z)
            elif dy > 0:
                t = (x + sw // 2, y + sd, z)
            else:
                t = (x + sw // 2, y - 1, z)
            targets.append((f.get("id") or f.get("item"), t))
        missing = [(n, t) for n, t in targets if t not in covered]
        return [f"{n}: no {w * VOXEL_M:g} m wide, {PASSAGE_HEAD * VOXEL_M:g} m high route from the entrance reaches {list(t)}"
                for n, t in missing[:12]] + ([f"... and {len(missing) - 12} more places a 2 m wide walker cannot reach"]
                                             if len(missing) > 12 else [])

    def check(self):
        errors, warnings = [], list(self.notes)
        if self.spec.get("altar") and not self.ground:
            errors.append("ground is off: set \"ground\": true so the bundle writes terrain under the build; "
                          "without it the floor, yard and furniture hang over every dip of the real site")
        errors += self.check_altar()
        errors += self.check_stairs()
        door_errors, door_warnings = self.check_doors()
        errors += door_errors
        warnings += door_warnings
        errors += self.check_holes()
        errors += self.check_floating()
        errors += self.check_routes()
        taken = {}
        for f in self.furniture():
            if f.get("placed"):
                continue
            name = f.get("id") or f.get("item")
            cells = list(_box(_vec(f["at"], 3, f"{name}.at"), _vec(f.get("size", [1, 1, 1]), 3, f"{name}.size")))
            for c in cells:
                if c in taken and not f.get("walkable"):
                    errors.append(f"{name}: overlaps {taken[c]} at {c}")
                    break
                taken.setdefault(c, name)
            if f.get("group") in USED_GROUPS:
                errors += self._check_use(f, name, cells)
            blocked = [c for c in cells + self._hung_cells(f) if c in self.voxels]
            if blocked:
                errors.append(f"{name}: overlaps blocks at {list(blocked[0])}")
            errors += self._check_mount(f, name, cells, warnings)
            if f.get("needs_shelter"):
                bare = [c for c in cells if not self.covered(*c)]
                if bare:
                    errors.append(f"{name}: open sky above {bare[0]} (no roof)")
                if self.open_to_outside(cells):
                    errors.append(f"{name}: room is open to outside at floor level (gap or doorway without door)")
            clear = [c for c in cells if c[2] == cells[0][2] and any((c[0], c[1], c[2] + h) in self.voxels for h in range(HEADROOM))]
            if f.get("walkable") and clear:
                warnings.append(f"{name}: less than {HEADROOM * VOXEL_M} m headroom at {clear[0]}")
        return errors, warnings

    def _check_mount(self, f, name, cells, warnings):
        """A prop must touch what holds it, like the game's snapping: floor, wall or ceiling."""
        mount = f.get("mount", "floor")
        x, y, z = f["at"]
        if mount == "wall":
            fx, fy = FRONT[f.get("rot", 0)]
            back = [(c[0] - fx, c[1] - fy, z) for c in cells if c[2] == z
                    and (c[0] - fx, c[1] - fy, z) not in {(a, b, z) for a, b, _ in cells}]
            gap = [c for c in back if c not in self.voxels]
            if not back or gap:
                return [f"{name}: wall prop is not on a wall (no block behind it at {list(gap[0]) if gap else [x, y, z]}; "
                        f"turn it with rot so its back faces the wall, or move it)"]
            return []
        if mount == "ceiling":
            cx, cy = x + f["size"][0] // 2, y + f["size"][1] // 2
            if not any((cx, cy, k) in self.voxels for k in range(z, z + 17)):
                return [f"{name}: ceiling prop has no ceiling above {[cx, cy, z]} within 8 m"]
            return []
        floor = [c for c in cells if c[2] == z]
        held = [c for c in floor if self.solid(c[0], c[1], z - 1)]
        centre = (x + f["size"][0] // 2, y + f["size"][1] // 2, z)
        if not self.solid(centre[0], centre[1], z - 1) or len(held) * 2 < len(floor):
            return [f"{name}: hangs in the air (no floor under {list(centre)}; it must stand on a block or ground)"]
        if len(held) < len(floor):
            warnings.append(f"{name}: not fully standing on blocks")
        return []

    def _check_use(self, f, name, cells):
        dirs = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}
        if f.get("use") not in dirs:
            return [f"{name}: set use to the side the player stands on ({', '.join(dirs)})"]
        dx, dy = dirs[f["use"]]
        z = cells[0][2]
        xs, ys = [c[0] for c in cells], [c[1] for c in cells]
        if dx:
            edge = max(xs) if dx > 0 else min(xs)
            front = [(edge + dx * i, y) for i in range(1, USE_CLEARANCE + 1) for y in set(ys)]
        else:
            edge = max(ys) if dy > 0 else min(ys)
            front = [(x, edge + dy * i) for i in range(1, USE_CLEARANCE + 1) for x in set(xs)]
        bad = [c for c in front if not self.walkable(*c, z)]
        return [f"{name}: no {USE_CLEARANCE * VOXEL_M} m of clear floor on its {f['use']} side at {list(bad[0]) + [z]}"] if bad else []

    def counts(self):
        return Counter(self.materials[m] for m in self.voxels.values())

    # --- output -------------------------------------------------------------------

    def layer(self, z):
        (x0, y0, _), (x1, y1, _) = self.bbox()
        keys = sorted(set(self.materials))
        glyph = {k: (self.spec.get("glyphs") or {}).get(k, chr(ord("A") + i)) for i, k in enumerate(keys)}
        furn = {}
        for f in self.furniture():
            for c in _box(f["at"], f.get("size", [1, 1, 1])):
                furn[c] = f.get("glyph", "*")
        rows = []
        for y in range(y1 + 1, y0 - 2, -1):  # north up
            row = ""
            for x in range(x0 - 1, x1 + 2):
                c = (x, y, z)
                row += glyph[self.voxels[c]] if c in self.voxels else "D" if c in self.doors else furn.get(c, ".")
            rows.append(row)
        legend = ", ".join(f"{g}={k}" for k, g in glyph.items())
        return f"z={z} ({z * VOXEL_M:+.1f} m)  {legend}, D=door, .=air\n" + "\n".join(rows)

    def export(self):
        runs = []
        for (x, y, z), m in sorted(self.voxels.items(), key=lambda kv: (kv[0][2], kv[0][1], kv[0][0])):
            last = runs[-1] if runs else None
            if last and last[1] == y and last[2] == z and last[4] == m and last[0] + last[3] == x:
                last[3] += 1
            else:
                runs.append([x, y, z, 1, m])
        return {
            "schema": VOXEL_SCHEMA,
            "name": self.spec.get("name"),
            "voxel_m": VOXEL_M,
            "axes": "x=east, y=north, z=up; origin is the design's (0,0,0)",
            "materials": self.materials,
            "altar": self.spec.get("altar"),
            "placements": self.placements,
            "runs_x": runs,
            "furniture": self.furniture(),
        }

    def scene(self):
        """The built result as a reusable piece (``custodian.enshrouded.scene.v1``)."""
        ids = {m: i for i, m in enumerate(sorted(set(self.voxels.values())))}
        runs = []
        for (x, y, z), m in sorted(self.voxels.items(), key=lambda kv: (kv[0][2], kv[0][1], kv[0][0])):
            last = runs[-1] if runs else None
            if last and last[1] == y and last[2] == z and last[4] == ids[m] and last[0] + last[3] == x:
                last[3] += 1
            else:
                runs.append([x, y, z, 1, ids[m]])
        props = []
        for f in self.furniture():
            if not f.get("prop"):
                continue
            if f.get("placed"):
                props.append({k: f.get(k) for k in ("prop", "pos", "rot_xyzw")} | {"size_m": None})
            else:
                props.append({"prop": f["prop"], "pos": f["pos"], "rot_xyzw": f["rot_xyzw"],
                              "size_m": prop(f["prop"])["size_m"]})
        return {"schema": SCENE_SCHEMA, "name": self.spec.get("name"), "voxel_m": VOXEL_M,
                "axes": "x = game X, y = game Z, z = up; origin = altar platform top corner",
                "altar": self.spec.get("altar"),
                "materials": {str(i): self.materials.get(m, m) for m, i in ids.items()},
                "runs": runs, "props": props}


def template_voxels(name):
    """Blocks a template places (voxel count of its shape)."""
    return len(shape(name)["cells"])


def _report(house):
    errors, warnings = house.check()
    (x0, y0, z0), (x1, y1, z1) = house.bbox()
    size = [(b - a + 1) * VOXEL_M for a, b in ((x0, x1), (y0, y1), (z0, z1))]
    print(f"{house.spec.get('name')}: {size[0]} x {size[1]} x {size[2]} m, {len(house.voxels)} blocks")
    altar = house.spec.get("altar")
    if altar:
        ax, ay, az = ALTAR_BOX[0] // 2, ALTAR_BOX[1] // 2, 0
        edge = ALTAR_CUBE_M[altar.get("level", 1)]
        span = ", ".join(f"{n} {(a - c) * VOXEL_M:+.1f}..{(b - c + 1) * VOXEL_M:+.1f}"
                         for n, a, b, c in (("x", x0, x1, ax), ("y", y0, y1, ay), ("z", z0, z1, az)))
        print(f"  altar level {altar.get('level', 1)}: cube {edge} m (±{edge / 2:g} m); design spans {span} m from altar centre")
    for mat, n in house.counts().most_common():
        raw = RAW_COST.get(mat)
        cost = f"  (~{-(-n // raw[2]) * raw[1]} {raw[0]})" if raw else ""
        print(f"  {n:6d}  {mat}{cost}")
    used = Counter(pl["template"] for pl in house.placements)
    if used:
        print("  templates: " + ", ".join(f"{n} x{c}" for n, c in sorted(used.items())))
    for w in warnings:
        print(f"WARN  {w}")
    for e in errors:
        print(f"ERROR {e}")
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=("check", "plan", "export", "templates"))
    parser.add_argument("design", type=Path, nargs="?")
    parser.add_argument("-z", type=int, action="append", help="plan: layer(s) to draw; default all")
    parser.add_argument("-o", "--out", type=Path, help="export: write voxel JSON here instead of stdout")
    args = parser.parse_args(argv)
    if args.command != "templates" and args.design is None:
        parser.error("design file is required")
    try:
        house = None if args.command == "templates" else House(json.loads(args.design.read_text(encoding="utf-8")))
    except (DesignError, KeyError, json.JSONDecodeError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    if args.command == "templates":
        for n in TEMPLATES:
            t = shape(n)
            w, d, h = t["size"]
            print(f"{n:16s} {w * VOXEL_M:g} x {d * VOXEL_M:g} x {h * VOXEL_M:g} m  {template_voxels(n):4d} voxels  "
                  f"{t['debug']}  ({t['name_ru']})")
        return 0
    if args.command == "check":
        return _report(house)
    if args.command == "plan":
        (_, _, z0), (_, _, z1) = house.bbox()
        for z in args.z or range(z0, z1 + 1):
            print(house.layer(z) + "\n")
        return 0
    text = json.dumps(house.export(), ensure_ascii=False)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
