"""Isometric PNG preview of an Enshrouded house design (needs Pillow).

Example: python scripts/enshrouded_house_render.py docs/enshrouded-houses/workshop-fortress.json fortress.png --view ne
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import enshrouded_house as eh

COLOURS = {"stone": (150, 148, 140), "cobble": (110, 112, 120), "wood": (150, 105, 60),
           "shingle": (95, 80, 85), "thatch": (200, 170, 90), "altar": (255, 120, 20)}
#: Rotation into the drawing frame and, per view, the two original directions facing the viewer.
VIEWS = {
    "sw": (lambda x, y: (x, y), [(-1, 0), (0, -1)]),
    "se": (lambda x, y: (-y, x), [(0, 1), (-1, 0)]),
    "ne": (lambda x, y: (-x, -y), [(1, 0), (0, 1)]),
    "nw": (lambda x, y: (y, -x), [(0, -1), (1, 0)]),
}


def render(spec, view="sw", scale=7):
    from PIL import Image, ImageDraw

    house = eh.House(spec)
    voxels = dict(house.voxels)
    if spec.get("altar"):
        # The platform top is flush with the floor; only the bowl (about 2 m wide, 1 m) stands above it.
        bx, by, bz = eh.ALTAR_BOX
        for x in range(bx):
            for y in range(by):
                voxels[(x, y, -1)] = "altar"
        for x in range(bx // 2 - 2, bx // 2 + 2):
            for y in range(by // 2 - 2, by // 2 + 2):
                for z in range(2):
                    voxels[(x, y, z)] = "altar"
    rot, facing = VIEWS[view]

    def proj(x, y, z):
        return (x - y) * scale, -(x + y) * scale / 2 - z * scale * 1.15

    cells = [(*rot(x, y), z, m, (x, y, z)) for (x, y, z), m in voxels.items()]
    corners = [proj(*c[:3]) for c in cells]
    left, top = min(a for a, _ in corners) - 2 * scale, min(b for _, b in corners) - 3 * scale
    width = max(a for a, _ in corners) + 2 * scale - left
    height = max(b for _, b in corners) + 2 * scale - top
    image = Image.new("RGB", (int(width), int(height)), (238, 240, 232))
    draw = ImageDraw.Draw(image)

    def pt(x, y, z):
        a, b = proj(x, y, z)
        return a - left, b - top

    for rx, ry, z, m, (x, y, _) in sorted(cells, key=lambda c: (-(c[0] + c[1]), c[2])):
        base = COLOURS.get(m, (180, 0, 180))
        shade = lambda k: tuple(int(v * k) for v in base)
        if (x, y, z + 1) not in voxels:
            draw.polygon([pt(rx, ry, z + 1), pt(rx + 1, ry, z + 1), pt(rx + 1, ry + 1, z + 1), pt(rx, ry + 1, z + 1)], fill=shade(1.0))
        if (x + facing[0][0], y + facing[0][1], z) not in voxels:
            draw.polygon([pt(rx, ry, z), pt(rx, ry + 1, z), pt(rx, ry + 1, z + 1), pt(rx, ry, z + 1)], fill=shade(0.8))
        if (x + facing[1][0], y + facing[1][1], z) not in voxels:
            draw.polygon([pt(rx, ry, z), pt(rx + 1, ry, z), pt(rx + 1, ry, z + 1), pt(rx, ry, z + 1)], fill=shade(0.65))
    return image


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("design", type=Path)
    parser.add_argument("out", type=Path)
    parser.add_argument("--view", choices=sorted(VIEWS), default="sw")
    args = parser.parse_args(argv)
    render(json.loads(args.design.read_text(encoding="utf-8")), args.view).save(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
