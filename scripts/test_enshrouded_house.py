"""Checks for the Enshrouded house design expander and its shelter rules."""

from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enshrouded_house as eh

EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "enshrouded-houses" / "starter-cottage.json"
FORTRESS = EXAMPLE.with_name("workshop-fortress.json")
ESTATE = EXAMPLE.with_name("estate.json")
CASTLE = EXAMPLE.with_name("castle.json")


def _example():
    return json.loads(EXAMPLE.read_text(encoding="utf-8"))


def _without(spec, op):
    spec = copy.deepcopy(spec)
    spec["parts"] = [p for p in spec["parts"] if p["op"] != op]
    return spec


class HouseDesignTests(unittest.TestCase):
    def test_example_is_valid(self) -> None:
        errors, _ = eh.House(_example()).check()
        self.assertEqual(errors, [])

    def test_fortress_is_inside_altar_cube(self) -> None:
        # Older example: it predates the buildability rules (doors, routes, steps) and fails them.
        spec = json.loads(FORTRESS.read_text(encoding="utf-8"))
        house = eh.House(spec)
        errors, _ = house.check()
        self.assertFalse(any("altar cube" in e for e in errors), errors)
        self.assertTrue(any("step 9 is missing" in e for e in errors), errors)
        self.assertTrue(house.placements)
        for part in spec["parts"]:
            part["at"][0] += 20
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("altar cube" in e for e in errors), errors)

    def test_altar_is_mandatory_and_at_origin(self) -> None:
        spec = _example()
        del spec["altar"]
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("no altar" in e for e in errors), errors)
        spec["altar"] = {"at": [1, 0, 0], "level": 1}
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("must be [0, 0, 0]" in e for e in errors), errors)

    def test_altar_box_and_margin_stay_free(self) -> None:
        spec = _example()
        spec["parts"].append({"op": "box", "mat": "stone", "at": [9, 2, 0], "size": [1, 1, 1]})
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("free around the altar" in e for e in errors), errors)
        spec["parts"].append({"op": "box", "mat": "stone", "at": [3, 3, 1], "size": [1, 1, 1]})
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("space over the altar is not free" in e for e in errors), errors)

    def test_closed_gate_cuts_the_route_to_the_altar(self) -> None:
        spec = json.loads(FORTRESS.read_text(encoding="utf-8"))
        spec["parts"].append({"op": "box", "mat": "stone", "at": [0, -60, 0], "size": [8, 4, 8]})
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("no walkable route" in e for e in errors), errors)

    def test_altar_platform_is_flush_with_floor(self) -> None:
        spec = _example()
        spec["parts"] = [q for q in spec["parts"] if q.get("at") != [-3, -3, -1]]   # drop the altar pad
        spec["parts"].append({"op": "box", "mat": "stone", "at": [-3, -3, -1], "size": [4, 1, 1]})
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("hole at the altar" in e for e in errors), errors)
        spec["parts"][-1] = {"op": "box", "mat": "stone", "at": [-3, -3, -1], "size": [14, 14, 1]}
        errors, _ = eh.House(spec).check()
        self.assertFalse(any("hole at the altar" in e for e in errors), errors)
        # A pit carved for the platform is the hole seen in game on 2026-10-31.
        spec["parts"].append({"op": "cut", "kind": "opening", "at": [0, 0, -1], "size": [8, 8, 1]})
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("hole at the altar" in e and "[0, 0, -1]" in e for e in errors), errors)

    def test_stairs_need_landings_at_both_ends(self) -> None:
        base = {"schema": eh.SCHEMA, "altar": {"level": 1}, "ground": True, "materials": {"s": "x"}}
        floor = {"op": "box", "mat": "s", "at": [20, 0, 8], "size": [20, 8, 1]}
        good = dict(base, parts=[floor, {"op": "flight", "mat": "s", "at": [11, 0, 0], "dir": "+x"}])
        self.assertEqual(eh.House(good).check_stairs(), [])
        # Flight that ends against a wall (the old wall-walk stair): no top landing.
        wall = {"op": "box", "mat": "s", "at": [22, 0, 9], "size": [1, 8, 8]}
        errors = eh.House(dict(base, parts=[floor, wall, good["parts"][1]])).check_stairs()
        self.assertTrue(any("landing at the top" in e for e in errors), errors)
        # Flight into a ceiling: no headroom over the upper steps.
        ceiling = {"op": "box", "mat": "s", "at": [10, 0, 8], "size": [10, 8, 1]}
        errors = eh.House(dict(base, parts=[floor, ceiling, good["parts"][1]])).check_stairs()
        self.assertTrue(any("headroom" in e for e in errors), errors)

    def test_flight_uses_the_game_stair_template(self) -> None:
        for d in ("+x", "-x", "+y", "-y"):
            house = eh.House({"schema": eh.SCHEMA, "altar": {"level": 1}, "materials": {"s": "x"},
                              "parts": [{"op": "flight", "mat": "s", "at": [20, 20, 0], "dir": d}]})
            (x, y, z), (dx, dy) = house.flights[0]["at"], house.flights[0]["d"]
            steps = [(x + dx * i + (0 if dx else k), y + dy * i + (k if dx else 0), z + i) for i in range(9) for k in range(8)]
            self.assertTrue(all(c in house.voxels for c in steps), d)
            self.assertEqual([p["template"] for p in house.placements], ["stair_4m"])

    def test_props_take_game_sizes_and_need_room_to_use(self) -> None:
        self.assertEqual(eh.prop_size("Prop_Production_T5_BlastFurnace"), [8, 4, 9])
        self.assertEqual(eh.prop_size("Prop_Decoration_Comfort2_T1_Bed", 90), [3, 6, 3])
        spec = _example()
        spec["parts"].append({"op": "box", "mat": "stone", "at": [30, 30, -1], "size": [10, 10, 1]})
        spec["furniture"].append({"id": "kiln", "prop": "Prop_Production_T1_Charcoal_Kiln", "at": [31, 31, 0]})
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("kiln: set use" in e for e in errors), errors)
        spec["furniture"][-1]["use"] = "+x"
        errors, _ = eh.House(spec).check()
        self.assertFalse(any(e.startswith("kiln") for e in errors), errors)
        spec["parts"].append({"op": "box", "mat": "stone", "at": [38, 31, 0], "size": [1, 6, 4]})
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("kiln: no 1.0 m of clear floor" in e for e in errors), errors)

    def test_missing_roof_breaks_shelter(self) -> None:
        errors, _ = eh.House(_without(_example(), "gable")).check()
        self.assertTrue(any("bed_1" in e and "no roof" in e for e in errors), errors)

    def test_doorway_without_door_is_open(self) -> None:
        spec = _example()
        for p in spec["parts"]:
            if p.get("kind") == "door":
                p["kind"] = "opening"
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("open to outside" in e for e in errors), errors)

    def test_window_above_bed_level_keeps_room_closed(self) -> None:
        errors, _ = eh.House(_example()).check()
        self.assertFalse(any("open to outside" in e for e in errors))

    def test_outside_altar_cube_is_reported(self) -> None:
        spec = _example()
        spec["parts"].append({"op": "box", "mat": "stone", "at": [200, 0, 0], "size": [1, 1, 1]})
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("altar cube" in e for e in errors), errors)

    def test_furniture_inside_wall_is_reported(self) -> None:
        spec = _example()
        wx, wy, wz = next(p["at"] for p in spec["parts"] if p["op"] == "walls")
        spec["furniture"][0]["at"] = [wx, wy + 10, wz]
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("overlaps blocks" in e for e in errors), errors)

    def test_template_costs_match_game(self) -> None:
        # Blocks observed in game for these shapes: foundation 2m = 64, narrow wall 2m = 32, ceiling 2m = 16.
        for shape, blocks in (("foundation_2m", 64), ("narrow_wall_2m", 32), ("ceiling_2m", 16)):
            for direction in ("x", "y"):
                spec = {"schema": eh.SCHEMA, "materials": {"s": "x"},
                        "parts": [{"op": "template", "name": shape, "mat": "s", "at": [0, 0, 0], "dir": direction}]}
                self.assertEqual(len(eh.House(spec).voxels), blocks, shape)

    def test_template_voxels_match_shape(self) -> None:
        for name in eh.TEMPLATES:
            spec = {"schema": eh.SCHEMA, "materials": {"s": "x"},
                    "parts": [{"op": "template", "name": name, "mat": "s", "at": [0, 0, 0], "dir": "y"}]}
            self.assertEqual(len(eh.House(spec).voxels), eh.template_voxels(name), name)

    def test_template_run_and_door_holes(self) -> None:
        spec = {"schema": eh.SCHEMA, "materials": {"s": "x"}, "parts": [
            {"op": "template", "name": "narrow_wall_2m", "mat": "s", "at": [0, 0, 0], "dir": "x", "count": 3},
            {"op": "template", "name": "door_frame_2m", "mat": "s", "at": [4, 0, 0], "dir": "x"}]}
        house = eh.House(spec)
        self.assertEqual(len(house.placements), 4)
        self.assertEqual(len(house.doors), 20)
        self.assertEqual(len(house.voxels), 3 * 32 - 20)

    def test_export_runs_cover_every_voxel(self) -> None:
        house = eh.House(_example())
        exported = house.export()
        self.assertEqual(sum(r[3] for r in exported["runs_x"]), len(house.voxels))
        self.assertEqual(exported["voxel_m"], 0.5)

    def test_gable_ridge_direction_is_symmetric(self) -> None:
        def roof(ridge, size):
            spec = {"schema": eh.SCHEMA, "materials": {"r": "x"},
                    "parts": [{"op": "gable", "roof": "r", "at": [0, 0, 0], "size": size, "ridge": ridge}]}
            return len(eh.House(spec).voxels)
        self.assertEqual(roof("x", [16, 12]), roof("y", [12, 16]))

    def test_rotation_keeps_shape(self) -> None:
        for rot in (0, 90, 180, 270):
            spec = {"schema": eh.SCHEMA, "materials": {"s": "x"},
                    "parts": [{"op": "template", "name": "stepped_wall_4m", "mat": "s", "at": [0, 0, 0], "rot": rot}]}
            house = eh.House(spec)
            self.assertEqual(len(house.voxels), 28)
            (x0, y0, _), (x1, y1, _) = house.bbox()
            along, across = (x1, y1) if rot in (0, 180) else (y1, x1)
            self.assertTrue(min(x0, y0) >= 0 and along <= 7 and across == 0 and min(x0, y0) <= 1, rot)

    def test_window_hole_matches_game_frame(self) -> None:
        # Window frame objects are 1 x 1.5 m; the template leaves exactly that, 1 m above its base.
        spec = {"schema": eh.SCHEMA, "materials": {"s": "x"},
                "parts": [{"op": "template", "name": "window_frame_2m", "mat": "s", "at": [0, 0, 0]}]}
        house = eh.House(spec)
        hole = {(x, z) for x in range(4) for z in range(8) if (x, 0, z) not in house.voxels}
        self.assertEqual(hole, {(x, z) for x in (1, 2) for z in (2, 3, 4)})

    def test_unknown_material_is_rejected(self) -> None:
        spec = _example()
        spec["parts"][0]["mat"] = "marble"
        with self.assertRaises(eh.DesignError):
            eh.House(spec)

    def test_old_estate_is_caught_by_buildability_rules(self) -> None:
        # The estate was built before the rules from the first in-game test: its corridors are 2 m
        # wide and its wall lamps face the wrong way. The checker must keep saying so.
        house = eh.House(json.loads(ESTATE.read_text(encoding="utf-8")))
        errors, _ = house.check()
        self.assertTrue(any("free floor" in e for e in errors), errors)
        self.assertTrue(any("not on a wall" in e for e in errors), errors)
        beds = [f for f in house.furniture() if f.get("group") == "bed"]
        self.assertEqual(len(beds), 8)

    def test_castle_is_valid_and_fits_one_bundle(self) -> None:
        house = eh.House(json.loads(CASTLE.read_text(encoding="utf-8")))
        errors, _ = house.check()
        self.assertEqual(errors, [])
        (x0, y0, z0), (x1, y1, z1) = house.bbox()
        self.assertLessEqual((x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1), 1_000_000)
        self.assertTrue(any(c[2] < -4 for c in house.dug), "basement is dug below the altar platform")

    def test_spire_and_octagon_tower(self) -> None:
        spec = {"schema": eh.SCHEMA, "materials": {"s": "x"}, "parts": [
            {"op": "tower", "mat": "s", "at": [0, 0, 0], "size": 12, "height": 1},
            {"op": "spire", "mat": "s", "at": [20, 0, 0], "size": 6, "steep": 2, "shape": "square"}]}
        house = eh.House(spec)
        ring = {(x, y) for x, y, z in house.voxels if x < 12}
        self.assertNotIn((0, 0), ring)                     # corners are cut
        self.assertIn((0, 5), ring)
        self.assertEqual(max(z for x, y, z in house.voxels if x >= 20), 5)

    def test_door_and_window_hang_game_props(self) -> None:
        spec = {"schema": eh.SCHEMA, "materials": {"s": "x"}, "parts": [
            {"op": "box", "mat": "s", "at": [0, 0, 0], "size": [12, 1, 8]},
            {"op": "box", "mat": "s", "at": [0, 0, 0], "size": [1, 12, 8]},
            {"op": "door", "at": [2, 0, 0], "wall": "x"},
            {"op": "window", "at": [0, 4, 2], "wall": "y"}]}
        house = eh.House(spec)
        self.assertEqual({c for c in house.doors}, set(eh._box([2, 0, 0], [4, 1, 5])))
        self.assertFalse(any(c in house.voxels for c in eh._box([0, 4, 2], [1, 2, 3])))
        door, window = house.placed
        self.assertEqual((door["pos"], door["rot_xyzw"]), ([4.0, 0.5, 0], [0, 0, 0, 1]))
        self.assertEqual(window["pos"], [0.5, 5.0, 2])
        self.assertAlmostEqual(abs(window["rot_xyzw"][1]), 0.70711, places=4)

    def test_flight_rise_and_lit_lamp(self) -> None:
        spec = {"schema": eh.SCHEMA, "materials": {"s": "x"}, "furniture": [
            {"id": "lamp", "prop": "Prop_Decoration_Comfort_T1_IlluminationWall_Candle", "at": [0, 0, 0], "lit": True}],
            "parts": [{"op": "flight", "mat": "s", "at": [0, 0, 0], "dir": "+x", "rise": 11}]}
        house = eh.House(spec)
        self.assertEqual(house.flights[0]["steps"], 11)
        tops = {x: max(z for cx, _, z in house.voxels if cx == x) for x in range(11)}
        self.assertEqual(tops, {x: x for x in range(11)})
        self.assertEqual(house.furniture()[0]["template_uuid"], ["45cb8c8ebf46d5b3", "857a38cf714e9ba8"])



def _room(**extra):
    """A 12 x 12 m stone room on ground with a door in its south wall, the altar outside."""
    spec = {"schema": eh.SCHEMA, "altar": {"level": 2}, "ground": True,
            "materials": {"s": "Block_T1_Stone_Rough_CRAFTING"}, "parts": [
                {"op": "box", "mat": "s", "at": [-3, -3, -1], "size": [14, 14, 1]},
                {"op": "box", "mat": "s", "at": [14, 14, -1], "size": [24, 24, 1]},
                {"op": "walls", "mat": "s", "at": [14, 14, 0], "size": [24, 24, 8]},
                {"op": "box", "mat": "s", "at": [14, 14, 8], "size": [24, 24, 1]},
                {"op": "door", "at": [24, 14, 0], "wall": "x", "note": "door"}], "furniture": []}
    for k, v in extra.items():
        spec[k] = spec[k] + v if isinstance(v, list) else v
    return spec


class BuildabilityTests(unittest.TestCase):
    """Rules learned when the first generated castle was built in game (2026-10-31)."""

    def test_clean_room_passes(self) -> None:
        errors, _ = eh.House(_room()).check()
        self.assertEqual(errors, [])

    def test_even_gable_gets_a_ridge_beam(self) -> None:
        # Two roof voxels side by side at the top became two slopes with a slot in game.
        mats = {"r": "Block_Roof_T5_Granite_Shingles", "s": "Block_T3_Stone_Fancy_CRAFTING"}
        h = eh.House({"schema": eh.SCHEMA, "materials": mats, "parts": [
            {"op": "gable", "roof": "r", "gable": "s", "at": [0, 0, 0], "size": [6, 4], "ridge": "y"}]})
        top = h.ridge_top
        self.assertEqual({h.voxels[(x, 1, top)] for x in (2, 3)}, {"s"})
        h = eh.House({"schema": eh.SCHEMA, "materials": mats, "parts": [
            {"op": "gable", "roof": "r", "at": [0, 0, 0], "size": [5, 4], "ridge": "y"}]})
        self.assertEqual(h.voxels[(2, 1, h.ridge_top)], "r")

    def test_column_in_front_of_a_door(self) -> None:
        errors, _ = eh.House(_room(parts=[{"op": "box", "mat": "s", "at": [25, 18, 0], "size": [1, 1, 8]}])).check()
        self.assertTrue(any(e.startswith("door: less than 3 m") and "block at [25, 18, 0]" in e for e in errors), errors)

    def test_furniture_in_front_of_a_door(self) -> None:
        f = {"id": "chest", "prop": "Prop_Storage_40_T3_Magic", "at": [24, 17, 0], "use": "+y"}
        errors, _ = eh.House(_room(furniture=[f])).check()
        self.assertTrue(any("furniture at" in e for e in errors), errors)

    def test_narrow_corridor_has_no_route(self) -> None:
        # A wall leaving a 1.5 m slot cuts the room in two for a 2 m wide walker.
        f = {"id": "chest", "prop": "Prop_Storage_40_T3_Magic", "at": [34, 34, 0], "use": "-y"}
        wall = {"op": "box", "mat": "s", "at": [15, 28, 0], "size": [19, 1, 8]}
        errors, _ = eh.House(_room(parts=[wall], furniture=[f])).check()
        self.assertTrue(any(e.startswith("chest: no 2 m wide") for e in errors), errors)

    def test_hole_in_floor(self) -> None:
        spec = _room(parts=[{"op": "cut", "at": [29, 29, -1], "size": [1, 1, 1]}], ground=False)
        spec["parts"].insert(0, {"op": "box", "mat": "s", "at": [14, 14, -3], "size": [24, 24, 1]})
        spec["parts"].append({"op": "cut", "at": [29, 29, -3], "size": [1, 1, 2]})
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("hole(s) in floors" in e for e in errors), errors)
        self.assertTrue(any(e.startswith("ground is off") for e in errors), errors)

    def test_floating_piece(self) -> None:
        errors, _ = eh.House(_room(parts=[{"op": "box", "mat": "s", "at": [24, 44, 6], "size": [2, 2, 1]}])).check()
        self.assertTrue(any("floating piece of 4 block(s) at [24, 44, 6]" in e for e in errors), errors)

    def test_wall_lamp_snaps_to_the_wall_face(self) -> None:
        lamp = {"id": "lamp", "prop": "Prop_Decoration_Comfort_T4_IlluminationWall", "at": [15, 24, 5]}
        house = eh.House(_room(furniture=[lamp]))
        f = house.furniture()[0]
        self.assertEqual((f["mount"], f["rot"]), ("wall", 270))       # back to the west wall, facing +x
        self.assertEqual(f["pos"], [15, 24.5, 5])                       # pivot on the wall face, like the game
        self.assertEqual(house.check()[0], [])
        lamp["rot"] = 0                                                 # facing +y: nothing behind it
        errors, _ = eh.House(_room(furniture=[lamp])).check()
        self.assertTrue(any("not on a wall" in e for e in errors), errors)

    def test_ceiling_lamp_hangs_from_the_ceiling(self) -> None:
        lamp = {"id": "chandelier", "prop": "Prop_Decoration_Comfort_T1_IlluminationCeiling_Chandelier", "at": [24, 24, 4]}
        f = eh.House(_room(furniture=[lamp])).furniture()[0]
        self.assertEqual(f["mount"], "ceiling")
        self.assertEqual(f["pos"][2], 8)                                # under the roof slab at z 8

    def test_floor_prop_in_the_air(self) -> None:
        f = {"id": "table", "prop": "Prop_Decoration_Comfort5_T4_Table_Large", "at": [18, 24, 3]}
        errors, _ = eh.House(_room(furniture=[f])).check()
        self.assertTrue(any(e.startswith("table: hangs in the air") for e in errors), errors)

    def test_cut_over_stairs_must_keep_the_last_step(self) -> None:
        spec = _room(parts=[{"op": "flight", "mat": "s", "at": [16, 20, 0], "dir": "+y", "rise": 9},
                            {"op": "cut", "at": [16, 20, 8], "size": [8, 9, 1]}])
        errors, _ = eh.House(spec).check()
        self.assertTrue(any("step 9 is missing" in e for e in errors), errors)

    # Gaps found in review of the first version of these rules (PR #4).
    def test_door_cut_gets_the_door_checks(self) -> None:
        spec = _room(parts=[{"op": "box", "mat": "s", "at": [25, 18, 0], "size": [1, 1, 8]}])
        spec["parts"] = [p if p.get("op") != "door" else
                         {"op": "cut", "kind": "door", "at": [24, 14, 0], "size": [4, 1, 5], "note": "door"}
                         for p in spec["parts"]]
        errors, _ = eh.House(spec).check()
        self.assertTrue(any(e.startswith("door: less than 3 m") for e in errors), errors)

    def test_furniture_blocks_the_route(self) -> None:
        wall = {"id": "screen", "at": [15, 28, 0], "size": [22, 2, 6]}
        chest = {"id": "chest", "prop": "Prop_Storage_40_T3_Magic", "at": [34, 34, 0], "use": "-y"}
        errors, _ = eh.House(_room(furniture=[wall, chest])).check()
        self.assertTrue(any(e.startswith("chest: no 2 m wide") for e in errors), errors)

    def test_block_in_a_dug_room_floats(self) -> None:
        parts = [{"op": "box", "mat": "s", "at": [24, 24, -7], "size": [8, 8, 1]},
                 {"op": "cut", "kind": "opening", "at": [24, 24, -6], "size": [8, 8, 5]},
                 {"op": "box", "mat": "s", "at": [27, 27, -3], "size": [1, 1, 1]}]
        self.assertTrue(any("[27, 27, -3]" in e for e in eh.House(_room(parts=parts)).check_floating()))

    def test_wide_pit_is_a_hole(self) -> None:
        cut = {"op": "cut", "kind": "opening", "at": [29, 29, -3], "size": [2, 2, 3]}
        self.assertTrue(eh.House(_room(parts=[cut])).check_holes())

    def test_hung_ceiling_prop_hits_a_beam(self) -> None:
        lamp = {"id": "lamp", "prop": "Prop_Decoration_Comfort_T1_IlluminationCeiling_Chandelier", "at": [24, 24, 7]}
        beam = {"op": "box", "mat": "s", "at": [15, 25, 4], "size": [22, 1, 1]}
        errors, _ = eh.House(_room(parts=[beam], furniture=[lamp])).check()
        self.assertTrue(any(e.startswith("lamp: overlaps blocks") for e in errors), errors)

    def test_ground_layers_are_parts_of_their_own(self) -> None:
        import tempfile
        import enshrouded_bundle as eb
        spec = _room()
        spec["altar"] = {"level": 2}
        spec["parts"] = [{"op": "box", "mat": "s", "at": [-70, -70, -1], "size": [148, 148, 1]},
                         {"op": "box", "mat": "s", "at": [0, 20, 0], "size": [1, 1, 8]}]
        with tempfile.TemporaryDirectory() as tmp:
            eb.write_bundle(eh.House(spec), Path(tmp) / "wide-v1", "wide")
            fit = json.loads((Path(tmp) / "wide-v1" / "fit.json").read_text(encoding="utf-8"))
        self.assertTrue(all(p["cells"] <= eb.PART_CELLS for p in fit["parts"]), [p["cells"] for p in fit["parts"]])

    def test_bundle_writes_ground_under_the_build(self) -> None:
        import tempfile
        import enshrouded_bundle as eb
        house = eh.House(_room())
        ground = house.ground_cells()
        self.assertIn((0, 0, -5), ground)                               # under the altar
        self.assertIn((2, 2, -2), ground)                               # no pit: the altar sits on ground
        self.assertEqual(min(c[2] for c in ground), -eh.GROUND_DEPTH)
        with tempfile.TemporaryDirectory() as tmp:
            eb.write_bundle(house, Path(tmp) / "room-v1", "room")
            scene = eb.read_bundle(Path(tmp) / "room-v1")
        dirt = sum(r[3] for r in scene["runs"] if scene["materials"][str(r[4])] == "Terrain_1")
        self.assertEqual(dirt, len(ground))


if __name__ == "__main__":
    unittest.main()
