"""Geometry and optional native raster measurements; never opens a window."""

import argparse
import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from core import effects


STYLES = ("glow", "beam") + effects.EFFECT_IDS + ("locator",)


def pen_for(style, depth, kind="depart", width=1920, height=1200):
    fx = effects.LOCATOR if style == "locator" else effects.preview_effect(style)
    return effects.draw(fx, kind, effects.state(
        width, height, "mac", "switch" if style == "locator" else "edge",
        ["#58c8f0", "#a673ec", "#f05e8a"], effect_size_fraction=depth / min(width, height),
        region=SimpleNamespace(kind="edge", edge="right", x=width - 1, y=0, w=1, h=height),
        point=effects.point(width - 1, height / 2), edge="right", along=0.5,
        pressure=1.0, tick=None, since=0.18 if kind == "arrive" else None))


def path_box(pen):
    points = [segment[i:i + 2] for op in pen.ops for segment in op[1]
              for i in range(1, len(segment) - 1, 2)]
    return (min(x for x, y in points), min(y for x, y in points),
            max(x for x, y in points), max(y for x, y in points))


def raster_box(pen, ratio, width=1920, height=1200):
    from core.tests.render_effect_parity import Bitmap
    width, height = round(width * ratio), round(height * ratio)
    bitmap = Bitmap(width, height, scale=ratio, transparent=True)
    try:
        bitmap.draw([pen])
        alpha = bitmap.raw()[3::4].translate(bytes(0 if i < 8 else 1 for i in range(256)))
    finally:
        bitmap.close()
    rows = [(y, alpha[y * width:(y + 1) * width]) for y in range(height)]
    rows = [(y, row) for y, row in rows if row.strip(b"\0")]
    return (min(width - len(row.lstrip(b"\0")) for y, row in rows), rows[0][0],
            max(len(row.rstrip(b"\0")) for y, row in rows), rows[-1][0] + 1)


class NativeSizeRasterTests(unittest.TestCase):
    def test_native_marks_grow_at_one_and_two_device_pixels_per_point(self):
        for ratio in (1, 2):
            for style in STYLES:
                kind = "arrive" if style == "locator" else "depart"
                with self.subTest(ratio=ratio, style=style):
                    small = raster_box(pen_for(style, 24, kind), ratio)
                    large = raster_box(pen_for(style, 90, kind), ratio)
                    self.assertGreater((1920 * ratio - large[0]) /
                                       (1920 * ratio - small[0]), 2.8)


def measure(raster=False):
    rows = []
    for style in STYLES:
        kind = "arrive" if style == "locator" else "depart"
        for depth in (24, 90, 96, 300):
            pen = pen_for(style, depth, kind)
            box = path_box(pen)
            for ratio in (1, 2):
                row = dict(style=style, kind=kind, selected_points=depth, device_ratio=ratio,
                           selection_valid=effects.valid_effect_size_fraction(depth / 1200),
                           path_control_box_pixels=[round(v * ratio, 3) for v in box],
                           path_depth_pixels=round((1920 - box[0]) * ratio, 3),
                           path_height_pixels=round((box[3] - box[1]) * ratio, 3))
                if raster:
                    box_px = raster_box(pen, ratio)
                    row.update(alpha_box_pixels=box_px,
                               alpha_depth_pixels=1920 * ratio - box_px[0],
                               alpha_height_pixels=box_px[3] - box_px[1])
                rows.append(row)
    return rows


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--raster", action="store_true")
    args = parser.parse_args()
    args.output.write_text(json.dumps(measure(args.raster), indent=2) + "\n", encoding="utf-8")
