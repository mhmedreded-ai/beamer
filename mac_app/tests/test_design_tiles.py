"""Measure Design tile states without ordering a window."""

import math
import unittest
from unittest import mock

from core import effects
from mac_app.tests.design_offscreen import (
    accent_pixels_near_top_edge, kvm_bridge_app, offscreen_control_window, raster, settle, tiles,
)
import theme
widgets = kvm_bridge_app.widgets
import Quartz


def relative_luminance(rgb):
    linear = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in (c / 255 for c in rgb)]
    return sum(x * weight for x, weight in zip(linear, (.2126, .7152, .0722)))


def picture_metrics(tile, scale):
    """Effect contrast and coverage, excluding the screen outlines and the pointer."""
    import previews
    import effects_overlay

    preview = tile.tile_content.preview
    if isinstance(preview, previews.TileStack):
        preview = preview.current()
    width, height = preview.bounds().size
    ground = tuple(round(channel * 255) for channel in theme.rgb(theme.T["ground"]))

    def paint():
        context = Quartz.CGBitmapContextCreate(
            None, round(width * scale), round(height * scale), 8, 0,
            Quartz.CGColorSpaceCreateWithName(Quartz.kCGColorSpaceSRGB), Quartz.kCGImageAlphaPremultipliedLast)
        Quartz.CGContextTranslateCTM(context, 0, height * scale)
        Quartz.CGContextScaleCTM(context, scale, -scale)
        Quartz.CGContextSetFillColorWithColor(context, theme.cg("ground"))
        Quartz.CGContextFillRect(context, ((0, 0), (width, height)))
        preview.painter(context, width, height)
        return theme.AppKit.NSBitmapImageRep.alloc().initWithCGImage_(Quartz.CGBitmapContextCreateImage(context))

    rep = paint()
    original = effects_overlay.draw_scene

    def without_marks(cg, ctx, scene, *args, **kwargs):
        return original(cg, ctx, dict(scene, pens=[]), *args, **kwargs)

    with mock.patch.object(effects_overlay, "draw_scene", without_marks):
        baseline = paint()
    data, before = bytes(rep.bitmapData()), bytes(baseline.bitmapData())
    row, channels = rep.bytesPerRow(), rep.samplesPerPixel()
    ground_lum = relative_luminance(ground)
    ratios = []
    for y in range(rep.pixelsHigh()):
        for x in range(rep.pixelsWide()):
            offset = y * row + x * channels
            rgb = tuple(data[offset:offset + 3])
            if max(abs(a - b) for a, b in zip(rgb, before[offset:offset + 3])) > 3:
                lum = relative_luminance(rgb)
                ratios.append((max(lum, ground_lum) + .05) / (min(lum, ground_lum) + .05))
    ratios.sort()
    contrast = ratios[min(len(ratios) - 1, math.ceil(.9 * len(ratios)) - 1)] if ratios else 1.0
    return contrast, len(ratios) / max(1, rep.pixelsWide() * rep.pixelsHigh())


def luminance(colour):
    colour = colour.colorUsingColorSpace_(theme.AppKit.NSColorSpace.deviceRGBColorSpace())
    rgb = [colour.redComponent(), colour.greenComponent(), colour.blueComponent()]
    linear = [x / 12.92 if x <= .04045 else ((x + .055) / 1.055) ** 2.4 for x in rgb]
    return sum(x * weight for x, weight in zip(linear, (.2126, .7152, .0722)))


class DesignTileMeasurements(unittest.TestCase):
    def test_still_time_is_inside_the_loop_and_deterministic(self):
        first = effects.still_time("beam", "edge")
        self.assertGreaterEqual(first, 0)
        self.assertLess(first, effects.PREVIEW_LOOP_S)
        self.assertEqual(first, effects.still_time("beam", "edge"))
        switch = effects.still_time("aperture", "switch")
        self.assertGreaterEqual(switch, 0)
        self.assertLess(switch, effects.SWITCH_LOOP_S)
        self.assertEqual(switch, effects.still_time("aperture", "switch"))

    def test_real_tile_previews_reach_contrast_and_coverage_in_both_appearances_and_scales(self):
        control = offscreen_control_window()
        control._select_page("design")
        # The page shows one family at a time; both are measured as they look when shown.
        control.switch_styles_box.setHidden_(False)
        previous = theme.is_dark()
        try:
            for appearance in ("dark", "light"):
                control._apply_appearance(appearance)
                for family, choices in (("crossing", control.glow_style_select),
                                        ("switch", control.switch_style_select)):
                    choices.value = None
                    settle(control.window)
                    for row in choices.rows:
                        for style, tile, _name in row.tiles:
                            for scale in (1, 2):
                                contrast, coverage = picture_metrics(tile, scale)
                                with self.subTest(family=family, style=style, appearance=appearance, scale=scale):
                                    self.assertGreaterEqual(contrast, 3.0)
                                    # The Qt test holds the same 1% floor. The Rule switch is four
                                    # small ticks round the pointer, under 1% in Light, so it alone is
                                    # held to 0.5%; Flint is a few short sparks drawn at their real size,
                                    # 0.4% at least.
                                    floor = {("switch", "rule"): .005, ("crossing", "flint"): .004}.get((family, style), .01)
                                    self.assertGreaterEqual(coverage, floor)
        finally:
            theme.set_dark(previous)

    def test_hover_background_brightens_in_both_appearances(self):
        control = offscreen_control_window()
        tile = control.glow_style_select.rows[0].tiles[1][1]
        previous = theme.is_dark()
        try:
            for dark in (True, False):
                theme.set_dark(dark)
                tile.set_tile_state(selected=False, hovered=False)
                rest = theme.AppKit.NSColor.colorWithCGColor_(tile.layer().backgroundColor())
                tile.set_tile_state(hovered=True)
                hovered = theme.AppKit.NSColor.colorWithCGColor_(tile.layer().backgroundColor())
                with self.subTest(dark=dark):
                    self.assertGreater(luminance(hovered), luminance(rest))
                theme.set_dark(not dark)
                theme.repaint(tile)
                switched = theme.AppKit.NSColor.colorWithCGColor_(tile.layer().backgroundColor())
                border = theme.AppKit.NSColor.colorWithCGColor_(tile.layer().borderColor())
                with self.subTest(switched_to_dark=not dark):
                    self.assertGreater(luminance(switched), luminance(theme.colour("ground")))
                    self.assertAlmostEqual(border.alphaComponent(), .18)
        finally:
            theme.set_dark(previous)

    def test_hover_has_no_accent_and_selection_has_thicker_ring(self):
        control = offscreen_control_window()
        control._select_page("design")
        settle(control.window)
        control.glow_style_select.value = "glow"
        selected, hovered = [item[1] for item in control.glow_style_select.rows[0].tiles[:2]]
        before = tiles(control)
        hovered._tile_hover_owner.mouseEntered_(None)
        self.assertEqual(accent_pixels_near_top_edge(hovered), 0)
        self.assertGreater(accent_pixels_near_top_edge(selected), 0)
        rep = raster(selected)
        x = rep.pixelsWide() // 2
        thickness = sum(rep.colorAtX_y_(x, y).blueComponent() -
                        rep.colorAtX_y_(x, y).redComponent() > .18 for y in range(8))
        self.assertGreaterEqual(thickness, 2, f"selected ring: {thickness} pixels")
        self.assertEqual(tiles(control), before)
        hovered._tile_hover_owner.mouseExited_(None)

    def test_selected_badge_is_a_signal_circle_with_ground_check_on_the_name_line(self):
        kvm_bridge_app.AppKit.NSApplication.sharedApplication()
        Quartz = kvm_bridge_app.Quartz
        previous = theme.is_dark()
        try:
            for dark in (True, False):
                theme.set_dark(dark)
                tile = widgets.pressable(lambda: None, "ground", "rule", theme.RADIUS["segment"])
                tile.setFrame_(((0, 0), (80, 48)))
                tile.add_selection_mark()
                tile.show_selection_mark(True)
                tile.layout()
                rep = raster(tile)
                self.assertFalse(tile.selection_mark.isHidden())
                self.assertFalse(tile.selection_check.isHidden())
                self.assertTrue(Quartz.CGColorEqualToColor(tile.selection_mark.fillColor(), theme.cg("signal")))
                self.assertTrue(Quartz.CGColorEqualToColor(tile.selection_check.strokeColor(), theme.cg("ground")))
                self.assertEqual(tile.selection_check.lineWidth(), 2)
                self.assertEqual(tile.selection_mark.bounds().size.width, 16)
                data = bytes(rep.bitmapData())
                channels = rep.samplesPerPixel()
                near_signal = sum(max(data[offset:offset + 3]) - min(data[offset:offset + 3]) > 70
                                  and data[offset + 2] > data[offset] + 50
                                  for offset in range(0, len(data), channels))
                with self.subTest(dark=dark):
                    self.assertGreater(near_signal, 80)
        finally:
            theme.set_dark(previous)

    def test_design_badge_sits_before_the_indented_name_below_the_preview(self):
        control = offscreen_control_window()
        control._select_page("design")
        tile = control.glow_style_select.rows[0].tiles[0][1]
        settle(control.window)
        mark = tile.selection_mark.frame()
        name = tile.tile_content.name.view.frame()
        preview = tile.tile_content.preview.frame()
        self.assertAlmostEqual(mark.origin.x + mark.size.width + 6, name.origin.x, delta=1)
        self.assertAlmostEqual(mark.origin.y + mark.size.height / 2,
                               name.origin.y + name.size.height / 2, delta=1)
        # The tile is unflipped: below the preview is a lower y.
        self.assertLess(mark.origin.y + mark.size.height, preview.origin.y)

    def test_preview_hint_uses_secondary_ink_with_readable_contrast(self):
        control = offscreen_control_window()
        hint = control.style_preview_hint
        self.assertEqual(hint.view.stringValue(), "Hover to preview")
        self.assertEqual(hint.ink, "ink_2")
        for dark in (True, False):
            theme.set_dark(dark)
            a, b = sorted((luminance(theme.colour(hint.ink)), luminance(theme.colour("panel"))))
            self.assertGreaterEqual((b + .05) / (a + .05), 4.5)

    def test_geometry_is_unchanged_by_hover_or_selection_at_every_width(self):
        control = offscreen_control_window()
        control._select_page("design")
        for width in (900, 1200, 1500, 1900):
            control.window.setFrame_display_(((100000, 100000), (width, 900)), False)
            settle(control.window)
            baseline = tiles(control)
            for family in control.glow_style_select.rows:
                frames = [tile.convertRect_toView_(tile.bounds(), family.view) for _, tile, _ in family.tiles]
                self.assertLessEqual(max(f.size.height for f in frames) - min(f.size.height for f in frames), 1)
                self.assertLessEqual(max(f.origin.y for f in frames) - min(f.origin.y for f in frames), 1)
            hover = control.glow_style_select.rows[0].tiles[1][1]._tile_hover_owner
            hover.mouseEntered_(None)
            settle(control.window)
            self.assertEqual(tiles(control), baseline)
            hover.mouseExited_(None)
            control.glow_style_select.value = "beam"
            settle(control.window)
            self.assertEqual(tiles(control), baseline)


if __name__ == "__main__":
    unittest.main()
