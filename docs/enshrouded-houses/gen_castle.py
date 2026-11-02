"""Generate castle.json: a Gothic castle around a level 2 altar (80 m cube).

One building, not a row of houses: a tall nave (the great hall, altar in its middle) with a
lower aisle on each side, a high square keep at its back and two octagonal towers flanking the
main portal at the front. Storage is in a vaulted basement under the back of the hall and the
keep.

  * Nave 10 x 23 m, 17 m high, lit by stained-glass lancets of glow stone above the aisle
    roofs; a deep pointed portal, a rose window and a band of glowing runes on the facade.
  * Aisles (west, east), two storeys: craft halls on the ground floor (5 m, the Blast Furnace
    fits), eight bedrooms upstairs off a corridor, reached by the grand stair and a bridge in
    the nave. Buttresses with glowing pinnacles carry the walls.
  * Keep 12 x 12 m, five floors (throne hall, master's chamber, study, guest room, lookout),
    flights with 2 m landings, crowned by a spire and four pinnacles. Rune panels on its faces.
  * Basement 11 x 16 m under the hall's back and the keep: magic chests, stair from the keep.
  * Grounds: curtain wall with a gatehouse and corner towers, a paved approach lined with
    braziers, farm beds in the yards beside the keep, a herb garden with beehives behind the keep.

Palette, taken from the studied Gothic-style bases (Everspire): one main stone (dressed stone)
for 60-70 % of the walls, a dark castle stone plinth, light carved stone for ribs, arches and
trim, polished stone for floors and string courses, dark star stone for inlay panels, palm wood
inside, one dark slate roof. Glow: gold for pinnacle tips, arches and lancets, blue for runes.

  python docs/enshrouded-houses/gen_castle.py docs/enshrouded-houses/castle.json
"""
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import enshrouded_house as eh  # noqa: E402

P, F = [], []

MATS = {
    "stone": "Block_T3_Stone_Fancy_CRAFTING",              # main walls
    "base": "Block_T3_Stone_CastleWall_REWARD",            # plinth, basement
    "trim": "Block_T3_Dungeon_Fancy_REWARD",               # ribs, arches, coping, rails
    "polished": "Block_T4_Stone_HighlyPolished_REWARD",    # floors, string courses
    "star": "Block_T5_Stone_StarMaterial_REWARD",          # dark inlay panels
    "wood": "Block_T4_Wood_Palmwood_CRAFTING",             # upper floors, stairs
    "carpet": "Block_T6_Fabric_CastleCarpet_REWARD",
    "slate": "Block_Roof_T5_Granite_Shingles",             # roofs and spires
    "slatetrim": "Block_Roof_T5_Granite_Ornamented",
    "hedge": "Block_Roof_T6_Palmleaves",
    "gold": "Block_T4_YellowGlow_MemorialHall_CRAFTING",
    "blue": "Block_T1_GlowDirt_Blue_CRAFTING",
}

G, U = 0, 11                    # feet levels of the two aisle storeys
SLAB, TOP = 10, 19              # aisle upper slab and ceiling
AW, AE = -22, 29                # aisle outer walls
NW, NE = -7, 14                 # nave walls
YF, YB = -23, 22                # front wall, back wall (= keep's south wall)
KX0, KX1, KY1 = -8, 15, 45      # keep x range and north wall
NAVE_TOP = 34                   # nave walls rise to here
KEEP_TOP = 46                   # keep walls rise to here (ceiling of the lookout)
BZ = -9                         # basement feet level (floor slab at -10)
CW = (-40, 47, -58, 62)         # curtain wall outer rectangle
USE_ROT = {"+y": 0, "-x": 90, "-y": 180, "+x": 270}
NAVE_DOORS = (-17, -2, 8)       # rows of the ground-floor doors from the nave into the craft halls
CORR = (-14, 21)                # upper corridor walls: corridors 3 m wide, so open doors leave room to pass
ROOM_SPANS = ((YF + 1, -12), (-10, -1), (1, 10), (12, YB - 1))
#: Bedroom doors at the south end, except the last room, whose door would open into the bridge door.
DOOR_AT_NORTH = (False, False, False, True)

RUNES = {   # 5 x 7 glyphs after the Elder Futhark
    "algiz": ["#.#.#", ".###.", "..#..", "..#..", "..#..", "..#..", "..#.."],
    "tiwaz": ["..#..", ".###.", "#.#.#", "..#..", "..#..", "..#..", "..#.."],
    "othala": ["..#..", ".#.#.", "#...#", ".#.#.", "..#..", ".#.#.", "#...#"],
    "dagaz": ["#...#", "##.##", "#.#.#", "#.#.#", "#.#.#", "##.##", "#...#"],
    "ingwaz": ["..#..", ".#.#.", "#...#", "#...#", "#...#", ".#.#.", "..#.."],
    "mannaz": ["#...#", "##.##", "#.#.#", "##.##", "#...#", "#...#", "#...#"],
    "sowilo": ["...#.", "..#..", ".#...", ".###.", "...#.", "..#..", ".#..."],
}


def add(**kw):
    P.append({k: v for k, v in kw.items() if v is not None})


def box(mat, at, size, note=None):
    if min(size) > 0:
        add(op="box", mat=mat, at=list(at), size=list(size), note=note)


def floor(mat, at, size, note=None):
    """A floor box. It runs on under the altar platform as in every real base: a pit carved for
    the platform showed as a hole in game."""
    box(mat, at, size, note)


def inlay(mat, at, rows, wall="x", note=None):
    add(op="inlay", mat=mat, at=list(at), rows=rows, wall=wall, note=note)


def rune_panel(at, wall, names, note):
    """Dark star-stone panel with glowing blue runes, 5 x 7 each, one cell apart."""
    x, y, z = at
    width = 6 * len(names) + 1
    box("star", [x, y, z], [width, 1, 9] if wall == "x" else [1, width, 9], note)
    for i, n in enumerate(names):
        pos = [x + 1 + 6 * i, y, z + 1] if wall == "x" else [x, y + 1 + 6 * i, z + 1]
        inlay("blue", pos, RUNES[n], wall)


def lancet(at, wall, height=9, glass="gold", note=None):
    """Pointed stained-glass lancet of glow stone in a trim frame, 4 wide (inlay in the wall)."""
    frame = [".##."] + ["#..#"] * (height - 2) + ["####"]
    pane = ["...."] + [".##."] * (height - 2) + ["...."]
    pane[1] = "...."
    frame[1] = "#..#".replace("..", "##")
    inlay("trim", at, frame, wall, note)
    inlay(glass, at, pane, wall)


def size_of(prop, rot=0):
    return eh.prop_size(prop, rot)


def thing(prop, at, note, use=None, rot=None, **extra):
    if rot is None and eh.mount_of(prop) == "floor":
        rot = USE_ROT[use] if use else 0
    item = {"id": note, "prop": prop, "at": list(at)}
    if rot is not None:
        item["rot"] = rot
    if use:
        item["use"] = use
    item.update(extra)
    F.append(item)
    return size_of(prop, rot)


def against(prop, room, side, pos, z, note, **extra):
    """Stand a station against a room wall, front to the room; ``pos`` runs along that wall."""
    x0, x1, y0, y1 = room
    use = {"n": "-y", "s": "+y", "w": "+x", "e": "-x"}[side]
    w, d, _ = size_of(prop, USE_ROT[use])
    pos = min(max(pos, x0 if side in "ns" else y0), (x1 - w + 1) if side in "ns" else (y1 - d + 1))
    at = {"n": [pos, y1 - d + 1], "s": [pos, y0], "w": [x0, pos], "e": [x1 - w + 1, pos]}[side]
    return thing(prop, at + [z], note, use, **extra)


#: Wall lamps wait until the blocks exist, then snap onto the nearest wall (see snap_lamps).
LAMPS = []


def lamp(x, y, z, note, prop="Prop_Decoration_Comfort_T4_IlluminationWall", to=None):
    """A lit wall lamp: from (x, y) walk toward ``to`` (or the nearest wall) and hang it there."""
    LAMPS.append((x, y, z, note, prop, to))


def snap_lamps(house):
    """Put every lamp on the face of a real wall, its back to the wall, like the game snaps it."""
    steps = {"+x": (1, 0), "-x": (-1, 0), "+y": (0, 1), "-y": (0, -1)}
    for x, y, z, note, prop, to in LAMPS:
        best = None
        for name, (dx, dy) in steps.items():
            if to and name != to:
                continue
            for k in range(0, 12):
                cx, cy = x + dx * k, y + dy * k
                if (cx, cy, z) in house.voxels:
                    break
                if (cx + dx, cy + dy, z) in house.voxels:
                    if best is None or k < best[0]:
                        best = (k, cx, cy)
                    break
        if best is None:
            raise SystemExit(f"lamp {note} at {x, y, z}: no wall nearby")
        thing(prop, [best[1], best[2], z], note, lit=True)


# --- the building -----------------------------------------------------------------------------

def basement():
    """Storage under the back of the nave and the keep: dug out, lined with dark castle stone."""
    x0, x1, y0, y1 = KX0, KX1, 11, KY1
    add(op="cut", kind="opening", at=[x0, y0, BZ - 1], size=[x1 - x0 + 1, y1 - y0 + 1, -BZ], note="подвал: выемка грунта")
    box("polished", [x0, y0, BZ - 1], [x1 - x0 + 1, y1 - y0 + 1, 1], "подвал: пол")
    add(op="pattern", mat="star", at=[x0 + 1, y0 + 1, BZ - 1], size=[x1 - x0 - 1, y1 - y0 - 1, 1], step=[4, 4, 1],
        cell=[1, 1, 1], note="подвал: узор пола")
    add(op="walls", mat="base", at=[x0, y0, BZ], size=[x1 - x0 + 1, y1 - y0 + 1, -BZ - 1], note="подвал: стены")
    for x in range(x0 + 4, x1 - 2, 6):  # trim ribs on the long walls, like vault springers
        box("trim", [x, y0, BZ], [1, 1, -BZ - 1])
        box("trim", [x, y1, BZ], [1, 1, -BZ - 1])
    rune_panel([x0, 17, BZ + 1], "y", ["algiz", "othala", "ingwaz"], "подвал: руны на западной стене")
    rune_panel([x1, 17, BZ + 1], "y", ["dagaz", "tiwaz", "sowilo"], "подвал: руны на восточной стене")


def floors():
    floor("polished", [AW, YF, -1], [AE - AW + 1, KY1 - YF + 1, 1], "замок: пол первого этажа")
    floor("wood", [AW + 1, YF + 1, -1], [NW - AW - 1, YB - YF - 1, 1], "запад: деревянный пол")
    floor("wood", [NE + 1, YF + 1, -1], [AE - NE - 1, YB - YF - 1, 1], "восток: деревянный пол")
    add(op="pattern", mat="star", at=[NW + 1, YF + 1, -1], size=[NE - NW - 1, YB - YF - 1, 1], step=[4, 4, 1],
        cell=[1, 1, 1], only_on="polished", note="неф: звёздный узор пола")
    floor("star", [-3, -3, -1], [14, 14, 1], "неф: тёмный квадрат у алтаря")
    add(op="walls", mat="gold", at=[-3, -3, -1], size=[14, 14, 1], note="неф: светящаяся рамка вокруг алтаря")
    box("carpet", [1, YF + 1, -1], [6, -3 - YF - 1, 1], "неф: ковровая дорожка от портала к алтарю")
    box("carpet", [1, 11, -1], [6, 3, 1], "неф: ковёр за алтарём")


def walls():
    # Aisles: two storeys of dressed stone on a dark plinth, string courses at the slabs.
    add(op="walls", mat="stone", at=[AW, YF, 0], size=[AE - AW + 1, YB - YF + 1, TOP + 1], note="приделы: стены")
    add(op="walls", mat="base", at=[AW, YF, 0], size=[AE - AW + 1, YB - YF + 1, 2], note="цоколь")
    for z in (SLAB, TOP):
        add(op="walls", mat="polished", at=[AW - 1, YF - 1, z], size=[AE - AW + 3, YB - YF + 3, 1], note="карниз")
    # Nave walls, rising above the aisle roofs.
    for x in (NW, NE):
        box("stone", [x, YF, 0], [1, YB - YF + 1, NAVE_TOP + 1], "стена нефа")
        box("polished", [x - 1, YF, NAVE_TOP], [3, YB - YF + 1, 1], "карниз нефа")
    box("stone", [NW, YF, 0], [NE - NW + 1, 1, NAVE_TOP + 1], "фасад нефа")
    for xa, xb in ((NW, -3), (11, NE)):  # cornice stops either side of the rose window
        box("polished", [xa, YF - 1, NAVE_TOP], [xb - xa + 1, 1, 1], None)
    # Slabs.
    box("wood", [AW + 1, YF + 1, SLAB], [NW - AW - 1, YB - YF - 1, 1], "запад: перекрытие")
    box("wood", [NE + 1, YF + 1, SLAB], [AE - NE - 1, YB - YF - 1, 1], "восток: перекрытие")
    box("trim", [AW + 1, YF + 1, TOP], [NW - AW - 1, YB - YF - 1, 1], "запад: потолок")
    box("trim", [NE + 1, YF + 1, TOP], [AE - NE - 1, YB - YF - 1, 1], "восток: потолок")
    # Ground floor rooms and upper corridors with bedrooms.
    for x0, x1 in ((AW + 1, NW - 1), (NE + 1, AE - 1)):
        for y in (-8, 7):
            box("stone", [x0, y, 0], [x1 - x0 + 1, 1, 10], None)
    for cw, rx0, rx1 in ((CORR[0], AW + 1, CORR[0] - 1), (CORR[1], CORR[1] + 1, AE - 1)):
        box("stone", [cw, YF + 1, U], [1, YB - YF - 1, 8], None)
        for y in (-11, 0, 11):
            box("stone", [rx0, y, U], [rx1 - rx0 + 1, 1, 8], None)


def nave_inside():
    # Bridge across the nave and the landing over the grand stair (as tested in the estate).
    box("wood", [NW + 1, 8, SLAB], [NE - NW - 1, 6, 1], "неф: мост между приделами")
    # The upper floors are reached from the keep: a door from its second floor opens onto this
    # landing, which joins the bridge. A grand stair in the nave had no room for 3 m at its foot.
    box("wood", [NW + 1, 14, SLAB], [5, 8, 1], "неф: площадка у двери из донжона")
    box("trim", [NW + 6, 14, U], [1, 8, 2], "неф: перила площадки")
    box("trim", [NW + 1, 8, U], [NE - NW - 1, 1, 2], "неф: перила моста")
    box("trim", [-1, 13, U], [NE + 1, 1, 2], "неф: перила моста (север)")
    add(op="pattern", mat="gold", at=[NW + 1, 8, U + 1], size=[NE - NW - 1, 1, 1], step=[3, 1, 1], only_on="trim",
        note="неф: огни в перилах")
    # Pilasters (light ribs) up the inner nave walls between the lancets.
    for y in range(YF + 1, YB, 7):
        for x in (NW + 1, NE - 1):
            if 8 <= y <= 21 or any(d - 1 <= y <= d + 4 for d in NAVE_DOORS):
                continue  # never in front of a door
            box("trim", [x, y, 0], [1, 1, NAVE_TOP], None)


def roofs():
    add(op="gable", roof="slate", gable="stone", at=[NW, YF, NAVE_TOP + 1], size=[NE - NW + 1, YB - YF + 1],
        ridge="y", overhang=1, cap="trim", note="крыша нефа")
    add(op="shed", roof="slate", gable="stone", at=[AW, YF, TOP + 1], size=[NW - AW, YB - YF + 1], down="-x",
        run=2, overhang=1, note="крыша западного придела")
    add(op="shed", roof="slate", gable="stone", at=[NE + 1, YF, TOP + 1], size=[AE - NE, YB - YF + 1], down="+x",
        run=2, overhang=1, note="крыша восточного придела")


def buttresses():
    """Stepped buttresses along the aisles with pinnacles tipped in gold glow."""
    for y in (-22, -15, -8, -1, 6, 13, 20):
        for x, out in ((AW, -1), (AE, 1)):
            bx = x + out if out > 0 else x - 2
            box("stone", [bx, y, 0], [2, 2, 15], None)
            box("base", [bx, y, 0], [2, 2, 2], None)
            box("trim", [bx, y, 15], [2, 2, 1], None)
            px = x + out
            box("stone", [px, y, 16], [1, 2, 6], None)
            box("trim", [px, y, 22], [1, 2, 1], None)
            box("gold", [px, y, 23], [1, 2, 1], "контрфорс: светящаяся вершина")


def keep():
    x0, x1, y0, y1 = KX0, KX1, YB, KY1
    add(op="walls", mat="stone", at=[x0, y0, 0], size=[x1 - x0 + 1, y1 - y0 + 1, KEEP_TOP + 1], note="донжон")
    add(op="walls", mat="base", at=[x0, y0, 0], size=[x1 - x0 + 1, y1 - y0 + 1, 2], note=None)
    for z in (SLAB, 19, 28, 37, KEEP_TOP):
        box("wood" if z < KEEP_TOP else "trim", [x0 + 1, y0 + 1, z], [x1 - x0 - 1, y1 - y0 - 1, 1], f"донжон: перекрытие {z}")
        # String course on the three outside faces (the south face is inside the nave).
        box("polished", [x0 - 1, y0, z], [1, y1 - y0 + 2, 1])
        box("polished", [x1 + 1, y0, z], [1, y1 - y0 + 2, 1])
        box("polished", [x0, y1 + 1, z], [x1 - x0 + 1, 1, 1])
    # Corner pilasters of light stone the full height.
    for cx in (x0, x1):
        for cy in (y0, y1):
            box("trim", [cx, cy, 2], [1, 1, KEEP_TOP - 2], None)
    # Battlement, pinnacles and the spire.
    add(op="battlement", mat="stone", at=[x0 - 1, y0 - 1, KEEP_TOP + 1], size=[x1 - x0 + 3, y1 - y0 + 3, 2], merlon=2, gap=2)
    for cx, cy in ((x0 - 1, y0 - 1), (x1 - 2, y0 - 1), (x0 - 1, y1 - 2), (x1 - 2, y1 - 2)):
        box("stone", [cx, cy, KEEP_TOP + 1], [4, 4, 4], None)
        add(op="spire", mat="slate", trim="slatetrim", at=[cx, cy, KEEP_TOP + 5], size=4, steep=3, shape="square",
            finial=2, finial_mat="gold", note="донжон: угловая башенка")
    add(op="spire", mat="slate", trim="slatetrim", at=[x0 + 4, y0 + 4, KEEP_TOP + 1], size=x1 - x0 - 7, steep=3,
        shape="octagon", finial=3, finial_mat="gold", note="донжон: шпиль")
    # Flights between floors, alternating sides, holes in the slabs above each run.
    # The hole stops one row short of the last step: that step is part of the floor above.
    # The first flight runs west along the south wall from the door's landing; the upper ones hug
    # the west and east walls, leaving a 3 m hall down the middle of every floor.
    # Every flight has 3 m of free floor past its foot and 2 m past its top.
    flights = [((8, 23, 0), "-x", 11, (-1, 23, SLAB), (10, 8)), ((7, 38, 11), "-y", 9, (7, 31, 19), (8, 8)),
               ((-7, 29, 20), "+y", 9, (-7, 29, 28), (8, 8)), ((7, 38, 29), "-y", 9, (7, 31, 37), (8, 8))]
    for i, (at, d, rise, hole, (w, n)) in enumerate(flights, 1):
        add(op="flight", mat="wood", at=list(at), dir=d, rise=rise, note=f"донжон: марш {i}")
        add(op="cut", kind="opening", at=list(hole), size=[w, n, 1], note=f"донжон: проём над маршем {i}")
    # Basement stair from the keep's ground floor, with a rail round its well.
    # It runs east along the north wall, its well clear of the first flight (in game the two
    # side by side left a squeeze past the well); one opens off the hall from each side.
    add(op="cut", kind="opening", at=[-1, 37, -1], size=[8, 8, 1], note="донжон: проём над лестницей в подвал")
    add(op="flight", mat="wood", at=[-1, 37, BZ], dir="+x", rise=-BZ, note="лестница в подвал")
    box("trim", [-2, 36, 0], [9, 1, 2], "донжон: перила у лестницы в подвал")
    box("trim", [-2, 37, 0], [1, 8, 2], None)
    # Rune panels on the three free faces, between the windows.
    rune_panel([x0, 28, 30], "y", ["algiz", "othala"], "донжон: руны (запад)")
    rune_panel([x1, 28, 30], "y", ["tiwaz", "dagaz"], "донжон: руны (восток)")
    rune_panel([-2, y1, 30], "x", ["ingwaz", "mannaz"], "донжон: руны (север)")


def front_towers():
    for tx in (-20, 16):
        add(op="tower", mat="stone", at=[tx, -35, 0], size=12, height=40, floor="polished", note="башня у портала")
        add(op="tower", mat="base", at=[tx, -35, 0], size=12, height=2, note=None)
        add(op="tower", mat="polished", at=[tx - 1, -36, SLAB], size=14, height=1, note=None)
        add(op="tower", mat="polished", at=[tx - 1, -36, 30], size=14, height=1, note=None)
        box("trim", [tx + 1, -34, SLAB], [10, 10, 1], None)
        # Octagonal parapet on the cornice ring: a square battlement would hang its corners in the air.
        add(op="tower", mat="stone", at=[tx - 1, -36, 40], size=14, height=2, note=None)
        add(op="spire", mat="slate", trim="slatetrim", at=[tx + 1, -34, 40], size=10, steep=3, shape="octagon",
            finial=3, finial_mat="gold", note="башня у портала: шпиль")
        rune_panel([tx + 3, -35, 21], "x", ["sowilo"], "башня: руна")
        for z in (12, 32):
            lancet([tx + 4, -35, z], "x", height=7, glass="gold")
        # Door from the aisle into the tower's ground room.
        door_x = tx + 4
        add(op="cut", kind="door", at=[door_x, -24, 0], size=[4, 1, 5], note="проход из придела в башню")
        add(op="door", at=[door_x, YF, 0], wall="x", note="дверь в башню")


def facade():
    # Pointed portal: three archivolts stepping out from the door, gold glow on the inner arch.
    add(op="double_door", at=[0, YF, 0], wall="x", note="главный вход")
    for k in (1, 2, 3):
        y = YF - k
        w0, w1 = -1 - k, 7 + k
        box("trim" if k % 2 else "stone", [w0, y, 0], [w1 - w0 + 1, 1, 9 + 2 * k], f"портал: арка {k}")
        # Opening: rectangle then a pointed head, one cell narrower each row.
        add(op="cut", kind="opening", at=[w0 + 1, y, 0], size=[w1 - w0 - 1, 1, 7 + k])
        for r in range(1, 4):
            if w1 - w0 - 1 - 2 * r > 0:
                add(op="cut", kind="opening", at=[w0 + 1 + r, y, 6 + k + r], size=[w1 - w0 - 1 - 2 * r, 1, 1])
    box("gold", [-1, YF - 1, 8], [9, 1, 1], "портал: светящаяся арка")
    box("trim", [-5, YF - 3, 15], [17, 3, 1], "портал: карниз")
    add(op="battlement", mat="trim", at=[-5, YF - 3, 16], size=[17, 3, 1], merlon=1, gap=1)
    # Rune band over the portal and the rose window above it. Everything on the facade is
    # centred on the door (x = 3.5, cell edges): in game an off-centre band looked shifted.
    rune_panel([-6, YF, 18], "x", ["algiz", "othala", "tiwaz"], "фасад: руны над порталом")
    rose(3.5, 32, 6.5)
    # Pinnacles at the facade corners, tipped with gold glow.
    for x in (NW - 1, NE):
        box("trim", [x, YF - 1, NAVE_TOP + 1], [2, 2, 8], "фасад: пинакль")
        add(op="spire", mat="slate", trim="slatetrim", at=[x, YF - 1, NAVE_TOP + 9], size=2, steep=2, shape="square",
            finial=1, finial_mat="gold")


def rose(cx, cz, r):
    """Rose window on the facade plane: carved ring, star-stone tracery, blue spokes, gold heart."""
    masks = {"trim": [], "star": [], "blue": [], "gold": []}
    x0, z0 = int(cx - r - 1), int(cz - r - 1)
    n = int(2 * r) + 3
    for z in range(z0 + n - 1, z0 - 1, -1):
        rows = {k: "" for k in masks}
        for x in range(x0, x0 + n):
            dx, dz = x + .5 - cx, z + .5 - cz
            d = math.hypot(dx, dz)
            ang = math.degrees(math.atan2(dz, dx)) % 45
            spoke = ang < 9 or ang > 36
            pick = ("trim" if r - 1 <= d <= r + .2 else "gold" if d <= 1.6 else "blue" if d <= 2.6
                    else "blue" if d < r - 1 and spoke else "star" if d < r - 1 else None)
            for k in masks:
                rows[k] += "#" if k == pick else "."
        for k in masks:
            masks[k].append(rows[k])
    for k, rows in masks.items():
        inlay(k, [x0, YF, z0], rows, "x", "фасад: роза" if k == "trim" else None)


def windows():
    # Real windows (game frames) in the aisle walls; stained-glass lancets in the clerestory.
    for x in (AW, AE):
        for y in (-19, -12, -5, 2, 9, 16):
            add(op="window", at=[x, y, G + 3], wall="y")
            box("trim", [x, y - 1, G + 6], [1, 4, 1], None)
        for y in (-18, -6, 4, 16):
            add(op="window", at=[x, y, U + 2], wall="y")
            box("trim", [x, y - 1, U + 5], [1, 4, 1], None)
    for x in (NW, NE):
        for y in (-20, -13, -6, 1, 15):
            lancet([x, y, 25], "y", height=9)
    for x in (KX0, KX1):
        for y in (25, 41):
            for z in (G + 3, U + 2, 22, 31):
                add(op="window", at=[x, y, z], wall="y")
            lancet([x, y - 1, 38], "y", height=7, glass="gold")
    for x in (-4, 10):
        for z in (G + 3, U + 2, 22, 31):
            if not (x == 10 and z == G + 3):    # the garden door is under it
                add(op="window", at=[x, KY1, z], wall="x")
        lancet([x - 1, KY1, 38], "x", height=7, glass="gold")


def doors():
    for x in (NW, NE):
        for y in NAVE_DOORS:
            add(op="door", at=[x, y, G], wall="y", note="дверь из нефа в мастерскую")
        add(op="door", at=[x, 9, U], wall="y", note="дверь с моста в коридор")
    for cw in CORR:
        for (y0, y1), north in zip(ROOM_SPANS, DOOR_AT_NORTH):
            add(op="door", at=[cw, y1 - 3 if north else y0, U], wall="y", note="дверь спальни")
    add(op="door", at=[10, YB, G], wall="x", note="дверь из нефа в донжон")
    add(op="door", at=[NW + 1, YB, U], wall="x", note="дверь из донжона на мост нефа")
    add(op="door", at=[9, KY1, G], wall="x", note="дверь из донжона в сад")


# --- grounds -----------------------------------------------------------------------------------

def grounds():
    x0, x1, y0, y1 = CW
    for k in (0, 1):
        add(op="walls", mat="stone", at=[x0 + k, y0 + k, 0], size=[x1 - x0 + 1 - 2 * k, y1 - y0 + 1 - 2 * k, 8],
            note="крепостная стена" if not k else None)
    add(op="walls", mat="base", at=[x0, y0, 0], size=[x1 - x0 + 1, y1 - y0 + 1, 2], note=None)
    for k in (0, 1):
        add(op="walls", mat="trim", at=[x0 + k, y0 + k, 8], size=[x1 - x0 + 1 - 2 * k, y1 - y0 + 1 - 2 * k, 1], note=None)
    add(op="battlement", mat="stone", at=[x0, y0, 9], size=[x1 - x0 + 1, y1 - y0 + 1, 2], merlon=2, gap=2)
    for cx, cy in ((x0 - 2, y0 - 2), (x1 - 7, y0 - 2), (x0 - 2, y1 - 7), (x1 - 7, y1 - 7)):
        add(op="tower", mat="stone", at=[cx, cy, 0], size=10, height=14, note="угловая башня стены")
        add(op="tower", mat="base", at=[cx, cy, 0], size=10, height=2, note=None)
        box("trim", [cx + 1, cy + 1, 13], [8, 8, 1], None)
        add(op="spire", mat="slate", trim="slatetrim", at=[cx, cy, 14], size=10, steep=2, shape="octagon",
            finial=2, finial_mat="gold", note="угловая башня: шпиль")
    # Gatehouse: pointed arch between two octagonal towers.
    gx, gw = -2, 12
    add(op="cut", kind="opening", at=[gx, y0, 0], size=[gw, 2, 9], note="ворота")
    for r in range(1, 4):
        add(op="cut", kind="opening", at=[gx + r, y0, 8 + r], size=[gw - 2 * r, 2, 1])
    box("gold", [gx, y0 - 1, 9], [gw, 1, 1], "ворота: светящаяся арка")
    box("stone", [gx, y0, 13], [gw, 2, 3], None)
    add(op="battlement", mat="stone", at=[gx, y0, 16], size=[gw, 2, 2], merlon=1, gap=1)
    for tx in (gx - 8, gx + gw):
        add(op="tower", mat="stone", at=[tx, y0 - 2, 0], size=8, height=18, note="надвратная башня")
        add(op="tower", mat="base", at=[tx, y0 - 2, 0], size=8, height=2, note=None)
        # Floor at the wall-walk level: the walk runs into the tower, its shaft must not be open.
        add(op="tower", mat="trim", at=[tx, y0 - 2, 8], size=8, height=1, note=None)
        box("trim", [tx + 1, y0 - 1, 8], [6, 6, 1], "надвратная башня: пол на уровне стены")
        add(op="spire", mat="slate", trim="slatetrim", at=[tx, y0 - 2, 18], size=8, steep=2, shape="octagon",
            finial=2, finial_mat="gold", note="надвратная башня: шпиль")
    # Approach: paved alley from the gate to the portal, braziers and flowers along it.
    ay0, ay1 = y0 + 2, YF - 4
    box("polished", [gx, ay0, -1], [gw, ay1 - ay0 + 1, 1], "дорога к порталу")
    add(op="pattern", mat="star", at=[gx, ay0, -1], size=[gw, ay1 - ay0 + 1, 1], step=[1, 6, 1], note=None)
    for x in (gx - 1, gx + gw):
        add(op="pattern", mat="gold", at=[x, ay0, -1], size=[1, ay1 - ay0 + 1, 1], step=[1, 3, 1],
            note="дорога: золотые огни по краю")
    box("polished", [-24, -38, -1], [56, 11, 1], "площадь перед замком")
    for y in range(ay0 + 7, -40, 7):
        for px in (gx - 4, gx + gw + 2):
            box("trim", [px, y, 0], [2, 2, 1], None)
            thing("Prop_Decoration_Comfort_T1_IlluminationFloor_Brazier", [px, y, 1], "Дорога: жаровня", walkable=True)
            thing("Flower_Pot_Long_Ground_Red", [px, y + 3, 0], "Дорога: цветы", walkable=True)
    for sx in (-31, 34):
        thing("Prop_Decoration_T3_Statue_Underworld", [sx, -37, 0], "Площадь: статуя")
    # Side yards: farm beds facing the castle; garden behind the keep.
    # The side yards along the aisles are only 7 m wide between the buttresses; a 5.5 m bed there
    # left a 1.5 m squeeze. The beds stand in the open yards beside the keep instead.
    for x, y in ((-38, 26), (-23, 26)):
        thing("Prop_Production_Farmbed_Empty", [x, y, 0], "Огород: грядка", use="+x")
    for x, y in ((35, 26), (20, 26)):
        thing("Prop_Production_Farmbed_Empty", [x, y, 0], "Огород: грядка", use="-x")
    box("polished", [-6, KY1 + 1, -1], [20, 4, 1], "сад: дорожка у донжона")
    for x in (-28, -22, -16, 22, 28):
        thing("Prop_Production_T4_Beehive", [x, 54, 0], "Сад: улей", use="-y")
    for x in (-24, -16, 16, 24):
        thing("Prop_Production_T1_Seedbed", [x, 48, 0], "Сад: рассадник", use="+y")
    for x in (-26, 33):
        for y in (-20, -5, 10, 25):
            thing("Prop_Decoration_Comfort_T1_IlluminationFloor_StandingTorch", [x, y, 0], "Двор: факел", walkable=True)
    add(op="walls", mat="hedge", at=[-30, 47, 0], size=[67, 12, 2], note="сад: живая изгородь")
    add(op="cut", kind="opening", at=[7, 47, 0], size=[8, 1, 2], note="сад: проход в изгороди")


# --- rooms -------------------------------------------------------------------------------------

STATION = {"needs_shelter": True}


def rooms():
    """Craft halls 7 x 7 m. Stations stand along the walls; the 3 m in front of each door
    (from the nave, from the towers) stay empty so the door opens and people walk in."""
    W, E = (AW + 1, NW - 1), (NE + 1, AE - 1)
    smithy, carpentry, alchemy = (*W, YF + 1, -9), (*W, -7, 6), (*W, 8, YB - 1)
    kitchen, weaving, workshop = (*E, YF + 1, -9), (*E, -7, 6), (*E, 8, YB - 1)
    # Smithy: the tower door is at x -16..-13 on the south wall, the nave door on the east.
    against("Prop_Production_T5_BlastFurnace", smithy, "w", -22, G, "Кузня: доменная печь", **STATION)
    # No T2 smelter: the blast furnace smelts everything it does, and a 7 m hall has no room
    # for both with 2 m to walk between the stations.
    against("Prop_Production_T1_Charcoal_Kiln", smithy, "n", -20, G, "Кузня: углежогная печь", **STATION)
    against("Prop_Production_T1_Forge", smithy, "n", -14, G, "Кузня: горн", **STATION)
    against("Craftingstation_Carpenter_Workbench", carpentry, "w", -6, G, "Плотницкая: верстак плотника", **STATION)
    against("Prop_Production_T2_Table_Saw", carpentry, "s", -15, G, "Плотницкая: пила", **STATION)
    against("Prop_Production_T2_Kiln", carpentry, "n", -21, G, "Плотницкая: обжиговая печь", **STATION)
    against("Prop_Storage_40_T3_Magic", carpentry, "n", -14, G, "Плотницкая: волшебный сундук")
    against("Prop_Production_T3_Alchemy_Station", alchemy, "w", 13, G, "Алхимия: стол алхимика", **STATION)
    against("Prop_Production_T4_Laboratory", alchemy, "n", -20, G, "Алхимия: лаборатория", **STATION)
    against("Prop_Production_T1_Drying_Rack", alchemy, "n", -14, G, "Алхимия: сушилка", **STATION)
    against("Prop_Storage_40_T3_Magic", alchemy, "s", -17, G, "Алхимия: волшебный сундук")
    # Kitchen: the tower door is at x 20..23 on the south wall.
    against("Prop_Decoration_Comfort4_T3_Hearth_FirePlace", kitchen, "n", 15, G, "Кухня: камин", **STATION)
    against("Craftingstation_Farmer_Kitchen", kitchen, "n", 21, G, "Кухня: кухня фермера", **STATION)
    against("Prop_Production_T3_Butter_Churn", kitchen, "e", -15, G, "Кухня: маслобойка", **STATION)
    against("Prop_Production_T6_MedievalGrill", kitchen, "s", 15, G, "Кухня: гриль", **STATION)
    against("Prop_Production_T3_Oven", kitchen, "s", 26, G, "Кухня: печь", **STATION)
    against("Prop_Production_T4_Spinning_Wheel", weaving, "e", -6, G, "Ткацкая: прялка", **STATION)
    against("Prop_Production_T5_CardingMachine_Spinning", weaving, "n", 16, G, "Ткацкая: чесальная машина", **STATION)
    against("Prop_Production_T3_Tanning_Station", weaving, "s", 19, G, "Ткацкая: дубильня", **STATION)
    against("Prop_Production_T2_NPC_Hand_Spindle", weaving, "e", 1, G, "Ткацкая: веретено", **STATION)
    against("Prop_Storage_40_T3_Magic", weaving, "s", 17, G, "Ткацкая: волшебный сундук")
    against("Craftingstation_Workbench", workshop, "e", 11, G, "Мастерская: верстак", **STATION)
    against("Prop_Production_T6_improvedGrinder", workshop, "n", 21, G, "Мастерская: мельница", **STATION)
    against("Prop_Production_T1_HandGrinder", workshop, "n", 17, G, "Мастерская: ручная мельница", **STATION)
    against("Prop_Storage_40_T3_Magic", workshop, "s", 22, G, "Мастерская: волшебный сундук")
    for x, y in ((-15, -10), (-15, 5), (-15, 20), (22, -10), (22, 5), (22, 20)):
        lamp(x, y, G + 6, "мастерская: светильник")
    # Nave: banners on the walls between the pilasters, a throne behind the altar.
    for y in (-14, -7):
        thing("Prop_Decoration_Ancients_Banner_Wall_01", [NW + 1, y, 14], "Неф: знамя", rot=270)
        thing("Prop_Decoration_Ancients_Banner_Wall_01", [NE - 1, y, 14], "Неф: знамя", rot=90)
    thing("Prop_Decoration_Comfort7_T4_Throne", [3, 11, G], "Неф: трон", rot=180)


def bedrooms():
    """Eight bedrooms 3.5 x 5 m off 3 m corridors. The door is at the south end of each room;
    the bed, its nightstand and the chest stand beyond the 3 m the door needs."""
    who = ["Кузнец", "Плотник", "Алхимик", "Охотница", "Фермер", "Бард", "Ткачиха", "Гостевая"]
    rooms_ = [(AW + 1, CORR[0] - 1, a, b) for a, b in ROOM_SPANS] + [(CORR[1] + 1, AE - 1, a, b) for a, b in ROOM_SPANS]
    for (x0, x1, y0, y1), name, north in zip(rooms_, who, DOOR_AT_NORTH * 2):
        west = x0 < 0
        # Bed head to the outer wall (the model's head is its -x end at rot 0), a nightstand at
        # the head; the chest across the room by the corridor wall, facing the door (in game a
        # chest right at the bed was in the way).
        rot, bx, nx, cx = (0, x0, x0, x1 - 2) if west else (180, x1 - 5, x1 - 1, x0)
        if north:   # door at the north end: bed against the south wall
            by, ny, cy, use = y0, y0 + 4, y1 - 5, "+y"
        else:
            by, ny, cy, use = y1 - 3, y1 - 5, y0 + 4, "-y"
        thing("Prop_Decoration_Comfort6_T4_Bed", [bx, by, U], f"{name}: кровать", rot=rot, needs_shelter=True)
        thing("Prop_Decoration_Comfort4_T3_Table_Night", [nx, ny, U], f"{name}: тумбочка", rot=rot)
        thing("Prop_Storage_32_T2_Chest_Wood", [cx, cy, U], f"{name}: сундук", use=use)
        lamp(x0 + 3, y0 if north else y1, U + 5, f"{name}: светильник", to="-y" if north else "+y")
    for cx in (CORR[0] + 3, CORR[1] - 3):
        for y in (-16, -4, 6, 18):
            lamp(cx, y, U + 4, "Коридор: свеча", "Prop_Decoration_Comfort_T1_IlluminationWall_Candle")


def keep_rooms():
    thing("Prop_Decoration_Ancients_Banner_Wall_01", [6, 44, 6], "Донжон: знамя", rot=180)
    # Master's chamber.
    thing("Prop_Decoration_Comfort6_T4_Bed", [-6, 40, U], "Хозяин: кровать", needs_shelter=True)
    thing("Prop_Decoration_Comfort4_T3_Hearth_FirePlace", [1, 42, U], "Хозяин: камин", use="-y")
    thing("Prop_Storage_56_T4_Box", [-7, 33, U], "Хозяин: сундук", use="+x")
    # Study.
    for x in (-2, 2, 6):
        thing("Prop_Decoration_T2_Shelf_Ornamented_Wooden", [x, 44, 23], "Кабинет: полка", rot=180)
    thing("Prop_Decoration_Comfort5_T4_Table_Large", [-1, 39, 20], "Кабинет: стол")
    thing("Prop_Decoration_T4_Clutter_Globe", [2, 23, 20], "Кабинет: глобус")
    # Guest room.
    thing("Prop_Decoration_Comfort6_T4_Bed", [-6, 23, 29], "Гостевая башни: кровать", needs_shelter=True)
    thing("Prop_Decoration_Comfort6_T4_Bed", [1, 23, 29], "Гостевая башни: кровать 2", needs_shelter=True)
    # Lookout.
    thing("Prop_Decoration_Comfort_T1_IlluminationCeiling_Chandelier", [2, 37, 44], "Смотровая: люстра", lit=True)
    for z in (G, U, 20, 29):
        lamp(-7, 38, z + 5, "Донжон: светильник", to="-x")
    # Basement storage: rows of magic chests along an aisle, lamps on the ribs.
    n = 0
    for y in range(13, 33, 4):   # rows end 1.5 m short of the stair foot
        for x in (-7, -1):    # a 2 m aisle between the rows
            n += 1
            thing("Prop_Storage_40_T3_Magic", [x, y, BZ], f"Хранилище: сундук {n}", "+x" if x < -3 else "-x")
    for y in (13, 17, 21, 25, 29):
        n += 1
        thing("Prop_Storage_40_T3_Magic", [12, y, BZ], f"Хранилище: сундук {n}", "-x")
    for x in (-7, 14):
        for y in (14, 30, 40):
            lamp(x, y, BZ + 5, "Подвал: светильник")


def spec():
    basement()
    floors()
    walls()
    nave_inside()
    roofs()
    buttresses()
    keep()
    front_towers()
    facade()
    windows()
    doors()
    grounds()
    rooms()
    bedrooms()
    keep_rooms()
    snap_lamps(eh.House({"schema": eh.SCHEMA, "materials": MATS, "parts": P, "ground": True}))
    return {
        "schema": eh.SCHEMA,
        "name": "custodian-castle-v1",
        "title": "Готический замок Хранителя",
        "description": "Неф с алтарём, приделы с мастерскими и спальнями, донжон со шпилем, две башни у портала, подвал-хранилище.",
        "notes": [
            "Алтарь в центре нефа высотой 17 м; к нему от ворот ведёт прямая дорога через стрельчатый портал.",
            "Приделы, 1 этаж 5 м: кузня, плотницкая, алхимия (запад); кухня, ткацкая, мастерская (восток). Перед каждой дверью 3 м свободного пола.",
            "2 этаж приделов: 8 спален ремесленников вдоль коридоров шириной 3 м; вход по парадной лестнице и мосту в нефе.",
            "Донжон 12×12 м: тронный зал, покои хозяина, кабинет, гостевая, смотровая; марши с площадками 2 м.",
            "Подвал под донжоном: хранилище с волшебными сундуками, лестница из донжона.",
            "Свет: золотые витражи-ланцеты и вершины контрфорсов, синие руны на тёмных панелях, роза над порталом.",
        ],
        "altar": {"at": [0, 0, 0], "level": 2},
        "ground": True,
        "materials": MATS,
        "levels": [["Подвал", -3], ["1 этаж", 6], ["2 этаж", 17], ["Донжон 3", 26], ["Донжон 4", 35],
                   ["Смотровая", 44], ["Всё", 80]],
        "parts": P,
        "furniture": F,
    }


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("castle.json"))
    out.write_text(json.dumps(spec(), ensure_ascii=False, indent=1), encoding="utf-8")
    house = eh.House(json.loads(out.read_text(encoding="utf-8")))
    errors, warnings = house.check()
    (x0, y0, z0), (x1, y1, z1) = house.bbox()
    print(f"{len(house.voxels)} blocks, {len(house.furniture())} props, {len(errors)} errors, {len(warnings)} warnings; "
          f"bbox {x1 - x0 + 1}x{y1 - y0 + 1}x{z1 - z0 + 1} = {(x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1)} cells")
    for e in errors:
        print("ERROR", e)
    for w in warnings:
        if "standing" not in w and "headroom at" not in w:
            print("warn ", w)
