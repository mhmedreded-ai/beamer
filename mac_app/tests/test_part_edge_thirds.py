"""Map third hit bands and ledger segments share the saved third choice."""

import copy
import itertools
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import AppKit
import Quartz

AppKit.NSApplication.sharedApplication()

import diagram
import theme
from core import return_edge, ways
from test_crossing_page_machines import BEE, SEA, Page, press, token


class PartEdgeThirds(Page):
    def layout(self, side="right", scoped=False, second=False):
        peers = [(BEE, token(100), "Bee", {"side": side})]
        zones = [{"peer": BEE, "kind": "edge"}]
        if scoped:
            zones.append({"peer": BEE, "kind": "edge", "display": "main", "side": side,
                          "target_side": return_edge.OPPOSITE[side]})
        if second:
            peers.append((SEA, token(150), "Sea", {"side": "left"}))
            zones.append({"peer": SEA, "kind": "edge"})
        window = self.build(peers, zones)
        own = self.store.current()["machine_id"]
        x, y = {"right": (160, 0), "left": (-160, 0), "top": (0, -90), "bottom": (0, 90)}[side]
        tiles = {own: {"x": 0, "y": 0, "w": 160, "h": 90},
                 BEE: {"x": x, "y": y, "w": 160, "h": 90}}
        if scoped:
            for tile in tiles.values():
                tile["displays"] = {"main": {"x": 0, "y": 0, "w": 160, "h": 90}}
        if second:
            tiles[SEA] = {"x": -160, "y": 0, "w": 160, "h": 90}
        window._atlas_layout_changed(tiles, BEE, snap=False)
        self.pick(BEE)
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas._draw(False)
        return window

    def row(self):
        return self.window.crossing_ledger.rows[BEE]

    def picked(self):
        return [part for part, button in self.row().contact().segments.items()
                if button.accessibilityValue() == "on"]

    def pick_thirds(self, picked):
        self.whole_edge()
        for part in ways.PARTS:
            if part not in picked:
                press(self.row().contact().segments[part])

    def whole_edge(self):
        if self.picked():
            press(self.row().contact())
        press(self.row().contact())

    def third_point(self, third, offset=0, peer=BEE):
        canvas = self.window.atlas_canvas
        own = canvas.tiles[canvas.own_id]
        contact = ways._rect_contact(own, canvas.tiles[peer])
        frames, scale = canvas._map()
        frame = frames[canvas.own_id]
        _third, side, low, high = next(stretch for stretch in ways.third_stretches(contact, own)
                                      if stretch[0] == third)
        if side in ("left", "right"):
            return AppKit.NSMakePoint(frame[0] + (frame[2] if side == "right" else 0) + offset,
                                     frame[1] + ((low + high) / 2 - own["y"]) * scale)
        return AppKit.NSMakePoint(frame[0] + ((low + high) / 2 - own["x"]) * scale,
                                 frame[1] + (frame[3] if side == "bottom" else 0) + offset)

    def click_third(self, third, offset=0):
        canvas = self.window.atlas_canvas
        point = self.third_point(third, offset)
        event = mock.Mock()
        event.locationInWindow.return_value = point
        event.clickCount.return_value = 1
        event.modifierFlags.return_value = 0
        with mock.patch.object(canvas, "window"), \
                mock.patch.object(canvas, "convertPoint_fromView_", return_value=point), \
                mock.patch.object(diagram.AppKit, "NSCursor"):
            canvas.mouseDown_(event)
            self.assertIsNone(canvas.dragging)
            canvas.mouseUp_(event)

    def test_all_seven_choices_round_trip_ledger_saved_intent_and_map(self):
        window = self.layout()
        row = self.row()
        buttons = dict(row.contact().segments)
        for count in (1, 2, 3):
            for picked in itertools.combinations(ways.PARTS, count):
                with self.subTest(picked=picked):
                    self.pick_thirds(picked)
                    saved = self.ways_of(BEE)
                    self.assertEqual(ways.picked_thirds(saved["methods"], saved["parts"]), list(picked))
                    window._load_ways()
                    window._reflect()
                    window.atlas_canvas._draw(False)
                    self.assertEqual(self.picked(), list(picked))
                    self.assertEqual(window.atlas_canvas.states[BEE]["picked_thirds"], list(picked))
                    self.assertIs(self.row(), row)
                    for part, button in buttons.items():
                        self.assertIs(row.contact().segments[part], button)

    def test_map_clicks_each_third_on_each_side_and_ledger_follows(self):
        for side in ways.EDGES:
            self.layout(side)
            for third in ways.PARTS:
                with self.subTest(side=side, third=third):
                    self.whole_edge()
                    self.click_third(third, offset=4)
                    self.assertEqual(self.picked(), [part for part in ways.PARTS if part != third])
                    self.click_third(third, offset=-4)
                    self.assertEqual(self.picked(), list(ways.PARTS))

    def test_last_third_off_then_map_reopens_only_the_clicked_third(self):
        self.layout()
        self.pick_thirds(["start"])
        self.click_third("start")
        self.assertEqual(self.picked(), [])
        self.assertFalse(set(self.ways_of(BEE)["methods"]) & {"edge", "part"})
        self.click_third("end")
        self.assertEqual(self.picked(), ["end"])
        self.assertEqual(self.ways_of(BEE)["parts"], ["end"])
        self.whole_edge()
        self.assertEqual(self.picked(), list(ways.PARTS))

    def test_mouse_down_and_drag_wait_for_mouse_up_and_never_move_tiles(self):
        window = self.layout()
        canvas = window.atlas_canvas
        before = copy.deepcopy(canvas.tiles)
        point = self.third_point("middle")
        event = mock.Mock()
        event.locationInWindow.return_value = point
        with mock.patch.object(canvas, "window"), \
                mock.patch.object(canvas, "convertPoint_fromView_", return_value=point):
            canvas.mouseDown_(event)
            canvas.mouseDragged_(event)
            self.assertIsNone(canvas.dragging)
            self.assertEqual(canvas.tiles, before)
            self.assertEqual(self.picked(), list(ways.PARTS))
            canvas.mouseUp_(event)
        self.assertEqual(self.picked(), ["start", "end"])
        self.assertEqual(canvas.tiles, before)

    def test_releasing_in_another_third_cancels(self):
        self.layout()
        canvas = self.window.atlas_canvas
        with mock.patch.object(canvas, "window"), \
                mock.patch.object(canvas, "convertPoint_fromView_", side_effect=[
                    self.third_point("start"), self.third_point("end")]):
            canvas.mouseDown_(mock.Mock())
            canvas.mouseUp_(mock.Mock())
        self.assertEqual(self.picked(), list(ways.PARTS))
        self.assertIsNone(canvas.third_pressed)

    def test_tooltips_cursor_and_five_pixel_band(self):
        self.layout()
        canvas = self.window.atlas_canvas
        self.assertEqual(canvas._third_tooltip(BEE, "start"), "Top third: click to turn it off")
        self.pick_thirds(["start"])
        self.assertEqual(canvas._third_tooltip(BEE, "start"), "Top third: click to turn it off")
        self.assertEqual(canvas._third_tooltip(BEE, "end"), "Bottom third: click to turn it on")
        point = self.third_point("end", 5)
        self.assertEqual(canvas._third_hit(point), (BEE, "end"))
        self.assertIsNone(canvas._third_hit(self.third_point("end", 5.1)))
        with mock.patch.object(canvas, "convertPoint_fromView_", return_value=point), \
                mock.patch.object(diagram.AppKit, "NSCursor") as cursor:
            canvas.mouseMoved_(mock.Mock())
        cursor.pointingHandCursor.return_value.set.assert_called_once()

    def test_selected_tracks_breaks_and_other_peers_seam(self):
        window = self.layout(second=True)
        self.pick_thirds(["middle"])
        canvas = window.atlas_canvas
        canvas._draw(False)
        self.assertEqual(set(canvas.third_layers), {(BEE, "start", "track"), (BEE, "end", "track")})
        track = canvas.third_layers[(BEE, "start", "track")]
        self.assertEqual(track.lineWidth(), 2)
        self.assertAlmostEqual(track.opacity(), .3)
        self.assertEqual(track.lineCap(), Quartz.kCALineCapRound)
        self.assertTrue(Quartz.CGColorEqualToColor(track.strokeColor(), theme.cg("signal")))
        x, y, _width, height = canvas._map()[0][canvas.own_id]
        seam = [segment for segment in canvas.seam_segments if segment["peer"] == BEE]
        self.assertEqual([segment["kind"] for segment in sorted(seam, key=lambda s: s["start"][1])],
                         ["track", "seam", "track"])
        for segment in seam:
            self.assertEqual(segment["start"][0], segment["end"][0])
        ends = sorted(seam, key=lambda s: s["start"][1])
        for before, after, point in zip(ends, ends[1:], (y + height / 3, y + 2 * height / 3)):
            seen_end, seen_start = before["end"][1] + before["width"] / 2, after["start"][1] - after["width"] / 2
            self.assertAlmostEqual((seen_end + seen_start) / 2, point)
            self.assertAlmostEqual(seen_start - seen_end, diagram.THIRD_GAP)
        self.assertIn((canvas.own_id, SEA, "signal"), canvas.contact_layers)
        self.assertIsNone(canvas._third_hit(self.third_point("start", peer=SEA)))
        self.pick(SEA)
        canvas._draw(False)
        self.assertFalse(any(key[0] == BEE for key in canvas.third_layers))

    def test_picked_third_with_only_a_sliver_of_contact_is_not_lit(self):
        window = self.layout()
        window.windows_input.server.caps_of.return_value = ["layout"]
        self.pick_thirds(["start", "middle"])
        tiles = copy.deepcopy(self.store.current()["layout"])
        tiles[BEE].update(y=25, h=40)
        settings = self.store.current()
        settings["layout"] = tiles
        self.store.save_settings(settings)
        window._show_arrangement()
        canvas = window.atlas_canvas
        canvas._draw(False)
        self.assertEqual(canvas.states[BEE]["picked_thirds"], ["start", "middle"])
        self.assertEqual(canvas.states[BEE]["lit_thirds"], ["middle"])
        self.assertIn((BEE, "start", "track"), canvas.third_layers)

    def test_edge_off_survives_layout_compile_and_text_restores_whole(self):
        for scoped in (False, True):
            with self.subTest(scoped=scoped):
                window = self.layout(scoped=scoped)
                self.pick_thirds(["start", "end"])
                press(self.row().contact())
                window._atlas_layout_changed(copy.deepcopy(self.store.current()["layout"]), BEE, snap=False)
                self.assertFalse(set(self.ways_of(BEE)["methods"]) & {"edge", "part"})
                self.assertEqual(self.picked(), [])
                window.atlas_canvas._draw(False)
                self.assertFalse(any(key[-1] == "track" for key in window.atlas_canvas.third_layers))
                press(self.row().contact())
                self.assertEqual(self.picked(), list(ways.PARTS))
                self.assertEqual(self.ways_of(BEE)["methods"], ["edge"])
