import logging
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import AppKit
import Quartz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import notch_beam
import notch_island
import previews

AppKit.NSApplication.sharedApplication()


def controller(after_ms=1200):
    return types.SimpleNamespace(cfg=types.SimpleNamespace(crossing={
        "notch_after_ms": after_ms,
        "glow_colour": "signal",
    }))


def render(view, at):
    width, height = 300, 150
    rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, width, height, 8, 4, True, False, AppKit.NSCalibratedRGBColorSpace, width * 4, 32
    )
    context = AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    view.paint_frame(context.CGContext(), width, height, at, False)
    return bytes(rep.bitmapData())


def changed_bounds(first, second, width=300):
    changed = [index // 4 for index in range(0, len(first), 4)
               if first[index:index + 4] != second[index:index + 4]]
    if not changed:
        return None
    xs = [pixel % width for pixel in changed]
    ys = [pixel // width for pixel in changed]
    return min(xs), min(ys), max(xs), max(ys)


class HostedNotchCrossingPreviewTest(unittest.TestCase):
    def preview(self, base):
        return previews.notch_crossing_still(base, lambda: "signal", logging.getLogger("preview-test"),
                                              controller(), (185.0, 32.0))

    def test_outline_and_island_host_their_real_layer_trees_at_the_crossing_seam(self):
        for base, expected in ((notch_beam.NotchBeam, notch_beam.NotchBeam),
                               (notch_island.NotchIsland, notch_island.NotchIsland)):
            with self.subTest(style=expected.__name__):
                view = self.preview(base)
                self.assertIsInstance(view.renderer, expected)
                self.assertIsInstance(view.stage, Quartz.CALayer)
                self.assertAlmostEqual(view.still_at, 1.9)
                self.assertGreater(view.stage.bounds().size.width, view.notch[0])

    def test_reduced_motion_and_hover_intent_hold_the_same_canonical_still(self):
        view = self.preview(notch_island.NotchIsland)
        view.playing_since = 10.1
        with mock.patch.object(previews.time, "monotonic", return_value=10.0), \
                mock.patch.object(previews.effects_overlay, "reduce_motion", return_value=False):
            self.assertEqual(view._time(), view.still_at)
        with mock.patch.object(previews.time, "monotonic", return_value=10.8), \
                mock.patch.object(previews.effects_overlay, "reduce_motion", return_value=True):
            self.assertEqual(view._time(), view.still_at)
        with mock.patch.object(previews.time, "monotonic", return_value=10.8), \
                mock.patch.object(previews.effects_overlay, "reduce_motion", return_value=False):
            self.assertAlmostEqual(view._time(), 0.7)

    def test_real_outline_and_island_layer_trees_render_distinct_mid_crossing_bitmaps(self):
        outline = self.preview(notch_beam.NotchBeam)
        island = self.preview(notch_island.NotchIsland)
        outline_image = render(outline, outline.still_at)
        island_image = render(island, island.still_at)
        self.assertGreater(len(outline_image), 100)
        self.assertGreater(len(island_image), 100)
        self.assertNotEqual(outline_image, island_image)

    def test_canonical_bitmap_is_independent_of_the_previous_frame(self):
        for base in (notch_beam.NotchBeam, notch_island.NotchIsland):
            with self.subTest(style=base.__name__):
                view = self.preview(base)
                still = render(view, view.still_at)
                render(view, 3.0)
                self.assertEqual(render(view, view.still_at), still)

    def test_layer_pixels_stay_around_the_notch_seam(self):
        view = self.preview(notch_beam.NotchBeam)
        view.stage.setHidden_(True)
        without_marks = render(view, view.still_at)
        view.stage.setHidden_(False)
        with_marks = render(view, view.still_at)
        bounds = changed_bounds(without_marks, with_marks)
        self.assertIsNotNone(bounds)
        left, top, right, bottom = bounds
        # Centred on the notch, in a camera fitted to the crossing rather than the whole journey.
        self.assertGreaterEqual(left, 65)
        self.assertLessEqual(right, 235)
        self.assertAlmostEqual((left + right) / 2, 150, delta=8)
        self.assertGreaterEqual(top, 40)
        self.assertLessEqual(bottom, 115)

    def test_renderer_frame_uses_the_selected_notch_duration(self):
        short = self.preview(notch_beam.NotchBeam)
        long = previews.notch_crossing_still(notch_beam.NotchBeam, lambda: "signal",
                                             logging.getLogger("preview-test"), controller(3000), (185.0, 32.0))
        self.assertEqual(short.after(), 1.2)
        self.assertEqual(long.after(), 3.0)
        with mock.patch.object(notch_beam, "lap_finish", wraps=notch_beam.lap_finish) as finish:
            crossed = (notch_beam.LAP * 0.3 + notch_beam.LAP
                       * (previews.effects._P_CROSS - previews.effects._P_PUSH)
                       / notch_beam.turn_seconds(1.0)) % notch_beam.LAP
            short._set_renderer_frame(2.5, "signal", True)
            finish.assert_called_with(crossed, 1.2)
            finish.reset_mock()
            long._set_renderer_frame(2.5, "signal", True)
            finish.assert_called_with(crossed, 3.0)


if __name__ == "__main__":
    unittest.main()
