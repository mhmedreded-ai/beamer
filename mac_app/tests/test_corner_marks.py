import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import AppKit
import Quartz
import diagram

AppKit.NSApplication.sharedApplication()

from test_crossing_page_machines import BEE, SEA, Page, token
from wake import WakingController


class CornerMarks(Page):
    def setUp(self):
        super().setUp()
        capture = mock.patch.object(WakingController, "start_input_capture")
        capture.start()
        self.addCleanup(capture.stop)

    def marked(self, corner="top_right", extra=None, off=False, notch=False, has_notch=False):
        zones = [{"peer": BEE, "kind": "corner", "corner": corner, "edge": "right", "off": off}]
        if notch:
            zones.append({"peer": BEE, "kind": "notch", "off": off})
        window = self.build([(BEE, token(100), "Bee", {"side": "right", **(extra or {})})], zones)
        window.has_notch = has_notch
        window.chosen_peer = BEE
        window._show_arrangement([])
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas._draw(False)
        return window

    def assert_bounds(self, layer, expected):
        bounds = Quartz.CGPathGetBoundingBox(layer.path())
        actual = (bounds.origin.x, bounds.origin.y, bounds.size.width, bounds.size.height)
        for value, wanted in zip(actual, expected):
            self.assertAlmostEqual(value, wanted)

    def test_each_corner_pad_and_halo_are_centred_on_this_macs_vertex(self):
        for corner in ("top_left", "top_right", "bottom_left", "bottom_right"):
            with self.subTest(corner=corner):
                canvas = self.marked(corner).atlas_canvas
                key = (BEE, "corner", corner)
                self.assertEqual(set(canvas.mark_layers), {key})
                x, y, width, height = canvas._map()[0][canvas.own_id]
                left = (x if corner.endswith("left") else x + width) - 8
                top = (y if corner.startswith("top") else y + height) - 8
                layer = canvas.mark_layers[key]
                self.assert_bounds(layer, (left, top, 16, 16))
                halo = canvas.corner_halo_layers[key]
                chevron = layer.sublayers()[0]
                self.assert_bounds(halo, (left - 3, top - 3, 22, 22))
                self.assertEqual(chevron.lineWidth(), 1.8)
                self.assertEqual(chevron.lineCap(), Quartz.kCALineCapRound)
                self.assertGreater(layer.zPosition(), canvas.layer_for_tile[canvas.own_id].zPosition())

    def test_two_machines_keep_their_corners_and_selected_opacity(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"}),
                             (SEA, token(150), "Sea", {"side": "left"})],
                            [{"peer": BEE, "kind": "corner", "corner": "top_right", "edge": "right"},
                             {"peer": SEA, "kind": "corner", "corner": "bottom_left", "edge": "left"}])
        window.chosen_peer = BEE
        window._show_arrangement([])
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas._draw(False)
        bee = canvas.mark_layers[(BEE, "corner", "top_right")]
        sea = canvas.mark_layers[(SEA, "corner", "bottom_left")]
        self.assertEqual(len(canvas.mark_layers), 2)
        self.assertAlmostEqual(bee.opacity(), 1.0)
        self.assertAlmostEqual(sea.opacity(), 0.55)
        window.chosen_peer = SEA
        window._show_arrangement([])
        self.assertIs(canvas.mark_layers[(BEE, "corner", "top_right")], bee)
        self.assertIs(canvas.mark_layers[(SEA, "corner", "bottom_left")], sea)
        self.assertAlmostEqual(bee.opacity(), 0.55)
        self.assertAlmostEqual(sea.opacity(), 1.0)

    def test_a_corner_only_machine_has_its_pad_and_no_seam(self):
        canvas = self.marked("top_right").atlas_canvas
        self.assertTrue(canvas.states[BEE]["crosses"])
        self.assertIn((BEE, "corner", "top_right"), canvas.mark_layers)
        self.assertNotIn((canvas.own_id, BEE, "signal"), canvas.contact_layers)

    def test_disabled_ways_have_no_marks(self):
        canvas = self.marked(off=True, notch=True, has_notch=True).atlas_canvas
        self.assertFalse(canvas.states[BEE]["corner_ways"])
        self.assertFalse(canvas.states[BEE].get("notch_way"))
        self.assertEqual(canvas.mark_layers, {})

    def test_corner_and_notch_stay_visible_when_edge_is_off(self):
        window = self.marked(notch=True, has_notch=True)
        window._write_ways(BEE, edge=False)
        window._show_arrangement([])
        canvas = window.atlas_canvas
        self.assertEqual(set(canvas.mark_layers), {(BEE, "corner", "top_right"), (BEE, "notch")})
        self.assertFalse(canvas.states[BEE]["picked_thirds"])

    def test_left_out_dormant_and_not_in_use_machines_have_no_marks(self):
        for extra in ({"left_out": True}, {"dormant": True}, {"in_use": False}):
            with self.subTest(extra=extra):
                canvas = self.marked(extra=extra, notch=True, has_notch=True).atlas_canvas
                self.assertFalse(canvas.states[BEE]["corner_ways"])
                self.assertFalse(canvas.states[BEE].get("notch_way"))
                self.assertEqual(canvas.mark_layers, {})

    def test_notch_tab_is_centred_on_this_macs_top_border(self):
        canvas = self.marked(notch=True, has_notch=True).atlas_canvas
        layer = canvas.mark_layers[(BEE, "notch")]
        x, y, width, _height = canvas._map()[0][canvas.own_id]
        self.assert_bounds(layer, (x + width / 2 - 7, y - 2, 14, 4))
        self.assertAlmostEqual(layer.opacity(), 1.0)
        canvas.selected = canvas.own_id
        canvas._draw(False)
        self.assertIs(canvas.mark_layers[(BEE, "notch")], layer)
        self.assertAlmostEqual(layer.opacity(), 0.55)

    def test_notch_requires_both_the_way_and_a_notched_mac(self):
        for notch, has_notch in ((False, True), (True, False), (False, False)):
            with self.subTest(notch=notch, has_notch=has_notch):
                canvas = self.marked(notch=notch, has_notch=has_notch).atlas_canvas
                self.assertNotIn((BEE, "notch"), canvas.mark_layers)

    def test_corner_bounds_with_display_metadata_still_use_this_macs_tile(self):
        canvas = self.marked("bottom_right").atlas_canvas
        tiles = {peer: dict(tile) for peer, tile in canvas.tiles.items()}
        tiles[canvas.own_id]["displays"] = {
            "main": {"x": 0, "y": 0, "w": 1728, "h": 1117},
            "second": {"x": -1280, "y": 0, "w": 1280, "h": 1024},
        }
        canvas.show_layout(tiles, canvas.states, canvas.labels, canvas.own_id, BEE)
        x, y, width, height = canvas._map()[0][canvas.own_id]
        self.assert_bounds(canvas.mark_layers[(BEE, "corner", "bottom_right")],
                           (x + width - 8, y + height - 8, 16, 16))

    def test_refresh_keeps_the_same_layers(self):
        window = self.marked(notch=True, has_notch=True)
        canvas = window.atlas_canvas
        layers = dict(canvas.mark_layers)
        window._show_arrangement([])
        self.assertEqual(set(canvas.mark_layers), set(layers))
        for key, layer in layers.items():
            self.assertIs(canvas.mark_layers[key], layer)
        canvas.setFrame_(((0, 0), (600, canvas.HEIGHT)))
        canvas._draw(False)
        for key, layer in layers.items():
            self.assertIs(canvas.mark_layers[key], layer)

    def test_turning_ways_off_removes_stale_layers(self):
        window = self.marked(notch=True, has_notch=True)
        canvas = window.atlas_canvas
        layers = list(canvas.mark_layers.values())
        window._write_ways(BEE, corner=False, notch=False)
        window._load_ways()
        window._show_arrangement([])
        self.assertEqual(canvas.mark_layers, {})
        for layer in layers:
            self.assertIsNone(layer.superlayer())


class CanvasCornerGeometry(unittest.TestCase):
    def canvas(self, side="right", picked=None, edge_off=False):
        canvas = diagram.AtlasCanvas.alloc().init().setup()
        own = {"x": 100, "y": 100, "w": 160, "h": 100}
        peer = {"x": 260 if side == "right" else 100, "y": 100 if side == "right" else 0,
                "w": 160, "h": 100}
        canvas.own_id, canvas.selected = "own", BEE
        canvas.tiles = {"own": own, BEE: peer}
        canvas.states = {BEE: {"state": "edge", "crosses": not edge_off, "edge_off": edge_off,
                              "linked": True, "picked_thirds": picked or [],
                              "lit_thirds": picked or [], "corner_ways": ["top_right"]}}
        return canvas, {"own": (30, 40, 160, 100), BEE: (190, 40, 160, 100)}

    def test_corner_mark_is_square_pad_with_halo_and_outward_chevron(self):
        canvas, frames = self.canvas()
        canvas._draw_marks(frames)
        pad = canvas.mark_layers[(BEE, "corner", "top_right")]
        halo = canvas.corner_halo_layers[(BEE, "corner", "top_right")]
        chevron = pad.sublayers()[0]
        for layer, wanted in ((pad, (182, 32, 16, 16)), (halo, (179, 29, 22, 22)),
                              (chevron, (186.5, 36.5, 7, 7))):
            bounds = Quartz.CGPathGetBoundingBox(layer.path())
            actual = (bounds.origin.x, bounds.origin.y, bounds.size.width, bounds.size.height)
            self.assertEqual(actual, wanted)
        self.assertAlmostEqual(chevron.lineWidth(), 1.8)
        self.assertEqual(chevron.lineCap(), Quartz.kCALineCapRound)

    def test_lit_seam_stops_eleven_pixels_before_corner_halo(self):
        canvas, frames = self.canvas(picked=["start", "middle", "end"])
        canvas._draw_contacts(frames)
        bounds = Quartz.CGPathGetBoundingBox(canvas.contact_layers[("own", BEE, "signal")].path())
        self.assertAlmostEqual(bounds.origin.y, 51)
        self.assertAlmostEqual(bounds.origin.y + bounds.size.height, 138)

    def test_unpicked_track_stops_eleven_pixels_before_corner_halo(self):
        canvas, frames = self.canvas(picked=["middle", "end"])
        canvas._draw_contacts(frames)
        bounds = Quartz.CGPathGetBoundingBox(canvas.third_layers[(BEE, "start", "track")].path())
        self.assertAlmostEqual(bounds.origin.y, 51)

    def test_halo_clips_another_machines_seam_at_the_same_vertex(self):
        canvas, frames = self.canvas(picked=["start", "middle", "end"])
        canvas.states[BEE]["corner_ways"] = []
        canvas.states[SEA] = {"corner_ways": ["top_right"]}
        frames[SEA] = (350, 40, 160, 100)
        canvas._draw_contacts(frames)
        bounds = Quartz.CGPathGetBoundingBox(canvas.contact_layers[("own", BEE, "signal")].path())
        self.assertAlmostEqual(bounds.origin.y, 51)

    def test_offline_seam_keeps_its_ways_at_fifty_five_percent(self):
        canvas, frames = self.canvas(picked=["start", "middle", "end"])
        canvas.states[BEE]["linked"] = False
        canvas._draw_contacts(frames)
        self.assertAlmostEqual(canvas.contact_layers[("own", BEE, "signal")].opacity(), 0.55)

    def test_edge_off_still_has_third_hits_but_no_tracks(self):
        canvas, frames = self.canvas(edge_off=True)
        self.assertEqual(len(canvas._third_segments(frames)), 3)
        canvas._draw_thirds(frames)
        self.assertFalse(any(key[-1] == "track" for key in canvas.third_layers))

    def test_map_tile_without_received_state_draws_and_has_turn_on_tooltip(self):
        canvas, frames = self.canvas()
        canvas.states = {}
        canvas._draw_thirds(frames)
        self.assertFalse(any(key[-1] == "track" for key in canvas.third_layers))
        self.assertEqual(canvas._third_tooltip(BEE, "start"), "Top third: click to turn it on")

    def test_corner_only_tile_outcome_is_crosses(self):
        canvas, _frames = self.canvas(edge_off=True)
        canvas.states[BEE]["crosses"] = True
        canvas.setFrame_(((0, 0), (480, 260)))
        canvas._draw(False)
        self.assertEqual(canvas.outcome_for_tile[BEE].string(), "Crosses")

    def test_small_map_tiles_keep_outcome_without_overlapping_status(self):
        canvas, _frames = self.canvas(picked=["start", "middle", "end"])
        for height in (64, 77 + 1 / 3):
            canvas.setFrame_(((0, 0), (2 * canvas.INSET + 320 * height / 100, 260)))
            canvas._draw(False)
            frame = canvas._map()[0][BEE]
            title = canvas.label_for_tile[BEE]
            status = canvas.state_for_tile[BEE]
            outcome = canvas.outcome_for_tile[BEE]
            with self.subTest(tile_height=height):
                self.assertAlmostEqual(frame[3], height)
                self.assertFalse(outcome.isHidden())
                self.assertEqual(outcome.string(), "Crosses")
                self.assertEqual(status.isHidden(), height < 72)
                self.assertEqual(canvas.dot_for_tile[BEE].isHidden(), height < 72)
                preceding = title if status.isHidden() else status
                self.assertLessEqual(preceding.frame().origin.y + preceding.frame().size.height,
                                     outcome.frame().origin.y)


if __name__ == "__main__":
    unittest.main()
