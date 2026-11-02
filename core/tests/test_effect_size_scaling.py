"""Size must change the marks, including styles that never read the setting themselves."""

import unittest
from types import SimpleNamespace

from core import effects


def frame(style, depth, kind="depart", reduced=False, width=1920, height=1200):
    return effects.draw(effects.preview_effect(style), kind, effects.state(
        width, height, "windows", "edge", ["#58c8f0", "#a673ec", "#f05e8a"],
        effect_size_fraction=depth / min(width, height), reduced=reduced,
        region=SimpleNamespace(kind="edge", edge="right", x=width - 1, y=0, w=1, h=height),
        point=effects.point(width - 1, height / 2), edge="right", along=0.5,
        pressure=1.0, tick=None, since=None if kind == "depart" else 0.18))


class SizeScalingTests(unittest.TestCase):
    def test_preview_camera_does_not_zoom_out_when_size_grows(self):
        for style in effects.EFFECT_IDS:
            for place in ("edge", "corner", "notch", "switch"):
                with self.subTest(style=style, place=place):
                    self.assertEqual(effects.still_view(style, place, effect_size_fraction=0.02),
                                     effects.still_view(style, place, effect_size_fraction=0.075))

    def test_every_style_changes_its_drawn_depth_with_size(self):
        for style in ("glow", "beam") + effects.EFFECT_IDS:
            for kind in ("depart", "arrive"):
                if style in ("glow", "beam") and kind == "arrive":
                    continue
                for reduced in (False, True):
                    with self.subTest(style=style, kind=kind, reduced=reduced):
                        small = frame(style, 24, kind, reduced)
                        large = frame(style, 90, kind, reduced)
                        self.assertIsNotNone(small)
                        self.assertIsNotNone(large)
                        small_depth = 1920 - small.bounds[0]
                        large_depth = 1920 - large.bounds[0]
                        self.assertGreater(large_depth / small_depth, 2.8)

    def test_large_displays_can_use_a_quarter_of_the_shorter_side(self):
        for width, height, expected in ((3840, 2160, 540), (7680, 4320, 1080),
                                         (3840, 2160, 540)):
            with self.subTest(width=width, height=height):
                self.assertTrue(effects.valid_effect_size_fraction(0.25))
                self.assertEqual(effects.edge_depth(width, height, size_fraction=0.25), expected)

    def test_drawing_does_not_change_the_callers_geometry(self):
        state = effects.state(1920, 1200, "mac", "edge", ["#ffffff"],
                              effect_size_fraction=0.075, point=effects.point(1919, 600),
                              region=SimpleNamespace(kind="edge", edge="right", x=1919, y=0, w=1, h=1200),
                              pressure=1, tick=None, since=None, along=0.5)
        effects.draw(effects.effect("skin"), "depart", state)
        self.assertEqual((state.screen.w, state.point.x, state.region.h), (1920, 1919, 1200))


if __name__ == "__main__":
    unittest.main()
