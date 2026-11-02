"""Generate workshop-fortress.json: a 64 m fortress for a level 2 Flame Altar.

Four two-storey ranges around a courtyard with the altar in its middle. Ground floors are
craft halls and the storage hall, upper floors are bedrooms off a corridor, the flat roofs
are the wall-walk. Each corner is a stair hall with two flights (ground -> upper -> roof),
each with 2 m landings at both ends.

Every side is written once in a local frame (the south side: range along x, courtyard to the
north) and turned into place by quarter turns about the altar centre. Run:
  python docs/enshrouded-houses/gen_workshop_fortress.py docs/enshrouded-houses/workshop-fortress.json
"""
import json
import sys

# Local frame of one side, in voxels (0.5 m). Altar platform top is x 0..7, y 0..7, z 0.
OUT0, OUT1 = -60, -57      # outer wall, 2 m of foundations
IN0, IN1 = -56, -41        # range interior, 8 m deep
INNER = -40                # courtyard wall
CORR0 = -44                # upper corridor y -44..-41, rooms y -56..-45
ROOM_WALL = -45
END0, END1 = -40, 47       # end walls of the range between corner halls
GROUND, UPPER, ROOF = 0, 9, 18   # feet levels; slabs at z -1, 8, 17

P, F, OPENINGS = [], [], []


def turn_box(at, size, k):
    """Quarter-turn a box ``k`` times anticlockwise about the altar centre (3.5, 3.5)."""
    (x0, y0, z), (sx, sy, sz) = at, size
    x1, y1 = x0 + sx - 1, y0 + sy - 1
    for _ in range(k % 4):
        x0, y0, x1, y1 = 7 - y1, x0, 7 - y0, x1
        sx, sy = sy, sx
    return [x0, y0, z], [sx, sy, sz]


DIRS = ["+x", "+y", "-x", "-y"]


def turn_dir(d, k):
    return DIRS[(DIRS.index(d) + k) % 4]


class Side:
    def __init__(self, k, name):
        self.k, self.name = k, name

    def add(self, **kw):
        P.append({k: v for k, v in kw.items() if v is not None})

    def box(self, mat, at, size, note=None):
        at, size = turn_box(at, size, self.k)
        self.add(op="box", mat=mat, at=at, size=size, note=note)

    def cut(self, kind, at, size, note=None):
        at, size = turn_box(at, size, self.k)
        self.add(op="cut", kind=kind, at=at, size=size, note=note)

    def run(self, name, mat, at, n, along="x", note=None, footprint=(4, 1, 8)):
        """``n`` templates in a row along local x (or y); footprint is one piece unturned."""
        w, d, h = footprint
        size = [w * n, d, h] if along == "x" else [d, w * n, h]
        rot0 = 0 if along == "x" else 90
        at2, _ = turn_box(at, size, self.k)
        rot = (rot0 + 90 * self.k) % 360
        self.add(op="template", name=name, mat=mat, at=at2, rot=rot, count=n, note=note)

    def grid(self, name, mat, at, nx, ny, piece, note=None):
        """``nx`` x ``ny`` square pieces (ceilings, foundations) over a local rectangle."""
        size = [piece * nx, piece * ny, 1]
        at2, size2 = turn_box(at, size, self.k)
        cx, cy = (nx, ny) if self.k % 2 == 0 else (ny, nx)
        self.add(op="template", name=name, mat=mat, at=at2, rot=0, count=[cx, cy], note=note)

    def flight(self, mat, at, d, note):
        size = [1, 8, 1] if d in "+x-x" else [8, 1, 1]
        at2, _ = turn_box(at, size, self.k)
        self.add(op="flight", mat=mat, at=at2, dir=turn_dir(d, self.k), note=note)

    def thing(self, prop, at, use, note, rot=0, **extra):
        """Furniture by game prop name; its real footprint is turned with the side."""
        at2, _ = turn_box(at, prop_size(prop, rot), self.k)
        item = {"id": note, "prop": prop, "at": at2, "rot": (rot + 90 * self.k) % 360}
        if use:
            item["use"] = turn_dir(use, self.k)
        item.update(extra)
        F.append(item)


def prop_size(debug, rot=0):
    sys.path.insert(0, "scripts")
    import enshrouded_house as h
    return h.prop_size(debug, rot)


def shell(s):
    """Outer wall, floors, courtyard wall, roof of one range, plus its corner stair hall."""
    n = s.name
    # Outer wall: four layers of 2 m foundations, a course of single blocks, then the roof slab.
    for z in (0, 4, 8, 12):
        s.run("foundation_2m", "stone", [OUT0, OUT0, z], 32, note=f"{n}: внешняя стена" if z == 0 else None,
              footprint=(4, 4, 4))
    s.box("stone", [OUT0, OUT0, 16], [128, 4, 1])
    # Floors: ground slab, upper slab, roof slab (ceiling templates 2 m).
    s.grid("ceiling_2m", "stone", [IN0, IN0, -1], 26, 4, 4, note=f"{n}: пол 1 этажа")
    s.box("stone", [IN0, INNER, -1], [104, 1, 1])
    s.grid("ceiling_2m", "wood", [IN0, IN0, 8], 26, 4, 4, note=f"{n}: перекрытие 1/2 этажа")
    s.box("wood", [IN0, INNER, 8], [104, 1, 1])
    s.grid("ceiling_2m", "stone", [OUT0, OUT0, 17], 27, 5, 4, note=f"{n}: плоская кровля = боевой ход")
    s.box("stone", [OUT0, INNER, 17], [108, 1, 1])
    # Courtyard wall, both storeys, and the end walls of the range.
    s.run("narrow_wall_2m", "stone", [END0, INNER, 0], 22, note=f"{n}: стена во двор, 1 этаж")
    s.run("narrow_wall_2m", "wood", [END0, INNER, 9], 22, note=f"{n}: стена во двор, 2 этаж")
    for x in (END0, END1):
        s.run("narrow_wall_2m", "stone", [x, IN0, 0], 4, along="y", note=f"{n}: торцевая стена")
        s.box("stone", [x, IN0, 8], [1, 16, 1])
        s.run("narrow_wall_2m", "wood", [x, IN0, 9], 4, along="y")


def corner(s):
    """Corner stair hall and landing hall; cut after every shell so no slab refills the holes."""
    n = s.name
    # Corner stair hall x -56..-41: flight 1 up the courtyard band, flight 2 up the outer band.
    s.flight("wood", [-52, -48, GROUND], "+x", f"{n}: лестница 1→2 этаж")
    s.cut("opening", [-48, -48, 8], [5, 8, 1], f"{n}: проём в перекрытии над лестницей")
    s.flight("wood", [-44, -56, UPPER], "-x", f"{n}: лестница 2 этаж→боевой ход")
    s.cut("opening", [-52, -56, 17], [5, 8, 1], f"{n}: люк на кровлю")
    # Landing hall x -39..-36: ground passage from the courtyard, upper landing for both flights.
    s.cut("door", [END0, -48, 0], [1, 4, 5], f"{n}: дверь лестничный зал → проход")
    s.cut("opening", [END0, IN0, UPPER], [1, 16, 5], f"{n}: выход с лестниц на этаж")
    s.run("narrow_wall_2m", "stone", [-35, IN0, 0], 4, along="y", note=f"{n}: стена прохода")
    s.box("stone", [-35, IN0, 8], [1, 16, 1])
    s.run("door_frame_2m", "stone", [-39, INNER, 0], 1, note=f"{n}: вход со двора к лестницам")


def windows_and_doors(s, bays):
    """Courtyard wall: a door into each ground hall and windows between; upper corridor windows."""
    for x0, x1, door in bays:
        if door:
            s.run("door_frame_2m", "stone", [door, INNER, 0], 1)
        for x in range(x0 + 2, x1 - 3, 8):
            if not (door and abs(x - door) < 6):
                s.run("window_frame_2m", "stone", [x, INNER, 0], 1)
    for x in range(-36, 44, 8):
        s.run("window_frame_2m", "wood", [x, INNER, UPPER], 1)


def bay_walls(s, xs, upper=True):
    for x in xs:
        s.run("narrow_wall_2m", "stone", [x, IN0, 0], 4, along="y", note=f"{s.name}: перегородка")
        s.box("stone", [x, IN0, 8], [1, 16, 1])
        if upper:
            s.run("narrow_wall_2m", "wood", [x, IN0, UPPER], 4, along="y")


def rooms(s, x_end, beds):
    """Upper floor: corridor along the courtyard, rooms 4 x 6 m with a bed, window and door."""
    n = (x_end + 36) // 4
    s.run("narrow_wall_2m", "wood", [-35, ROOM_WALL, UPPER], n, note=f"{s.name}: стена коридора")
    if -35 + 4 * n <= x_end:
        s.box("wood", [-35 + 4 * n, ROOM_WALL, UPPER], [x_end - (-35 + 4 * n) + 1, 1, 8])
    s.run("narrow_wall_2m", "wood", [-35, IN0, UPPER], 3, along="y")
    r0, i = -34, 0
    while r0 + 7 <= x_end:
        r1 = r0 + 7 if x_end - (r0 + 7) >= 9 else x_end
        if r1 < x_end:
            s.run("narrow_wall_2m", "wood", [r1 + 1, IN0, UPPER], 3, along="y")
        s.run("door_frame_2m", "wood", [r0 + 2, ROOM_WALL, UPPER], 1)
        s.cut("window", [r0 + 3, OUT0, UPPER + 2], [2, 4, 3], f"{s.name}: окно комнаты")
        who = beds[i] if i < len(beds) else "Свободная спальня"
        s.thing("Prop_Decoration_Comfort2_T1_Bed", [r0 + 1, IN0, UPPER], None, f"{who}: кровать", rot=90,
                needs_shelter=True, glyph="b")
        s.thing("Prop_Decoration_Comfort2_T1_Table_Night", [r0 + 5, IN0, UPPER], None, f"{who}: тумбочка", glyph="t")
        s.thing("Prop_Storage_32_T2_Box", [r1 - 1, -48, UPPER], "-x", f"{who}: сундук", glyph="c")
        r0, i = r1 + 2, i + 1


def stations(s, items):
    """Place props in local coordinates: (prop, x, y, use, label, rot)."""
    for prop, x, y, use, label, *rot in items:
        r = rot[0] if rot else 0
        s.thing(prop, [x, y, GROUND], use, label, rot=r, needs_shelter=True, glyph="s")


def chests(s, x0, x1, y, use, label, step=3):
    for x in range(x0, x1 - 1, step):
        s.thing("Prop_Storage_40_T3_Magic", [x, y, GROUND], use, f"{label} {x}", glyph="c")


S, E, N, W = Side(0, "юг"), Side(1, "восток"), Side(2, "север"), Side(3, "запад")
for side in (S, E, N, W):
    shell(side)
for side in (S, E, N, W):
    corner(side)

# ---- South: hunter and carpenter halls either side of the gate.
bay_walls(S, [-1, 8], upper=False)
windows_and_doors(S, [(-34, -2, -20), (9, 46, 26)])
S.cut("door", [0, OUT0, 0], [8, 4, 8], "ворота 4x4 м")
S.cut("opening", [0, INNER, 0], [8, 1, 8], "выход ворот во двор")
rooms(S, 46, ["Охотник Атален Скри", "Плотник Кейд Хоторн", "Надвратная: стража", "Надвратная: стража",
              "Гостевая", "Гостевая", "Гостевая", "Гостевая", "Гостевая"])
stations(S, [
    ("Prop_Production_T1_Drying_Rack", -32, IN0, "+y", "Охотник: сушилка"),
    ("Prop_Production_T3_Tanning_Station", -26, IN0, "+y", "Охотник: дубильный станок"),
    ("Prop_Production_T4_Spinning_Wheel", -19, IN0, "+y", "Охотник: прялка"),
    ("Prop_Production_T5_CardingMachine_Spinning", -14, IN0, "+y", "Охотник: прядильный станок"),
    ("Prop_Production_T2_NPC_Hand_Spindle", -7, IN0, "+y", "Охотник: веретено"),
    ("Prop_Production_T2_Table_Saw", 11, IN0, "+y", "Плотник: циркулярная пила"),
    ("Craftingstation_Carpenter_Workbench", 18, IN0, "+y", "Плотник: верстак для декора"),
    ("Prop_Production_T2_Kiln", 26, IN0, "+y", "Плотник: промышленная печь"),
    ("Craftingstation_Workbench", 33, IN0, "+y", "Плотник: верстак"),
])
chests(S, -32, -22, -46, "-y", "Охотник: магический сундук")
chests(S, 11, 21, -46, "-y", "Плотник: магический сундук")

# ---- East (k=1): local x -39..46 is global y south to north. Kitchen, then dining hall.
bay_walls(E, [3], upper=False)
windows_and_doors(E, [(-34, 2, -16), (4, 46, 24)])
rooms(E, 46, ["Фермер Эмили Фрей", "Помощник фермера", "Хозяйская спальня", "Хозяйская спальня",
              "Гостевая", "Гостевая", "Гостевая", "Гостевая", "Гостевая"])
stations(E, [
    ("Craftingstation_Farmer_Kitchen", -32, IN0, "+y", "Фермер: кухня"),
    ("Prop_Production_T3_Oven", -24, IN0, "+y", "Фермер: духовая печь"),
    ("Prop_Production_T3_Butter_Churn", -19, IN0, "+y", "Фермер: маслобойка"),
    ("Prop_Production_T1_Seedbed", -14, IN0, "+y", "Фермер: рассадница"),
    ("Prop_Production_T6_MedievalGrill", -7, IN0, "+y", "Фермер: жаровня"),
    ("Prop_Decoration_Comfort2_T1_Hearth_FirePlace", 23, IN0, "+y", "Обеденный зал: камин"),
])
chests(E, -32, -20, -46, "-y", "Кухня: магический сундук")
for i, x in enumerate((8, 32)):
    E.thing("Prop_Decoration_Comfort2_T1_Table_Large", [x, -50, GROUND], None,
            f"Обеденный зал: стол {i + 1}", glyph="t")
    for y in (-53, -45):
        E.thing("Prop_Decoration_Comfort2_T1_Bench", [x + 1, y, GROUND], None,
                f"Обеденный зал: скамья {i + 1}{'ю' if y < -50 else 'с'}", glyph="t")

# ---- North (k=2): local x -39..46 is global x east to west. Workshop, storage hall, guest hall.
bay_walls(N, [-19, 26], upper=False)
windows_and_doors(N, [(-34, -20, -28), (-18, 25, 0), (27, 46, 36)])
rooms(N, 46, ["Алхимик Бальтазар", "Помощник алхимика", "Коллекционер Олден Кроули", "Бард Вэлори",
              "Цирюльник Элио Риччи", "Рыбак Мелвилл Фонтейн", "Гостевая", "Гостевая", "Гостевая"])
stations(N, [
    ("Craftingstation_Workbench", -32, IN0, "+y", "Мастерская: верстак"),
    ("Craftingstation_Logic_System_Workbench", -25, IN0, "+y", "Мастерская: верстак искры"),
])
chests(N, -16, 24, -56, "+y", "Хранилище: у стены")
chests(N, -12, 21, -51, "-y", "Хранилище: остров юг")
chests(N, -12, 21, -49, "+y", "Хранилище: остров север")
N.thing("Prop_Decoration_Comfort1_T0_Table_Medium", [33, -50, GROUND], None, "Гостевой зал: стол", glyph="t")

# ---- West (k=3): local x -39..46 is global y north to south. Alchemy, then double-height smithy.
bay_walls(W, [3])
windows_and_doors(W, [(-34, 2, -16), (4, 46, 24)])
W.cut("opening", [4, IN0, 8], [43, 16, 1], "кузница: двойная высота (доменная печь 4.03 м)")
rooms(W, 2, ["Кузнец Освальд Андерс", "Помощник кузнеца", "Охотник: помощник", "Свободная спальня"])
stations(W, [
    ("Prop_Production_T3_Alchemy_Station", -32, IN0, "+y", "Алхимик: алхимический стол"),
    ("Prop_Production_T4_Laboratory", -24, IN0, "+y", "Алхимик: лаборатория"),
    ("Prop_Production_T1_HandGrinder", -16, IN0, "+y", "Алхимик: шлифовальные камни"),
    ("Prop_Production_T6_improvedGrinder", -11, IN0, "+y", "Алхимик: мельница"),
    ("Prop_Production_T5_BlastFurnace", 6, IN0, "+y", "Кузнец: доменная печь"),
    ("Prop_Production_T1_Forge", 16, IN0, "+y", "Кузнец: кузница"),
    ("Prop_Production_T2_Smelter", 24, IN0, "+y", "Кузнец: плавильня"),
    ("Prop_Production_T1_Charcoal_Kiln", 31, IN0, "+y", "Кузнец: углеобжигательная печь"),
    ("Craftingstation_Workbench", 39, IN0, "+y", "Кузнец: верстак"),
])
chests(W, -32, -20, -46, "-y", "Алхимик: магический сундук")
chests(W, 6, 30, -46, "-y", "Кузнец: магический сундук")

# ---- Roof: outer parapet and merlons, low railing on the courtyard edge.
P.append({"op": "walls", "mat": "stone", "at": [OUT0, OUT0, ROOF], "size": [128, 128, 2], "note": "парапет 1 м"})
P.append({"op": "battlement", "mat": "stone", "at": [OUT0, OUT0, ROOF + 2], "size": [128, 128, 2], "note": "зубцы"})
P.append({"op": "walls", "mat": "stone", "at": [INNER, INNER, ROOF], "size": [88, 88, 1], "note": "ограждение во двор 0.5 м"})

# ---- Courtyard: four farm plots and beehives; the altar stays in the middle, gate on its axis.
for x, y in ((-30, 14), (-30, -22), (27, 14), (27, -22)):
    F.append({"id": f"Огород {x},{y}", "prop": "Prop_Production_Farmbed_Empty", "at": [x, y, 0], "use": "+y" if y > 0 else "-y", "glyph": "g"})
for x in (-34, 42):
    F.append({"id": f"Улей {x}", "prop": "Prop_Production_T4_Beehive", "at": [x, 0, 0], "use": "+x" if x < 0 else "-x", "glyph": "h"})

spec = {
    "schema": "custodian.enshrouded.house.v1",
    "name": "Workshop fortress 64x64",
    "description": ("Крепость 64x64 м для алтаря 2 уровня (куб 80 м). Четыре двухэтажных корпуса вокруг двора с "
                    "алтарём в центре: внизу ремесленные залы (кузница двойной высоты, алхимик, охотник, плотник, "
                    "кухня фермера), хранилище и обеденный зал; наверху спальни ремесленников и гостей вдоль "
                    "коридора; плоские кровли = боевой ход. В каждом углу лестничный зал: два марша из шаблона "
                    "«Ступенька» с площадками 2 м сверху и снизу."),
    "altar": {"at": [0, 0, 0], "level": 2},
    "ground": True,
    "materials": {"stone": "Block_T1_Stone_Rough_CRAFTING", "wood": "Block_T1_Wood_Rough_CRAFTING"},
    "glyphs": {"stone": "#", "wood": "="},
}
row = lambda x: json.dumps(x, ensure_ascii=False)
out = json.dumps(spec, ensure_ascii=False, indent=2)[:-2]
out += ',\n  "parts": [\n    ' + ',\n    '.join(map(row, P)) + '\n  ],\n  "furniture": [\n    ' + ',\n    '.join(map(row, F)) + '\n  ]\n}\n'
json.loads(out)
open(sys.argv[1], "w", encoding="utf-8").write(out)
