"""The Crossing map's thirds breaks, shortcut-cycle badge, fit, seam and notch dash, on a standalone
canvas with synthetic tiles (rc14: ticks hung inside the lower tile and the tick read as a caret)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import AppKit
import Quartz

AppKit.NSApplication.sharedApplication()

import diagram
import theme
from core import ways as core_ways
from core.return_edge import Rect

PARTS = ["start", "middle", "end"]
OWN, LAPTOP, FEDORA, DESK = "0a" * 16, "1b" * 16, "2c" * 16, "3d" * 16
LABELS = {OWN: "This Mac", LAPTOP: "Loan laptop with a long name", FEDORA: "fedora", DESK: "Desk-PC-Workstation"}
TWO_WIDE = {"a": {"x": 0, "y": 0, "w": 1920, "h": 1080}, "b": {"x": 1920, "y": 0, "w": 1920, "h": 1080}}
# Two displays meeting at a corner beside a side contact: under This Mac and off its bottom-right
# corner, then This Mac's right-hand display off the bottom-right corner of a machine under its left one.
DISPLAY_CORNERS = ({OWN: {"x": 0, "y": 0, "w": 160, "h": 90}, DESK: {"x": 0, "y": 90, "w": 320, "h": 90, "displays": TWO_WIDE}},
                   {OWN: {"x": 0, "y": 0, "w": 160, "h": 90, "displays": TWO_WIDE}, DESK: {"x": 0, "y": 90, "w": 80, "h": 90}})
ROW = {OWN: {"x": 0, "y": 0, "w": 160, "h": 100},
       LAPTOP: {"x": -160, "y": 0, "w": 160, "h": 100},
       FEDORA: {"x": -320, "y": 0, "w": 160, "h": 100},
       DESK: {"x": 0, "y": 100, "w": 160, "h": 100}}
DISPLAYS = {"0": {"x": 0, "y": 0, "w": 2560, "h": 1440}, "1": {"x": 2560, "y": 0, "w": 1512, "h": 982}}
# An external display with the shorter built-in to its right, top edges level, and a machine below
# reaching under both: only the external's bottom edge touches it.
WITH_DISPLAYS = {OWN: {"x": 0, "y": 0, "w": 226, "h": 80, "displays": DISPLAYS},
                 DESK: {"x": 60, "y": 80, "w": 113, "h": 70}}


def peer_state(**changes):
    return {"state": "edge", "crosses": True, "linked": True, "in_use": True, "platform": "Windows",
            "picked_thirds": PARTS, "lit_thirds": PARTS, "edge_off": False, **changes}


def canvas_for(tiles=ROW, size=(850, 260), desk=None, own=None):
    canvas = diagram.AtlasCanvas.alloc().initWithFrame_(((0, 0), size)).setup()
    states = {key: peer_state() for key in tiles if key != OWN}
    states[OWN] = {"state": "this", "platform": "macOS", "machine_name": "Studio Mac", **(own or {})}
    states[DESK] = peer_state(**(desk or {}))
    canvas.show_layout(tiles, states, LABELS, OWN, DESK,
                       cycle={key: True for key in tiles if key != OWN}, cycle_enabled=True)
    canvas._draw(False)
    return canvas


def rect_of(layer):
    frame = layer.frame()
    return frame.origin.x, frame.origin.y, frame.size.width, frame.size.height


def intersects(one, two):
    return (one[0] < two[0] + two[2] and two[0] < one[0] + one[2]
            and one[1] < two[1] + two[3] and two[1] < one[1] + one[3])


class MapTicks(unittest.TestCase):
    def pieces(self, canvas, kind="seam"):
        found = [segment for segment in canvas.seam_segments if segment["peer"] == DESK and segment["kind"] == kind]
        return sorted(found, key=lambda segment: segment["start"][0])

    def assert_breaks(self, canvas, wanted):
        pieces = sorted(self.pieces(canvas, "seam") + self.pieces(canvas, "track"), key=lambda s: s["start"][0])
        self.assertEqual(len(pieces), len(wanted) + 1)
        for (before, after), point in zip(zip(pieces, pieces[1:]), wanted):
            seen_end = before["end"][0] + before["width"] / 2
            seen_start = after["start"][0] - after["width"] / 2
            self.assertAlmostEqual((seen_end + seen_start) / 2, point, delta=0.01)
            self.assertAlmostEqual(seen_start - seen_end, diagram.THIRD_GAP, delta=0.01)

    def test_breaks_are_centred_on_the_thirds_of_the_shared_stretch(self):
        canvas = canvas_for()
        x, _y, width, _height = canvas._map()[0][OWN]
        self.assert_breaks(canvas, [x + width / 3, x + 2 * width / 3])

    def test_an_unlit_third_breaks_the_same_way_as_a_lit_one(self):
        canvas = canvas_for(desk={"lit_thirds": ["start", "end"], "picked_thirds": ["start", "end"]})
        x, _y, width, _height = canvas._map()[0][OWN]
        self.assertEqual(len(self.pieces(canvas, "track")), 1)
        self.assert_breaks(canvas, [x + width / 3, x + 2 * width / 3])

    def test_partial_contact_breaks_at_this_macs_own_third_boundaries(self):
        tiles = {OWN: ROW[OWN], DESK: {"x": 40, "y": 100, "w": 80, "h": 100}}
        canvas = canvas_for(tiles)
        x, _y, width, _height = canvas._map()[0][OWN]
        self.assert_breaks(canvas, [x + width / 3, x + 2 * width / 3])
        pieces = self.pieces(canvas)
        scale = canvas._map()[1]
        self.assertAlmostEqual(pieces[0]["start"][0], x + 40 * scale + 2)
        self.assertAlmostEqual(pieces[-1]["end"][0], x + 120 * scale - 2)

    def test_nothing_for_the_thirds_is_drawn_outside_the_seam_band(self):
        for desk in ({}, {"lit_thirds": ["middle"], "picked_thirds": ["middle"]}):
            with self.subTest(desk=desk):
                canvas = canvas_for(desk=desk)
                line = canvas._map()[0][OWN][1] + canvas._map()[0][OWN][3]
                self.assertTrue(self.pieces(canvas))
                for segment in self.pieces(canvas) + self.pieces(canvas, "track"):
                    for point in (segment["start"], segment["end"]):
                        self.assertLessEqual(abs(point[1] - line) + segment["width"] / 2, 2.0)
                self.assertFalse(any(key[-1] == "divider" for key in canvas.third_layers))
                for layer in canvas.third_layers.values():
                    box = Quartz.CGPathGetBoundingBox(layer.path())
                    self.assertLessEqual(abs(box.origin.y - line) + layer.lineWidth() / 2, 2.0)
                    self.assertEqual(box.size.height, 0)

    def test_edge_off_draws_nothing_for_the_thirds(self):
        canvas = canvas_for(desk={"crosses": False, "edge_off": True, "picked_thirds": [], "lit_thirds": []})
        self.assertEqual(self.pieces(canvas) + self.pieces(canvas, "track"), [])
        self.assertEqual(canvas.third_layers, {})
        self.assertEqual(len(canvas._third_segments(canvas._map()[0])), 3)

    def test_unselected_peer_seam_is_one_unbroken_line(self):
        canvas = canvas_for()
        laptop = [segment for segment in canvas.seam_segments if segment["peer"] == LAPTOP]
        self.assertEqual(len(laptop), 1)
        x, y, _width, height = canvas._map()[0][OWN]
        self.assertEqual(laptop[0]["start"], (x, y + 2))
        self.assertEqual(laptop[0]["end"], (x, y + height - 2))

    def test_lit_seam_lies_on_the_shared_edge_and_spans_the_shared_stretch(self):
        canvas = canvas_for()
        frames = canvas._map()[0]
        own, desk = frames[OWN], frames[DESK]
        self.assertAlmostEqual(own[1] + own[3], desk[1], delta=0.5)
        pieces = self.pieces(canvas)
        for segment in pieces:
            self.assertAlmostEqual(segment["start"][1], own[1] + own[3], delta=0.5)
            self.assertAlmostEqual(segment["end"][1], desk[1], delta=0.5)
        self.assertAlmostEqual(pieces[0]["start"][0], own[0] + 2)
        self.assertAlmostEqual(pieces[-1]["end"][0], own[0] + own[2] - 2)

    def test_badge_never_meets_the_name_at_any_tile_height(self):
        for size, band in (((850, 260), "row"), ((260, 260), "small"), ((200, 260), "tiny")):
            canvas = canvas_for(size=size)
            frames = canvas._map()[0]
            for key, frame in frames.items():
                with self.subTest(band=band, peer=LABELS[key]):
                    {"row": lambda: self.assertGreaterEqual(frame[3], 56),
                     "small": lambda: self.assertTrue(44 <= frame[3] < 56),
                     "tiny": lambda: self.assertLess(frame[3], 44)}[band]()
                    title = rect_of(canvas.label_for_tile[key])
                    for value, wanted in zip(title, canvas._title_rect(key, frame)):
                        self.assertAlmostEqual(value, wanted)
                    if key == OWN:
                        self.assertTrue(canvas.cycle_for_tile[key].isHidden())
                        self.assertAlmostEqual(title[0], frame[0] + 8)
                        continue
                    box = rect_of(canvas.cycle_for_tile[key])
                    self.assertFalse(canvas.cycle_for_tile[key].isHidden())
                    for value, wanted in zip(box, canvas._cycle_box(frame)):
                        self.assertAlmostEqual(value, wanted)
                    self.assertEqual(box[2:], (16, 16))
                    self.assertAlmostEqual(box[0], frame[0] + frame[2] - 24)
                    self.assertFalse(intersects(box, title))
                    if band == "small":
                        self.assertGreaterEqual(title[2], frame[2] - 16 - 1e-9)
                        self.assertAlmostEqual(title[1], frame[1] + 4)
                        self.assertAlmostEqual(box[1], frame[1] + frame[3] - 20)
                    else:
                        self.assertAlmostEqual(box[1] + 8, title[1] + title[3] / 2)
                    if band == "tiny":
                        self.assertAlmostEqual(title[0] + title[2], box[0] - 6)

    def test_a_name_that_fits_stays_centred_and_a_long_one_takes_the_room_left_of_the_badge(self):
        canvas = canvas_for()
        frames = canvas._map()[0]
        short, long = rect_of(canvas.label_for_tile[FEDORA]), rect_of(canvas.label_for_tile[LAPTOP])
        fedora, laptop = frames[FEDORA], frames[LAPTOP]
        self.assertAlmostEqual(short[0], fedora[0] + 30)
        self.assertAlmostEqual(short[0] - fedora[0], fedora[0] + fedora[2] - short[0] - short[2])
        self.assertAlmostEqual(long[0], laptop[0] + 8)
        self.assertAlmostEqual(long[0] + long[2], canvas._cycle_box(laptop)[0] - 6)

    def test_cycle_hit_tooltip_and_accessibility_frame_share_the_box(self):
        canvas = canvas_for()
        frame = canvas._map()[0][DESK]
        x, y, width, height = canvas._cycle_box(frame)
        hit = canvas._cycle_hit_rect(frame)
        self.assertEqual(hit, (x - 4, y - 4, 24, 24))
        self.assertTrue(canvas._cycle_hit(DESK, AppKit.NSMakePoint(x + 8, y + 8)))
        self.assertTrue(canvas._cycle_hit(DESK, AppKit.NSMakePoint(hit[0] + 0.5, hit[1] + 0.5)))
        self.assertFalse(canvas._cycle_hit(DESK, AppKit.NSMakePoint(hit[0] - 1, y + 8)))
        self.assertFalse(canvas._cycle_hit(OWN, AppKit.NSMakePoint(x + 8, y + 8)))
        element = diagram.AtlasAccessibleElement.alloc().initWithCanvas_key_kind_(canvas, DESK, "cycle")
        found = element.accessibilityFrame()
        self.assertEqual((found.origin.x, found.origin.y, found.size.width, found.size.height), hit)

    def test_cycle_hit_follows_the_badge_under_the_name(self):
        canvas = canvas_for(size=(260, 260))
        frame = canvas._map()[0][DESK]
        x, y, _width, _height = canvas._cycle_box(frame)
        self.assertEqual(canvas._cycle_hit_rect(frame), (x - 4, y - 4, 24, 24))
        self.assertTrue(canvas._cycle_hit(DESK, AppKit.NSMakePoint(x + 8, y + 8)))
        element = diagram.AtlasAccessibleElement.alloc().initWithCanvas_key_kind_(canvas, DESK, "cycle")
        found = element.accessibilityFrame()
        self.assertEqual((found.origin.x, found.origin.y, found.size.width, found.size.height),
                         canvas._cycle_hit_rect(frame))

    def test_cycle_box_styles(self):
        canvas = canvas_for()
        layer = canvas.cycle_for_tile[DESK]
        check = layer.sublayers()[0]
        self.assertEqual(layer.cornerRadius(), 3)
        self.assertEqual(layer.borderWidth(), 0)
        self.assertTrue(Quartz.CGColorEqualToColor(layer.backgroundColor(), theme.cg("ink_2")))
        self.assertFalse(check.isHidden())
        self.assertTrue(Quartz.CGColorEqualToColor(check.strokeColor(), theme.cg("ground")))
        self.assertAlmostEqual(check.lineWidth(), 1.8)
        canvas.toggle_cycle(DESK)
        self.assertEqual(layer.borderWidth(), 1)
        self.assertTrue(Quartz.CGColorEqualToColor(layer.backgroundColor(), theme.cg("well")))
        self.assertTrue(Quartz.CGColorEqualToColor(layer.borderColor(), theme.cg("edge")))
        self.assertTrue(check.isHidden())

    def test_check_glyph_is_a_tick_not_a_caret(self):
        canvas = canvas_for()
        bounds = canvas.bounds()
        rep = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
            None, int(bounds.size.width * 2), int(bounds.size.height * 2), 8, 4, True, False,
            AppKit.NSDeviceRGBColorSpace, 0, 0)
        rep.setSize_(bounds.size)
        canvas.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
        x, y, width, height = canvas._cycle_box(canvas._map()[0][DESK])
        ground = theme.rgb(theme.T["ground"])
        fill = theme.rgb(theme.T["ink_2"])
        ink = []
        for py in range(round((y + 2) * 2), round((y + height - 2) * 2)):
            for px in range(round((x + 2) * 2), round((x + width - 2) * 2)):
                colour = rep.colorAtX_y_(px, py).colorUsingColorSpace_(AppKit.NSColorSpace.deviceRGBColorSpace())
                rgb = (colour.redComponent(), colour.greenComponent(), colour.blueComponent())
                if sum((a - b) ** 2 for a, b in zip(rgb, ground)) < sum((a - b) ** 2 for a, b in zip(rgb, fill)):
                    ink.append((px / 2, py / 2))
        self.assertGreater(len(ink), 10)
        lowest = max(point[1] for point in ink)
        bottom = [point[0] for point in ink if point[1] == lowest]
        highest = min(point[1] for point in ink)
        top = [point[0] for point in ink if point[1] == highest]
        centre = x + width / 2
        self.assertLess(sum(bottom) / len(bottom), centre)
        self.assertGreater(sum(top) / len(top), centre)

    def test_map_fits_and_centres_the_whole_arrangement(self):
        for size in ((850, 260), (240, 260), (480, 260)):
            with self.subTest(size=size):
                canvas = canvas_for(size=size)
                frames, scale = canvas._map()
                left = min(frame[0] for frame in frames.values())
                right = max(frame[0] + frame[2] for frame in frames.values())
                top = min(frame[1] for frame in frames.values())
                bottom = max(frame[1] + frame[3] for frame in frames.values())
                self.assertAlmostEqual((left + right) / 2, size[0] / 2, delta=1)
                self.assertAlmostEqual((top + bottom) / 2, size[1] / 2, delta=1)
                room_w, room_h = size[0] - 2 * canvas.INSET, size[1] - 2 * canvas.INSET
                self.assertTrue(abs(scale - 1.15) < 1e-9 or abs(right - left - room_w) <= 1
                                or abs(bottom - top - room_h) <= 1, (scale, right - left, bottom - top))
                self.assertLessEqual(right - left, room_w + 1e-6)
                self.assertLessEqual(bottom - top, room_h + 1e-6)

    def test_notch_span_picks_the_top_display_that_holds_the_notch(self):
        side_by_side = {"0": Rect(0, 0, 2560, 1440), "1": Rect(2560, 0, 1512, 982)}
        self.assertEqual(diagram.notch_span((3216, 3416), side_by_side), ("1", 656 / 1512, 856 / 1512))
        below = {"0": Rect(2000, -1440, 2560, 1440), "1": Rect(0, 0, 1512, 982)}
        self.assertIsNone(diagram.notch_span((656, 856), below))
        self.assertIsNone(diagram.notch_span(None, side_by_side))
        self.assertIsNone(diagram.notch_span((3216, 3416), {}))
        self.assertIsNone(diagram.notch_span((2500, 2700), side_by_side))

    def test_notch_dash_sits_over_the_notch_on_the_built_in_display(self):
        span = ("1", 656 / 1512, 856 / 1512)
        tiles = {OWN: {"x": 0, "y": 0, "w": 226, "h": 80, "displays": DISPLAYS},
                 DESK: {"x": 60, "y": 80, "w": 113, "h": 70}}
        canvas = canvas_for(tiles, desk={"notch_way": True}, own={"notch_span": span})
        frame, scale = canvas._map()[0][OWN], canvas._map()[1]
        screen = next(screen for screen in core_ways._display_frames(tiles[OWN]) if screen["display"] == "1")
        left = frame[0] + screen["x"] * scale
        box = Quartz.CGPathGetBoundingBox(canvas.mark_layers[(DESK, "notch")].path())
        self.assertAlmostEqual(box.origin.x, left + span[1] * screen["w"] * scale, places=6)
        self.assertAlmostEqual(box.origin.x + box.size.width, left + span[2] * screen["w"] * scale, places=6)
        self.assertAlmostEqual(box.origin.y, frame[1] + screen["y"] * scale - 2)
        self.assertAlmostEqual(box.size.height, 4)
        self.assertGreater(box.origin.x, frame[0] + frame[2] / 2)

    def test_notch_dash_falls_back_to_the_tile_top_centre(self):
        for tiles, own in ((ROW, {"notch_span": ("1", 0.4, 0.6)}),
                           ({OWN: {"x": 0, "y": 0, "w": 226, "h": 80, "displays": DISPLAYS},
                             DESK: {"x": 60, "y": 80, "w": 113, "h": 70}}, {})):
            with self.subTest(displays=bool(tiles[OWN].get("displays"))):
                canvas = canvas_for(tiles, desk={"notch_way": True}, own=own)
                x, y, width, _height = canvas._map()[0][OWN]
                box = Quartz.CGPathGetBoundingBox(canvas.mark_layers[(DESK, "notch")].path())
                self.assertEqual((box.origin.x, box.origin.y, box.size.width, box.size.height),
                                 (x + width / 2 - 7, y - 2, 14, 4))

    def external(self, canvas):
        return next(screen for screen in core_ways._display_frames(WITH_DISPLAYS[OWN]) if screen["display"] == "0")

    def test_with_displays_the_seam_runs_only_where_a_display_edge_touches_the_machine(self):
        for desk in ({}, {"lit_thirds": ["start", "middle"], "picked_thirds": ["start", "middle"]}):
            with self.subTest(desk=desk):
                canvas = canvas_for(WITH_DISPLAYS, desk=desk)
                frame, scale = canvas._map()[0][OWN], canvas._map()[1]
                external = self.external(canvas)
                bottom = frame[1] + (external["y"] + external["h"]) * scale
                drawn = self.pieces(canvas) + self.pieces(canvas, "track")
                self.assertTrue(drawn)
                for segment in drawn:
                    self.assertAlmostEqual(segment["start"][1], bottom)
                    self.assertAlmostEqual(segment["end"][1], bottom)
                    self.assertGreaterEqual(segment["start"][0] - segment["width"] / 2, frame[0] + 60 * scale)
                    self.assertLessEqual(segment["end"][0] + segment["width"] / 2,
                                         frame[0] + (external["x"] + external["w"]) * scale)

    def test_with_displays_the_breaks_are_at_that_displays_thirds_inside_the_contact(self):
        canvas = canvas_for(WITH_DISPLAYS)
        frame, scale = canvas._map()[0][OWN], canvas._map()[1]
        external = self.external(canvas)
        self.assert_breaks(canvas, [frame[0] + (external["x"] + 2 * external["w"] / 3) * scale])
        thirds = [(segment[1], segment[3][0], segment[4][0]) for segment in canvas._third_segments(canvas._map()[0])]
        self.assertEqual([third for third, _start, _end in thirds], ["middle", "end"])
        self.assertAlmostEqual(thirds[0][1], frame[0] + 60 * scale)
        self.assertAlmostEqual(thirds[1][2], frame[0] + (external["x"] + external["w"]) * scale)
        point = AppKit.NSMakePoint((thirds[1][1] + thirds[1][2]) / 2, frame[1] + 80 * scale + 4)
        self.assertEqual(canvas._third_hit(point), (DESK, "end"))
        self.assertEqual(canvas.view_stringForToolTip_point_userData_(canvas, 0, point, None),
                         "Right third: click to turn it off")

    def test_titles_draw_above_their_tiles_displays(self):
        tiles = {**WITH_DISPLAYS, DESK: {**WITH_DISPLAYS[DESK], "displays": {"9": {"x": 0, "y": 0, "w": 1920, "h": 1080}}}}
        canvas = canvas_for(tiles)
        root = list(canvas.layer().sublayers())
        for key in (OWN, DESK):
            with self.subTest(peer=LABELS[key]):
                title = canvas.label_for_tile[key]
                self.assertFalse(title.isHidden())
                for layer in canvas.display_layers[key]:
                    self.assertGreater(root.index(title), root.index(layer))
                    self.assertGreater(title.zPosition(), layer.zPosition())
        bounds = canvas.bounds()
        rep = canvas.bitmapImageRepForCachingDisplayInRect_(bounds)
        canvas.cacheDisplayInRect_toBitmapImageRep_(bounds, rep)
        ratio = rep.pixelsWide() / bounds.size.width
        for key in (OWN, DESK):
            x, y, width, height = rect_of(canvas.label_for_tile[key])
            bright = 0
            for py in range(round(y * ratio), round((y + height) * ratio)):
                for px in range(round(x * ratio), round((x + width) * ratio)):
                    colour = rep.colorAtX_y_(px, py).colorUsingColorSpace_(AppKit.NSColorSpace.deviceRGBColorSpace())
                    bright += colour.redComponent() > 0.5
            with self.subTest(peer=LABELS[key]):
                self.assertGreater(bright, 20)

    def test_a_corner_where_two_displays_meet_beside_a_side_contact_is_not_drawn(self):
        for tiles in DISPLAY_CORNERS:
            for selected in (DESK, OWN):
                with self.subTest(tiles=sorted(tiles[DESK]), selected=selected == DESK):
                    canvas = canvas_for(tiles)
                    canvas.selected = selected
                    canvas._draw(False)
                    box = Quartz.CGPathGetBoundingBox(canvas.contact_layers[(OWN, DESK, "signal")].path())
                    self.assertEqual(box.size.height, 0)
                    self.assertEqual({segment["side"] for segment in canvas.seam_segments}, {"bottom"})

    def test_the_join_between_a_machines_displays_is_not_a_break(self):
        tiles = {OWN: {"x": 0, "y": 0, "w": 160, "h": 90},
                 DESK: {"x": 0, "y": 90, "w": 160, "h": 90,
                        "displays": {"a": {"x": 0, "y": 0, "w": 1920, "h": 1080},
                                     "b": {"x": 1920, "y": 0, "w": 1920, "h": 1080}}}}
        for lit in (["start"], PARTS):
            with self.subTest(lit=lit):
                canvas = canvas_for(tiles, desk={"picked_thirds": lit, "lit_thirds": lit})
                x, _y, width, _height = canvas._map()[0][OWN]
                self.assert_breaks(canvas, [x + width / 3, x + 2 * width / 3])


if __name__ == "__main__":
    unittest.main()
