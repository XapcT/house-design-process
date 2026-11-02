# Enshrouded blueprint folder format

Read from the bundle `mayor-manor-furnished-no-stations-v1`, the World Editor `blueprint.lua` and the local house builder. The `.sfbp` format belongs to the ShroudForge World Editor mod; it is not a native game format. Facts here were checked against the real files unless marked "not verified".

## Coordinates

- Axes: X, Y up, Z. Metres in `.sfbp` extents and props. One voxel is 0.5 m.
- Native voxel grid coordinate = world metres x 2. Example: `originGrid` 7224,1608,4667 is world 3612, 804, 2333.5.
- All voxel parts and the props frame share one local origin: the minimum corner of the crop, `fit.json: source_origin_world`.
- Local origin is NOT the altar. The altar is a marker row in the props frame. In this bundle it sits at local 15.25, 9.5, 17.25.
- Altar relation: main floor top = altar origin Y + 1 m. Altar box = origin + (-1.75, -1, -1.75) to origin + (2.25, 1, 2.25), so 4 x 2 x 4 m. The altar lands on the real altar in the target world; the builder never creates, moves or copies an altar.
- Compass direction of +Z is not confirmed.

## Files in a folder

| File | Role | Read by the builder |
|---|---|---|
| `manifest.json` | Bundle id, status, altar reference, floor offset, sha256 and size of each file in `files` | Yes, every listed hash is checked |
| `fit.json` | Geometry contract: parts list, crop origin, altar reference | Yes |
| `<name>-part-N.sfbp` | Voxels, one tile each | Yes |
| `<name>-props-frame.sfbp` | Props and the altar marker, no voxels | Yes |
| `source-prop-aabbs.json` | World AABB of every prop, used to prove nothing overlaps the altar | Yes |
| `catalog-entry.json` | Catalog card: label, author, sizes, recipe counts | Catalog only |
| `README.md`, `VARIANT-REPORT.md`, `build_variant.py`, `verify_native_parse.py` | Notes and reproducibility | No |
| `evidence/` | Live-run receipts. Left out of this repo copy: holds a Steam id and one-off native handles | No |

### manifest.json

`id`, `status`, `derived_from`, `label`, `source_url`, `house_props`, `source_parts`, `source_reference_altar` [x,y,z world], `floor_offset_above_altar_origin_m` (1.0), `preserve_altar` (true), `altar_body_open_square_m` [4,4], `minimum_proven_altar_zone_m` [40,40,40], and `files`: {name: {sha256, bytes}}.

### fit.json (fields the planner uses)

- `source_origin_world` local origin in world metres.
- `source_dims` whole crop in cells: 59,45,62. `source_extent_m` is that x 0.5.
- `source_anchor_world` altar position in the source world.
- `source_crop_world_bounds` {min,max}.
- `parts`: [{file, sha256, dims, offset (cells, always a stack here: 0,0,0 / 0,15,0 / 0,30,0), cells, occupied}].
- `props_frame` file name. `source_props_count`.
- Target-only history: `target_alt_world`, `translation`, `target_floor_top`, `originGrid`, `translated_*`, `alt_level1_zone_world_bounds`. A new placement recalculates these from a fresh observation; do not reuse them.
- `clear_anchor_proof`, `component_selection`, `selected_props`, `excluded_props`, `notes` are evidence of how the crop was chosen.
- Parts must cover the whole crop with no gap and no overlap.

### source-prop-aabbs.json

`props`: [{item_id, recipe, world_min, world_max, overlaps_alt, gap_xyz, gap_m}], `altar_aabb_world`, `overlap_count`, `nearest`, `recipe_bounds_source`.

## .sfbp text format (V7)

Plain UTF-8. Lines end with LF. There is NO newline after the last line: CRLF or a final newline is rejected by the loader. Keep these bytes exact; a Windows checkout that converts line endings breaks the hashes.

| Line | Voxel file | Props file |
|---|---|---|
| 1 | `SHROUDFORGE_WORLD_BLUEPRINT_V7` | same |
| 2 | rotation axis, `y` | `y` |
| 3 | extent metres `x,y,z` = dims x 0.5 | whole crop extent, all > 0 |
| 4 | `voxel` | `props` |
| 5 | dims in cells `sx,sy,sz` (integers) | `-` |
| 6 | voxel offset, `0,0,0` | `-` |
| 7 | cell size, `0.5,0.5,0.5` | `-` |
| 8 | cells: `sx*sy*sz` comma-separated uint16 | `-` |
| 9 | coverage: same count, values 0, 1, 2 | `-` |
| 10 | prop count, `0` in parts | prop count |
| 11+ | none | one prop per row |

Cell index: `i = x + sx * (y + sy * z)`. X fastest, then Y, then Z. A part file with props is legal for the format, but the bundle keeps props in the frame.

### Cells

`value & 255` is the material id, `value >> 8` is density.

- Building voxel: value = material id, 128 and up, high byte 0. See `materials.json`. Example 132 = `Block_T4_Stone_HighlyPolished_REWARD`.
- Terrain voxel: material id under 128, density in the high byte. 65282 = 0xFF02 is material 2 at density 255. Lower densities are partial terrain surface (55553 = 0xD901 is material 1, density 217).
- Air: 0.

There is NO shape, half-block, slope or rotation field. Every building cell is a whole 0.5 m cube. Rounded or sloped looks come from arrangement and the game's own rendering; Construction Hammer templates are only stamped sets of cubes. Build roofs and stairs as stepped cubes (`enshrouded-geometry-templates.json` has the exact shapes).

### Coverage

- 2: write this cell. Must be used exactly when the cell is non-zero.
- 1: cell is 0, write air (clears the target). With paste mode `add` it keeps the target instead.
- 0: cell is 0, keep what is already in the world. Only source of "don't touch". The Mayor bundle uses only 1 and 2.

Use 0 for anything outside the building, or empty blueprint space will erase terrain.

### Prop row (13 comma fields)

`item_id, x, y, z, qx, qy, qz, qw, sx, sy, sz, template_uuid_high, template_uuid_low`

- `item_id`: decimal ItemInfo id. See `prop-catalog.json`. There is no debug name in the file. The loader needs a current placement recipe for the id.
- `x,y,z`: metres from local origin, prop pivot. Finite; not snapped to the grid (doors, candles sit at arbitrary fractions).
- `qx,qy,qz,qw`: rotation quaternion. Non-zero length. A yaw of a degrees is (0, sin(a/2), 0, cos(a/2)).
- `sx,sy,sz`: scale, 1,1,1 in the manor.
- Template UUID: two hex strings up to 16 digits, lower case, not all zero. From entity guid `aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee`: high = `ccccbbbbaaaaaaaa`, low = bytes of `ddddeeeeeeeeeeee` reversed. `prop-catalog.json` lists both for each item.

Lit lamps use a different template from the item's default. In the manor, 84 of 205 rows do: wall candle `Prop_Candle01_Wall_On`, chandelier `Prop_Castle_Chandelier_04_On`, wall lantern, torch, plate candle, firefly basket. The other 121 match the catalog exactly. To place a lamp that is on, copy these UUIDs from the manor frame. Catalog UUIDs for lamps give the item's default template, in-game result not verified.

Container contents are not stored. Storage props arrive empty.

## How the builder consumes a folder

1. `deployment/enshrouded-house-builder/catalog-host/host_planner.py`, `load_variant()`: only a variant registered in a trusted `CatalogApproval` (root, manifest sha256, approved file hashes) loads. The default is `mayor-manor-working-v1`. A new bundle must be registered; it does not run because it exists in a folder.
2. `_bundle_geometry()`: parses each part with `palworld_gm.enshrouded_blueprint_plan.parse_v7`, checks dims against `fit.json`, cell size 0.5, gap-free coverage, exactly one altar marker in the frame, and that the marker equals `source_anchor_world - source_origin_world`.
3. `placement_geometry()` / `build_plan()`: takes a fresh native observation of the altar, world, level, zone and existing props. Crop origin in the world = live altar position minus the bundle's altar-relative position. Rotation is in quarter turns about the altar, Y axis only (`rotate_v7_cells`, `rotate_position_about_anchor`, `rotate_quaternion_y`). Rotation applies to the whole bundle.
4. The altar body AABB is left out of voxels and prop checks. Everything must fit inside the altar's zone; level 1 is a 40 m cube, altar +/-20 m on each axis. The real altar is kept.
5. `catalog-runtime/catalog-build-transaction.lua` executes the plan, one tick at a time, each step journalled and read back:
   - safety gates (session, source bytes, altar pose, inventories)
   - delete old owned props, if replacing a prior build
   - restore prior voxels to the baseline, tile by tile
   - write new voxels tile by tile: `runtime.world.voxel.write(ox,oy,oz,sx,sy,sz,cells)` in grid coordinates
   - read back all geometry
   - spawn props one by one in frame order: `runtime.world.entity.spawn(high,low,{x,y,z},{qx,qy,qz,qw},item_id,0)`
   - final check of the whole region
   An unknown result stops and is never replayed automatically.

Order matters only as voxels first, props second. Props do not need to be sorted.

The older single-file path `custodian_house.lua` has stricter limits (no rotation, one source altar). Do not target it for new work.

## Limits

| Limit | Value | Enforced by |
|---|---|---|
| Cells in one part | 65,536 | `parse_v7`, planner, transaction |
| Bytes in one part | 16 MiB (planner), 1 MiB per file part (transaction) | see left |
| Cells in the whole crop | 1,000,000 | planner |
| New props | 2,000 | planner, transaction |
| Value range | 0-65535, coverage 0-2 | `parse_v7` |
| Cell size | exactly 0.5 | planner |
| Crop origin | aligned to the 0.5 m grid | planner |
| Occupied voxels in a part | at least one; an empty part is rejected | `parse_v7` |

The manor parts are 225-279 KB each, so the 1 MiB limit is not a problem at this density.

## Worked example from the manor

Crop origin is world 3782.5, 834.5, 1407. Dims 59,45,62, split into three parts of 59,15,62 at Y offsets 0, 15, 30.

Altar row, last line of the frame:

`3954785428,15.25,9.5,17.25,0,0,0,1,1,1,1,4e2560675bcf6d3e,e34aed9ab4daf9a0`

- World position 3782.5+15.25 = 3797.75, 834.5+9.5 = 844, 1407+17.25 = 1424.25, which matches `source_anchor_world`.
- Template guid `5bcf6d3e-6067-4e25-a0f9-dab49aed4ae3` gives high `4e25` + `6067` + `5bcf6d3e`, low `e3 4a ed 9a b4 da f9 a0`.
- Altar box runs local X 13.5-17.5, Y 8.5-10.5, Z 15.5-19.5, so cells X 27-34, Y 17-20, Z 31-38. All 256 are 0 with coverage 1: a clean hole for the altar plate.
- Floor: at X 40, Z 35, cells Y 19 and 20 hold 132, coverage 2, and Y 21 up is air. Top of cell 20 is local 10.5, world 845 = altar + 1 m. The floor is 1 m thick.
- Reading that cell: Y 20 is in part 2, local Y 5. Index = 40 + 59 * (5 + 15 * 35) = 31,310 (0-based) in line 8.
- First prop row: `4089251356,13,3.408...,8.520...,0,0.7071,0,-0.7071,1,1,1,45cb8c8ebf46d5b3,857a38cf714e9ba8`. Wall candle at local x 13, y 3.408, z 8.520. Quaternion is qx 0, qy 0.7071, qz 0, qw -0.7071: yaw -90 degrees. The UUID is the lit template `Prop_Candle01_Wall_On`.

## Writing a new bundle

1. Choose crop origin on the 0.5 m grid; keep the altar at a chosen offset. Floor top = altar Y + 1.
2. Fill a grid, with coverage 0 outside, 2 for solid, 1 for carved air inside rooms.
3. Leave the 8 x 8 x 4 cell altar box empty; leave open height above it for the bowl (1.3 m covers all levels).
4. Split along Y into parts of at most 65,536 cells, each with at least one voxel. Stack offsets in `fit.json`.
5. Write the frame: extent = whole crop, props, and exactly one altar row with the rows above.
6. Compute AABBs, hashes, `manifest.json`, then register a `CatalogApproval`. Not automatic.

Not verified in-game: new bundles, coverage 0 through the catalog planner (the Mayor bundle uses only 1 and 2), and any scale other than 1.
