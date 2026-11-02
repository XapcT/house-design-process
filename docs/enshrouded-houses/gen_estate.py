"""Generate estate.json: a manor house with craft halls inside a walled estate (level 2 altar, 80 m).

Layout (north up, 1 voxel = 0.5 m, altar platform top = x 0..7, y 0..7, z 0):
  * The manor, 32 x 22 m, is centred on the altar. Its middle is a double-height entrance hall
    with the altar in the centre; the grand stair and a gallery bridge are at the back of it.
  * West wing, ground floor: smithy, carpentry, storage hall. East wing: kitchen and dining,
    alchemy, weaving and tannery. Every room opens straight onto the altar hall.
  * Upper floor: eight bedrooms (craftspeople and the master) off two corridors, reached over
    the gallery bridge. Every bed is in a closed, roofed room.
  * Four corner towers, three gabled roofs, brick ground floor and timber-frame upper floor,
    polished stone plinth and cornices, glow-stone inlays.
  * The estate wall (70 x 70 m) with corner towers and a gatehouse. In front: a paved alley from
    the gate to the porch, lined with hedges, braziers and flowers. Behind: a garden with farm
    beds, beehives, hedges and a pavilion.

Run:
  python docs/enshrouded-houses/gen_estate.py docs/enshrouded-houses/estate.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import enshrouded_house as eh  # noqa: E402

P, F = [], []

MATS = {
    "brick": "Block_T2_Loam_FiredBricks_CRAFTING",
    "frame": "Block_T2_Wood_Timberframe_REWARD",
    "framebrick": "Block_T2_Wood_TimberframeBrick_REWARD",
    "polished": "Block_T4_Stone_HighlyPolished_REWARD",
    "fancy": "Block_T3_Stone_Fancy_CRAFTING",
    "castle": "Block_T3_Stone_CastleWall_REWARD",
    "citywall": "Block_T2_Stone_Nation01_Citywall01_REWARD",
    "granite": "Block_T5_Stone_Granite_Polished_REWARD",
    "marble": "Block_T2_Dungeon_Marble_HallowHalls_REWARD",
    "floor": "Block_T2_Wood_Fancy_CRAFTING",
    "palm": "Block_T4_Wood_Palmwood_CRAFTING",
    "paving": "Block_T4_SandStone_Polished_REWARD",
    "sandstone": "Block_T4_SandStone_Sandstone_Fancy_CRAFTING",
    "carpet": "Block_T6_Fabric_CastleCarpet_REWARD",
    "roof": "Block_Roof_T2_Loam_FiredShingle_CRAFTING",
    "towerroof": "Block_Roof_T5_Granite_Ornamented",
    "pavroof": "Block_Roof_T6_ShellLime_Castle_WallAndRoof",
    "hedge": "Block_Roof_T6_Palmleaves",
    "glow": "Block_T1_GlowDirt_WarmWhite_CRAFTING",
    "gold": "Block_T4_YellowGlow_MemorialHall_CRAFTING",
    "blue": "Block_T1_GlowDirt_Blue_CRAFTING",
    "green": "Block_T2_GlowDirt_Green_CRAFTING",
}

# Manor outline and storeys.
X0, X1, Y0, Y1 = -28, 35, -18, 25          # outer walls
PW, PE = -7, 14                            # hall walls (west / east)
G, U = 0, 11                               # feet levels: ground (5 m storey), upper (4 m)
SLAB, TOP = 10, 19                         # upper floor slab, ceiling under the roofs
# Estate wall (2 voxels thick) and the gate.
WX0, WX1, WY0, WY1 = -70, 77, -66, 73
GATE = (-2, 12)                            # gate opening x, width
USE_ROT = {"+y": 0, "-x": 90, "-y": 180, "+x": 270}


def add(**kw):
    P.append({k: v for k, v in kw.items() if v is not None})


def box(mat, at, size, note=None):
    add(op="box", mat=mat, at=list(at), size=list(size), note=note)


def floor(mat, at, size, note=None):
    """A box at floor level that leaves the altar platform (x 0..7, y 0..7) untouched."""
    (x0, y0, z), (sx, sy, sz) = at, size
    x1, y1 = x0 + sx, y0 + sy
    if z > -1 or x1 <= 0 or x0 >= 8 or y1 <= 0 or y0 >= 8:
        return box(mat, at, size, note)
    for bx0, bx1, by0, by1 in ((x0, x1, y0, 0), (x0, x1, 8, y1), (x0, 0, max(y0, 0), min(y1, 8)), (8, x1, max(y0, 0), min(y1, 8))):
        if bx1 > bx0 and by1 > by0:
            box(mat, [bx0, by0, z], [bx1 - bx0, by1 - by0, sz], note)
            note = None


def size_of(prop, rot=0):
    return eh.prop_size(prop, rot)


def thing(prop, at, note, use=None, rot=None, **extra):
    """Furniture by game prop; ``use`` (player side) sets the turn so the front faces the player."""
    if rot is None:
        rot = USE_ROT[use] if use else 0
    item = {"id": note, "prop": prop, "at": list(at), "rot": rot}
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
    at = {"n": [pos, y1 - d + 1], "s": [pos, y0], "w": [x0, pos], "e": [x1 - w + 1, pos]}[side]
    return thing(prop, at + [z], note, use, **extra)


def room_lamp(x, y, z, note):
    thing("Prop_Decoration_Comfort_T4_IlluminationWall", [x, y, z], note, lit=True, walkable=True)


# --- manor shell ------------------------------------------------------------------------------

def manor():
    # Floors flush with the altar platform (z -1), leaving the platform itself.
    floor("floor", [X0, Y0, -1], [X1 - X0 + 1, Y1 - Y0 + 1, 1], "особняк: пол первого этажа")
    floor("granite", [PW, Y0, -1], [PE - PW + 1, Y1 - Y0 + 1, 1], "зал: гранитный пол")
    add(op="pattern", mat="marble", at=[PW + 1, Y0 + 1, -1], size=[PE - PW - 1, Y1 - Y0 - 1, 1],
        step=[4, 4, 1], cell=[2, 2, 1], only_on="granite", note="зал: мраморная шахматка")
    floor("marble", [-4, -4, -1], [16, 16, 1], "зал: мраморный круг у алтаря")
    add(op="walls", mat="gold", at=[-4, -4, -1], size=[16, 16, 1], note="зал: светящаяся рамка вокруг алтаря")
    box("carpet", [1, Y0 + 1, -1], [6, -4 - Y0 - 1, 1], "зал: ковровая дорожка к алтарю")
    # Outer walls: brick ground floor on a polished plinth, timber frame upstairs.
    add(op="walls", mat="brick", at=[X0, Y0, 0], size=[X1 - X0 + 1, Y1 - Y0 + 1, 10], note="особняк: кирпичные стены")
    add(op="walls", mat="polished", at=[X0, Y0, 0], size=[X1 - X0 + 1, Y1 - Y0 + 1, 2], note="особняк: цоколь")
    add(op="walls", mat="frame", at=[X0, Y0, U], size=[X1 - X0 + 1, Y1 - Y0 + 1, 8], note="особняк: фахверк второго этажа")
    for z in (SLAB, TOP):
        add(op="walls", mat="fancy", at=[X0 - 1, Y0 - 1, z], size=[X1 - X0 + 3, Y1 - Y0 + 3, 1], note="особняк: карниз")
        add(op="pattern", mat="glow", at=[X0 - 1, Y0 - 1, z], size=[X1 - X0 + 3, Y1 - Y0 + 3, 1],
            step=[4, 4, 1], only_on="fancy", note="особняк: светящиеся точки по карнизу")
    # Quoins: polished stone pilasters on the facades, every hall wall and corner.
    for x in (X0, PW, PE, X1):
        for y in (Y0 - 1, Y1 + 1):
            box("polished", [x - (1 if x == PW else 0), y, 0], [2 if x == PW else 1, 1, TOP], None)
    # Slabs: upper floor over the wings, gallery and bridge in the hall, ceiling under the roofs.
    box("palm", [X0 + 1, Y0 + 1, SLAB], [PW - X0 - 1, Y1 - Y0 - 1, 1], "запад: перекрытие")
    box("palm", [PE + 1, Y0 + 1, SLAB], [X1 - PE - 1, Y1 - Y0 - 1, 1], "восток: перекрытие")
    box("palm", [PW + 1, 11, SLAB], [PE - PW - 1, 6, 1], "зал: мост-галерея между крыльями")
    box("palm", [PW + 1, 17, SLAB], [5, 8, 1], "зал: площадка над лестницей")
    box("fancy", [PW + 1, 11, U], [PE - PW - 1, 1, 2], "зал: перила моста")
    box("fancy", [-1, 16, U], [PE + 1, 1, 2], "зал: перила над лестницей")
    add(op="pattern", mat="gold", at=[PW + 1, 11, U + 1], size=[PE - PW - 1, 1, 1], step=[3, 1, 1],
        only_on="fancy", note="зал: огни в перилах")
    box("fancy", [X0, Y0, TOP], [X1 - X0 + 1, Y1 - Y0 + 1, 1], "особняк: потолок под крышей")
    # Hall walls, both storeys.
    for x in (PW, PE):
        box("brick", [x, Y0 + 1, 0], [1, Y1 - Y0 - 1, 11], None)
        box("framebrick", [x, Y0 + 1, U], [1, Y1 - Y0 - 1, 8], None)
    # Ground floor rooms: two cross walls in each wing.
    for x0, x1 in ((X0 + 1, PW - 1), (PE + 1, X1 - 1)):
        for y in (0, 13):
            box("brick", [x0, y, 0], [x1 - x0 + 1, 1, 10], None)
    # Upper floor: corridor wall and bedroom walls in each wing.
    for cw, rx0, rx1 in ((-12, X0 + 1, -13), (19, 20, X1 - 1)):
        box("framebrick", [cw, Y0 + 1, U], [1, Y1 - Y0 - 1, 8], None)
        for y in (-7, 3, 14):
            box("framebrick", [rx0, y, U], [rx1 - rx0 + 1, 1, 8], None)


def towers():
    """Corner towers 6 x 6 m; below the ceiling only their parts outside the manor are built."""
    for cx, cy in ((X0 - 4, Y0 - 4), (X1 - 7, Y0 - 4), (X0 - 4, Y1 - 7), (X1 - 7, Y1 - 7)):
        cells = [(x, y) for x in range(cx, cx + 12) for y in range(cy, cy + 12)
                 if x in (cx, cx + 11) or y in (cy, cy + 11)]
        for x, y in cells:
            if not (X0 < x < X1 and Y0 < y < Y1):
                box("polished", [x, y, 0], [1, 1, 2], None)
                box("castle", [x, y, 2], [1, 1, TOP - 1], None)
        add(op="walls", mat="castle", at=[cx, cy, TOP + 1], size=[12, 12, 8], note="башня")
        add(op="walls", mat="fancy", at=[cx - 1, cy - 1, TOP + 9], size=[14, 14, 1], note="башня: карниз")
        add(op="pattern", mat="gold", at=[cx, cy, TOP + 4], size=[12, 12, 3], step=[4, 4, 4], cell=[1, 1, 3],
            only_on="castle", note="башня: светящиеся бойницы")
        box("fancy", [cx, cy, TOP + 9], [12, 12, 1], None)
        add(op="gable", roof="towerroof", gable="castle", at=[cx, cy, TOP + 10], size=[12, 12], ridge="y",
            overhang=1, note="башня: шатёр")


def roofs():
    for x0, x1, note in ((X0, PW, "западное крыло"), (PW, PE, "зал"), (PE, X1, "восточное крыло")):
        add(op="gable", roof="roof", gable="frame", at=[x0, Y0, TOP + 1], size=[x1 - x0 + 1, Y1 - Y0 + 1],
            ridge="y", overhang=1, note=f"крыша: {note}")


def openings():
    # Main door with a porch, back door to the garden.
    add(op="double_door", at=[0, Y0, 0], wall="x", note="парадная дверь")
    box("polished", [-3, Y0 - 6, 0], [2, 2, 9], "крыльцо: колонна")
    box("polished", [9, Y0 - 6, 0], [2, 2, 9], "крыльцо: колонна")
    box("fancy", [-4, Y0 - 7, 9], [16, 7, 1], "крыльцо: навес")
    add(op="battlement", mat="fancy", at=[-4, Y0 - 7, 10], size=[16, 7, 1], merlon=1, gap=1)
    add(op="walls", mat="gold", at=[-4, Y0 - 7, 9], size=[16, 7, 1], note="крыльцо: светящаяся кромка")
    box("granite", [-4, Y0 - 7, -1], [16, 7, 1], "крыльцо: пол")
    box("gold", [-1, Y0 - 1, 7], [9, 1, 1], "парадная дверь: светящаяся притолока")
    add(op="door", at=[10, Y1, 0], wall="x", note="дверь в сад")
    # Hall doors into every room, both storeys.
    for x in (PW, PE):
        for y in (-10, 5, 18):
            add(op="door", at=[x, y, G], wall="y", note="дверь из зала")
        add(op="door", at=[x, 12, U], wall="y", note="дверь с моста в коридор")
    add(op="door", at=[PW, 19, U], wall="y", note="дверь с площадки в коридор")
    # Bedroom doors from the corridors.
    for cw in (-12, 19):
        for y in (-14, -3, 7, 18):
            add(op="door", at=[cw, y, U], wall="y", note="дверь спальни")
    # Windows: ground floor sill 1.5 m, upper 1 m, with a stone lintel and a glow dot above.
    south_north = [-18, -14, 21, 25]
    for y in (Y0, Y1):
        for x in south_north:
            window(x, y, "x", G + 3)
            window(x, y, "x", U + 2)
        for x in (-4, 10) if y == Y0 else (-4,):
            window(x, y, "x", U + 2)
    for x in (X0, X1):
        for y in (-7, -3, 5, 9):
            window(x, y, "y", G + 3)
        for y in (-10, -2, 8):
            window(x, y, "y", U + 2)


def window(x, y, wall, z):
    add(op="window", at=[x, y, z], wall=wall)
    lintel = [x - 1, y, z + 3] if wall == "x" else [x, y - 1, z + 3]
    box("fancy", lintel, [4, 1, 1] if wall == "x" else [1, 4, 1], None)
    sill = [x - 1, y, z - 1] if wall == "x" else [x, y - 1, z - 1]
    box("fancy", sill, [4, 1, 1] if wall == "x" else [1, 4, 1], None)


def stair():
    # Grand stair along the back of the hall: up toward the west, 11 steps of 0.5 m.
    add(op="flight", mat="palm", at=[9, 17, G], dir="-x", rise=U - G, note="парадная лестница зала")


# --- rooms and furniture --------------------------------------------------------------------

STATION = {"needs_shelter": True}


def ground_floor():
    smithy, carpentry, storage = (X0 + 1, PW - 1, Y0 + 1, -1), (X0 + 1, PW - 1, 1, 12), (X0 + 1, PW - 1, 14, Y1 - 1)
    kitchen, alchemy, weaving = (PE + 1, X1 - 1, Y0 + 1, -1), (PE + 1, X1 - 1, 1, 12), (PE + 1, X1 - 1, 14, Y1 - 1)
    # Smithy: furnaces along the outer walls.
    against("Prop_Production_T5_BlastFurnace", smithy, "s", -26, G, "Кузня: доменная печь", **STATION)
    against("Prop_Production_T2_Smelter", smithy, "s", -16, G, "Кузня: плавильня", **STATION)
    against("Prop_Production_T1_Forge", smithy, "w", -12, G, "Кузня: горн кузнеца", **STATION)
    against("Prop_Production_T1_Charcoal_Kiln", smithy, "n", -26, G, "Кузня: углежогная печь", **STATION)
    against("Prop_Production_T2_Kiln", smithy, "n", -18, G, "Кузня: обжиговая печь", **STATION)
    against("Prop_Storage_40_T3_Magic", smithy, "n", -13, G, "Кузня: волшебный сундук")
    # Carpentry.
    against("Craftingstation_Carpenter_Workbench", carpentry, "w", 2, G, "Плотницкая: верстак плотника", **STATION)
    against("Prop_Production_T2_Table_Saw", carpentry, "w", 8, G, "Плотницкая: пила", **STATION)
    against("Craftingstation_Workbench", carpentry, "n", -22, G, "Плотницкая: верстак", **STATION)
    against("Prop_Production_T6_improvedGrinder", carpentry, "n", -16, G, "Плотницкая: мельница", **STATION)
    against("Prop_Production_T1_HandGrinder", carpentry, "s", -20, G, "Плотницкая: ручная мельница", **STATION)
    against("Prop_Storage_40_T3_Magic", carpentry, "s", -16, G, "Плотницкая: волшебный сундук")
    # Storage hall: two rows of magic chests with an aisle, shelves on the west wall.
    for i in range(6):
        thing("Prop_Storage_40_T3_Magic", [-22 + 2 * i, 18, G], f"Хранилище: волшебный сундук {i + 1}", "-y")
        thing("Prop_Storage_40_T3_Magic", [-22 + 2 * i, 20, G], f"Хранилище: волшебный сундук {i + 7}", "+y")
    against("Prop_Decoration_T2_Shelf_Ornamented_Wooden", storage, "w", 15, G, "Хранилище: полка")
    against("Prop_Decoration_T2_Cabinet_Large_Ornamented_Wooden", storage, "s", -14, G, "Хранилище: шкаф")
    for i, x in enumerate((-24, -20, -16, -12)):
        against("Prop_Storage_40_T3_Magic", storage, "n", x, G, f"Хранилище: сундук у стены {i + 1}")
    # Kitchen and dining.
    against("Craftingstation_Farmer_Kitchen", kitchen, "e", -16, G, "Кухня: кухня фермера", **STATION)
    against("Prop_Production_T3_Oven", kitchen, "e", -10, G, "Кухня: печь", **STATION)
    against("Prop_Production_T6_MedievalGrill", kitchen, "s", 28, G, "Кухня: гриль", **STATION)
    against("Prop_Production_T3_Butter_Churn", kitchen, "s", 24, G, "Кухня: маслобойка", **STATION)
    against("Prop_Decoration_Comfort4_T3_Hearth_FirePlace", kitchen, "n", 28, G, "Столовая: камин", **STATION)
    against("Prop_Storage_40_T3_Magic", kitchen, "s", 18, G, "Кухня: волшебный сундук")
    thing("Prop_Decoration_Comfort5_T4_Table_Large", [18, -10, G], "Столовая: стол")
    for x in (18, 21, 24):
        thing("Prop_Decoration_Comfort5_T4_Chair", [x, -12, G], "Столовая: стул", rot=0, walkable=True)
        thing("Prop_Decoration_Comfort5_T4_Chair", [x, -5, G], "Столовая: стул", rot=180, walkable=True)
    # Alchemy.
    against("Prop_Production_T3_Alchemy_Station", alchemy, "e", 2, G, "Алхимия: стол алхимика", **STATION)
    against("Prop_Production_T4_Laboratory", alchemy, "n", 18, G, "Алхимия: лаборатория", **STATION)
    against("Prop_Production_T1_Drying_Rack", alchemy, "s", 26, G, "Алхимия: сушилка", **STATION)
    against("Prop_Storage_40_T3_Magic", alchemy, "s", 20, G, "Алхимия: волшебный сундук")
    # Weaving and tannery.
    against("Prop_Production_T4_Spinning_Wheel", weaving, "e", 15, G, "Ткацкая: прялка", **STATION)
    against("Prop_Production_T5_CardingMachine_Spinning", weaving, "n", 22, G, "Ткацкая: чесальная машина", **STATION)
    against("Prop_Production_T3_Tanning_Station", weaving, "n", 28, G, "Ткацкая: дубильня", **STATION)
    against("Prop_Production_T2_NPC_Hand_Spindle", weaving, "e", 20, G, "Ткацкая: веретено", **STATION)
    against("Prop_Storage_40_T3_Magic", weaving, "s", 26, G, "Ткацкая: волшебный сундук")
    # Lamps in every room and the hall.
    for x, y in ((-18, -2), (-18, 11), (-18, 23), (24, -2), (24, 11), (24, 23)):
        room_lamp(x, y, G + 5, "светильник")
    for x, y in ((3, -9), (3, 3)):
        thing("Prop_Decoration_Comfort_T1_IlluminationCeiling_Chandelier", [x, y, TOP - 2], "Зал: люстра", lit=True,
              walkable=True)
    for y in (-14, -2):
        thing("Prop_Decoration_Ancients_Banner_Wall_01", [PW + 1, y, 12], "Зал: знамя", rot=90, walkable=True)
        thing("Prop_Decoration_Ancients_Banner_Wall_01", [PE - 1, y, 12], "Зал: знамя", rot=270, walkable=True)


def bedrooms():
    who = ["Кузнец", "Плотник", "Алхимик", "Хозяин", "Фермер", "Охотница", "Бард", "Гостевая"]
    rooms = [(X0 + 1, -13, y0, y1) for y0, y1 in ((Y0 + 1, -8), (-6, 2), (4, 13), (15, Y1 - 1))]
    rooms += [(20, X1 - 1, y0, y1) for y0, y1 in ((Y0 + 1, -8), (-6, 2), (4, 13), (15, Y1 - 1))]
    for (x0, x1, y0, y1), name in zip(rooms, who):
        west = x0 < 0
        bed_x = x0 + 1 if west else x1 - 6
        thing("Prop_Decoration_Comfort6_T4_Bed", [bed_x, y0, U], f"{name}: кровать", needs_shelter=True)
        thing("Prop_Decoration_Comfort3_T2_Table_Night", [bed_x + 6 if west else bed_x - 3, y0, U], f"{name}: тумбочка")
        against("Prop_Storage_32_T2_Chest_Wood", (x0, x1, y0, y1), "n", x0 + 2 if west else x1 - 4, U, f"{name}: сундук")
        thing("Prop_Decoration_Comfort_T4_Wall_Painting_3", [x0 + 2 if west else x1 - 3, y1, U + 3], f"{name}: картина",
              walkable=True)
        room_lamp(x0 if west else x1, (y0 + y1) // 2, U + 4, f"{name}: светильник")
    for cx in (-10, 16):
        for y in (-12, 0, 12, 22):
            thing("Prop_Decoration_Comfort_T1_IlluminationWall_Candle", [cx, y, U + 4], "Коридор: свеча", lit=True,
                  walkable=True)


# --- estate ---------------------------------------------------------------------------------

def estate_wall():
    add(op="walls", mat="citywall", at=[WX0, WY0, 0], size=[WX1 - WX0 + 1, WY1 - WY0 + 1, 8], note="ограда усадьбы")
    add(op="walls", mat="citywall", at=[WX0 + 1, WY0 + 1, 0], size=[WX1 - WX0 - 1, WY1 - WY0 - 1, 8], note=None)
    add(op="walls", mat="polished", at=[WX0, WY0, 0], size=[WX1 - WX0 + 1, WY1 - WY0 + 1, 1], note="ограда: цоколь")
    for at, size in (([WX0, WY0, 8], [WX1 - WX0 + 1, WY1 - WY0 + 1, 1]), ([WX0 + 1, WY0 + 1, 8], [WX1 - WX0 - 1, WY1 - WY0 - 1, 1])):
        add(op="walls", mat="fancy", at=at, size=size, note="ограда: парапет")
    add(op="battlement", mat="citywall", at=[WX0, WY0, 9], size=[WX1 - WX0 + 1, WY1 - WY0 + 1, 2], merlon=2, gap=2)
    add(op="pattern", mat="blue", at=[WX0 + 1, WY0 + 1, 5], size=[WX1 - WX0 - 1, WY1 - WY0 - 1, 1], step=[8, 8, 1],
        only_on="citywall", note="ограда: голубые огни по внутренней стороне")
    # Corner towers.
    for cx, cy in ((WX0 - 2, WY0 - 2), (WX1 - 7, WY0 - 2), (WX0 - 2, WY1 - 7), (WX1 - 7, WY1 - 7)):
        add(op="walls", mat="castle", at=[cx, cy, 0], size=[10, 10, 13], note="угловая башня ограды")
        box("castle", [cx + 1, cy + 1, 12], [8, 8, 1], None)
        add(op="walls", mat="polished", at=[cx, cy, 0], size=[10, 10, 2], note=None)
        add(op="battlement", mat="castle", at=[cx, cy, 13], size=[10, 10, 2], merlon=2, gap=2)
        add(op="walls", mat="gold", at=[cx, cy, 10], size=[10, 10, 1], note="башня ограды: золотой пояс")
    # Gatehouse: two towers, an arch with a glowing keystone line.
    gx, gw = GATE
    add(op="cut", kind="opening", at=[gx, WY0, 0], size=[gw, 2, 9], note="ворота")
    for tx in (gx - 6, gx + gw):
        add(op="walls", mat="castle", at=[tx, WY0 - 2, 0], size=[6, 8, 15], note="надвратная башня")
        box("castle", [tx + 1, WY0 - 1, 14], [4, 6, 1], None)
        add(op="battlement", mat="castle", at=[tx, WY0 - 2, 15], size=[6, 8, 2], merlon=1, gap=1)
        add(op="walls", mat="gold", at=[tx, WY0 - 2, 11], size=[6, 8, 1], note=None)
    box("castle", [gx, WY0 - 1, 9], [gw, 3, 4], "ворота: арка")
    box("gold", [gx, WY0 - 2, 9], [gw, 1, 1], "ворота: светящаяся кромка арки")
    add(op="battlement", mat="castle", at=[gx, WY0 - 1, 13], size=[gw, 3, 2], merlon=1, gap=1)
    for tx in (gx - 6, gx + gw + 2):
        thing("Prop_Decoration_Ancients_Banner_Wall_01", [tx + 1, WY0 - 3, 6], "Ворота: знамя", walkable=True)


def alley():
    y0, y1 = WY0 + 2, Y0 - 8                     # from inside the gate to the porch
    gx, gw = GATE
    box("paving", [gx, y0, -1], [gw, y1 - y0 + 1, 1], "аллея: мощение")
    add(op="pattern", mat="granite", at=[gx, y0, -1], size=[gw, y1 - y0 + 1, 1], step=[1, 6, 1], cell=[1, 1, 1],
        note="аллея: гранитные поперечные полосы")
    for x in (gx - 1, gx + gw):
        box("glow", [x, y0, -1], [1, y1 - y0 + 1, 1], "аллея: светящаяся кромка")
    # Forecourt before the porch, with a gold star inlay.
    box("paving", [-16, -36, -1], [40, 12, 1], "парадный двор")
    add(op="walls", mat="glow", at=[-16, -36, -1], size=[40, 12, 1], note="парадный двор: светящаяся кромка")
    add(op="pattern", mat="gold", at=[-14, -34, -1], size=[36, 8, 1], step=[6, 4, 1], cell=[1, 1, 1],
        note="парадный двор: золотые вставки")
    # Hedges with gaps, braziers on pedestals and flowers along both sides.
    for hx in (gx - 6, gx + gw + 5):
        for y in range(y0 + 2, y1 - 2, 8):
            box("hedge", [hx, y, 0], [1, 6, 2], "аллея: живая изгородь")
    for y in range(y0 + 4, y1 - 2, 8):
        for px in (gx - 4, gx + gw + 2):
            box("polished", [px, y, 0], [2, 2, 1], None)
            thing("Prop_Decoration_Comfort_T1_IlluminationFloor_Brazier", [px, y, 1], "Аллея: жаровня", walkable=True)
            thing("Flower_Pot_Long_Ground_Red" if y % 16 else "Flower_Pot_Long_Ground_White", [px, y + 3, 0],
                  "Аллея: цветы", walkable=True)
    for sx in (-14, 17):
        thing("Prop_Decoration_T3_Statue_Underworld", [sx, -34, 0], "Двор: статуя")
    # Side yards: lamps along the house.
    for x in (X0 - 10, X1 + 8):
        for y in range(-12, 24, 10):
            thing("Prop_Decoration_Comfort_T1_IlluminationFloor_StandingTorch", [x, y, 0], "Двор: факел",
                  walkable=True)


def garden():
    # Terrace behind the house and the main path to the pavilion.
    box("paving", [-16, Y1 + 1, -1], [40, 6, 1], "сад: терраса")
    add(op="walls", mat="glow", at=[-16, Y1 + 1, -1], size=[40, 6, 1], note=None)
    box("sandstone", [8, Y1 + 7, -1], [8, 58 - Y1 - 7, 1], "сад: центральная дорожка")
    for x in (7, 16):
        add(op="pattern", mat="green", at=[x, Y1 + 7, -1], size=[1, 58 - Y1 - 7, 1], step=[1, 3, 1],
            note="сад: зелёные огни по краям дорожки")
    # Pavilion at the far end: columns, a castle roof, benches and a table inside.
    px0, py0 = 2, 58
    for x, y in ((px0, py0), (px0 + 18, py0), (px0, py0 + 10), (px0 + 18, py0 + 10)):
        box("polished", [x, y, 0], [2, 2, 8], "беседка: колонна")
    box("granite", [px0, py0, -1], [20, 12, 1], "беседка: пол")
    add(op="walls", mat="gold", at=[px0, py0, -1], size=[20, 12, 1], note=None)
    box("fancy", [px0, py0, 8], [20, 12, 1], "беседка: перекрытие")
    add(op="gable", roof="pavroof", gable="fancy", at=[px0, py0, 9], size=[20, 12], ridge="x", overhang=1,
        note="беседка: крыша")
    thing("Prop_Decoration_Comfort5_T4_Table_Large", [px0 + 6, py0 + 4, 0], "Беседка: стол")
    for x in (px0 + 5, px0 + 10):
        thing("Prop_Decoration_Comfort2_T1_Bench", [x, py0 + 2, 0], "Беседка: скамья", walkable=True)
        thing("Prop_Decoration_Comfort2_T1_Bench", [x, py0 + 9, 0], "Беседка: скамья", rot=180, walkable=True)
    thing("Prop_Decoration_Comfort_T1_IlluminationCeiling_Chandelier", [px0 + 9, py0 + 5, 6], "Беседка: люстра",
          lit=True, walkable=True)
    # Four farm beds each side, hedged quadrants with openings toward the path.
    for side, (qx0, qx1) in (("w", (-48, -2)), ("e", (25, 71))):
        add(op="walls", mat="hedge", at=[qx0, 32, 0], size=[qx1 - qx0 + 1, 34, 2], note="сад: живая изгородь")
        gap_x = qx1 if side == "w" else qx0
        add(op="cut", kind="opening", at=[gap_x, 46, 0], size=[1, 6, 2], note="сад: проход в изгороди")
        box("sandstone", [qx0 + 1, 47, -1], [qx1 - qx0 - 1, 4, 1], "сад: дорожка между грядками")
        for bx in (qx0 + 3, qx0 + 18, qx0 + 33):
            thing("Prop_Production_Farmbed_Empty", [bx, 34, 0], "Сад: грядка", use="+y")
            thing("Prop_Production_Farmbed_Empty", [bx, 54, 0], "Сад: грядка", use="-y")
    for y in range(34, 64, 7):
        thing("Prop_Production_T4_Beehive", [WX0 + 4, y, 0], "Сад: улей", use="+x")
    for x in (-1, 20):
        for y in (40, 50, 60):
            thing("Prop_Decoration_Comfort_T1_IlluminationFloor_StandingTorch", [x, y, 0], "Сад: факел",
                  walkable=True)
    for x in range(4, 20, 4):
        thing("Flower_Pot_Long_Ground_Yellow", [x, Y1 + 2, 0], "Сад: цветы на террасе", walkable=True)


def spec():
    manor()
    towers()
    roofs()
    openings()
    stair()
    estate_wall()
    alley()
    garden()
    ground_floor()
    bedrooms()
    return {
        "schema": eh.SCHEMA,
        "name": "custodian-estate-v1",
        "title": "Усадьба Хранителя",
        "description": "Особняк с ремесленными залами и спальнями, ограда с башнями, аллея и сад. Алтарь 2 уровня.",
        "notes": [
            "Алтарь в центре двусветного зала; от ворот к нему ведёт прямая аллея и парадная дверь.",
            "1 этаж (5 м): кузня, плотницкая, хранилище (запад); кухня-столовая, алхимия, ткацкая (восток). Все двери выходят в зал.",
            "Лестница в глубине зала: 11 ступеней, площадки 2 м снизу и сверху; мост-галерея ведёт в оба крыла.",
            "2 этаж (4 м): 8 спален ремесленников и хозяина, каждая закрыта и под крышей.",
            "Сад за домом: 6 грядок, 8 ульев, изгороди, беседка. Светящиеся блоки: рамка у алтаря, карнизы, аллея, ограда.",
        ],
        "altar": {"at": [0, 0, 0], "level": 2},
        "ground": True,
        "materials": MATS,
        "levels": [["1 этаж", 6], ["2 этаж", 17], ["Чердак", 21], ["Всё", 60]],
        "parts": P,
        "furniture": F,
    }


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).with_name("estate.json"))
    out.write_text(json.dumps(spec(), ensure_ascii=False, indent=1), encoding="utf-8")
    house = eh.House(json.loads(out.read_text(encoding="utf-8")))
    errors, warnings = house.check()
    print(f"{len(house.voxels)} blocks, {len(house.furniture())} props, {len(errors)} errors, {len(warnings)} warnings")
    for e in errors:
        print("ERROR", e)
    for w in warnings[:40]:
        print("warn ", w)
