import copy
import itertools
import sys
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import AppKit
import Quartz

AppKit.NSApplication.sharedApplication()

import diagram
from core import protocol, settings_sync, ways as core_ways
from test_crossing_page_machines import BEE, OAK, SEA, Page, entry, token
from wake import WakingController


class AtlasCanvas(Page):
    def setUp(self):
        super().setUp()
        capture = mock.patch.object(WakingController, "start_input_capture")
        capture.start()
        self.addCleanup(capture.stop)

    def test_per_screen_desk_canvas_draws_both_machine_display_groups(self):
        from core.tests.crossing_harness import per_screen_layout

        window = self.build([(BEE, token(100), "Studio PC", {"side": "right"})])
        own = self.store.current()["machine_id"]
        tiles = per_screen_layout(own, BEE)
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (560, canvas.HEIGHT)))
        canvas.show_layout(tiles, {own: {"state": "this"}, BEE: {"state": "edge", "crosses": True}},
                           {own: "This Mac", BEE: "Studio PC"}, own, BEE)

        self.assertEqual(len(canvas.display_layers[own]), 2)
        self.assertEqual(len(canvas.display_layers[BEE]), 2)
        self.assertEqual([frame["display"] for frame in core_ways._display_frames(tiles[own])], ["0", "1"])
        self.assertEqual([frame["display"] for frame in core_ways._display_frames(tiles[BEE])], ["0", "1"])

    def layout(self, bee_x=320, bee_y=100):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"}),
                             (SEA, token(150), "Sea", {"side": "left"})])
        window._design_peer_caps = lambda: {BEE: {"settings", "layout"}, SEA: {"settings", "layout"}}
        window.controller._crossing_known_caps = {BEE: frozenset({"settings", "layout"}),
                                                   SEA: frozenset({"settings", "layout"})}
        window.windows_input.server.caps_of.return_value = frozenset({"settings", "layout"})
        own = self.store.current()["machine_id"]
        tiles = {own: {"x": 160, "y": 100, "w": 160, "h": 100},
                 BEE: {"x": bee_x, "y": bee_y, "w": 160, "h": 100},
                 SEA: {"x": 160, "y": 200, "w": 160, "h": 100}}
        window._atlas_layout_changed(tiles, BEE)
        return window

    def test_drag_drop_compiles_and_saves_the_tile_position(self):
        window = self.layout(bee_x=0)
        current = self.store.current()
        self.assertEqual(current["layout"][BEE], {"x": 0, "y": 100, "w": 160, "h": 100})
        self.assertEqual(next(peer for peer in current["peers"] if peer["id"] == BEE)["side"], "left")

    def test_overlap_drop_uses_the_engine_snap_and_keeps_the_saved_position(self):
        window = self.layout()
        own = self.store.current()["machine_id"]
        colliding = dict(self.store.current()["layout"])
        colliding[BEE] = dict(colliding[own])
        window._atlas_layout_changed(colliding, BEE)
        saved = self.store.current()["layout"][BEE]
        self.assertNotEqual(saved, colliding[BEE])
        self.assertFalse(window.atlas_canvas.tiles[BEE] == colliding[BEE])
        self.assertEqual(saved, window.atlas_canvas.tiles[BEE])

    def test_unstamped_defaults_still_show_existing_edges(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})])
        self.assertEqual(window.atlas_canvas.states[BEE]["state"], "edge")

    def test_disabled_edge_and_corner_are_not_drawn_as_crossings(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})],
                            [{"peer": BEE, "kind": "edge", "off": True},
                             {"peer": BEE, "kind": "corner", "corner": "top_right", "edge": "right", "off": True}])
        self.assertFalse(window.atlas_canvas.states[BEE]["crosses"])
        self.assertEqual(window.atlas_canvas.states[BEE]["state"], "gap")
        self.assertNotIn((self.store.current()["machine_id"], BEE, "signal"), window.atlas_canvas.contact_layers)
        self.assertEqual(window.atlas_canvas.outcome_for_tile[BEE].string(), "Edge off")
        self.assertEqual(core_ways.picked_thirds(core_ways.ways(self.store.current(), BEE)["methods"], []), [])

    def test_tiles_show_the_peer_platform_and_the_mac_uses_its_platform_name(self):
        window = self.build([(BEE, token(100), "Bee", {"platform": "linux", "side": "right"})])
        window._live = lambda peer: True
        window._show_arrangement([])
        own = self.store.current()["machine_id"]
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas._draw(False)
        self.assertIn(own, canvas.tiles)
        self.assertEqual(canvas.label_for_tile[own].string(), "This Mac")
        self.assertIn(own, canvas.display_layers)
        self.assertEqual(canvas.states[BEE]["platform"], "Linux")
        self.assertEqual(canvas.state_for_tile[BEE].string(), "Linux · Linked")
        self.assertTrue(canvas.state_for_tile[own].string().endswith(" · macOS"))
        self.assertLess(canvas.dot_for_tile[BEE].frame().origin.x,
                        canvas.state_for_tile[BEE].frame().origin.x)

    def test_own_tile_without_a_saved_name_uses_hostname_and_never_says_offline(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})])
        own = self.store.current()["machine_id"]
        canvas = window.atlas_canvas
        canvas.states[own].pop("machine_name", None)
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas._draw(False)

        self.assertIn("macOS", canvas.state_for_tile[own].string())
        self.assertNotIn("Offline", canvas.state_for_tile[own].string())

    def test_own_name_is_above_its_display_layers(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})])
        canvas = window.atlas_canvas
        own = self.store.current()["machine_id"]
        tiles = dict(canvas.tiles)
        tiles[own] = {**tiles[own], "displays": {"main": {"x": 0, "y": 0, "w": 1728, "h": 1117}}}
        canvas.show_layout(tiles, canvas.states, {**canvas.labels, own: "This Mac"}, own, BEE)
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas._draw(False)

        title = canvas.label_for_tile[own]
        displays = canvas.display_layers[own]
        self.assertEqual(title.string(), "This Mac")
        self.assertTrue(displays)
        self.assertGreater(title.zPosition(), max(layer.zPosition() for layer in displays))

    def test_selected_machine_name_uses_tail_elision_and_keeps_full_accessible_text_at_320(self):
        full_name = "M" * 60
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})],
                            [{"peer": BEE, "kind": "edge"}])
        settings = self.store.current()
        settings["peers"][0]["name"] = full_name
        self.store.save_settings(settings)
        window._show_arrangement([])
        label = window.crossing_ledger.rows[BEE].name.view
        label.setFrameSize_((320, label.frame().size.height))
        attributed = label.attributedStringValue()
        paragraph = attributed.attribute_atIndex_effectiveRange_(
            AppKit.NSParagraphStyleAttributeName, 0, None
        )[0]
        with self.subTest("320 pt machine name uses tail elision"):
            self.assertEqual(label.frame().size.width, 320)
            self.assertEqual(paragraph.lineBreakMode(), AppKit.NSLineBreakByTruncatingTail)
        with self.subTest("full name remains available"):
            self.assertEqual(label.toolTip(), full_name)
            self.assertEqual(label.accessibilityLabel(), full_name)

    def test_fresh_edge_way_is_visible_and_tracks_the_saved_zone(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})],
                            [{"peer": BEE, "kind": "edge"},
                             {"peer": BEE, "kind": "edge", "display": "main", "side": "right",
                              "target_side": "left"}])
        own = self.store.current()["machine_id"]
        settings = self.store.current()
        settings["layout"][own] = {"x": 160, "y": 100, "w": 160, "h": 100,
                                    "displays": {"main": {"x": 0, "y": 0, "w": 160, "h": 100}}}
        settings["layout"][BEE] = {"x": 320, "y": 100, "w": 160, "h": 100,
                                   "displays": {"main": {"x": 0, "y": 0, "w": 160, "h": 100}}}
        self.store.save_settings(settings)
        window.windows_input.server.links.return_value = []
        window._load_ways()
        window._show_arrangement([])
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas._draw(False)

        self.assertIn("edge", core_ways.ways(self.store.current(), BEE)["methods"])
        signal = canvas.contact_layers[(own, BEE, "signal")]
        self.assertIsNotNone(signal)
        import theme
        self.assertTrue(Quartz.CGColorEqualToColor(signal.strokeColor(), theme.cg("signal")))
        self.assertEqual(signal.lineWidth(), 4)
        self.assertEqual(signal.lineCap(), Quartz.kCALineCapRound)
        frames, _scale = canvas._map()
        seam = Quartz.CGPathGetBoundingBox(signal.path())
        own_frame, peer_frame = frames[own], frames[BEE]
        self.assertAlmostEqual(seam.origin.x, own_frame[0] + own_frame[2])
        self.assertAlmostEqual(seam.origin.y, peer_frame[1] + 2)
        self.assertAlmostEqual(seam.size.height, peer_frame[3] - 4)
        self.assertGreater(signal.zPosition(), canvas.layer_for_tile[own].zPosition())
        self.assertGreater(signal.zPosition(), canvas.layer_for_tile[BEE].zPosition())
        self.assertEqual(canvas.outcome_for_tile[BEE].string(), "Offline")
        for layer in (canvas.state_for_tile[BEE], canvas.outcome_for_tile[BEE],
                      canvas.dot_for_tile[BEE], canvas.cycle_for_tile[BEE]):
            self.assertGreater(layer.zPosition(), max(item.zPosition() for item in canvas.display_layers[BEE]))
        self.assertEqual(canvas.state_for_tile[BEE].string(), "Windows · Offline")
        window._ledger_edge(BEE)
        self.assertFalse(core_ways.has_way(self.store.current(), BEE))
        self.assertFalse(core_ways.crosses_on_map(self.store.current(), BEE))
        self.assertFalse(any(zone.get("peer") == BEE and zone.get("kind") in ("edge", "part")
                             for zone in self.store.current()["zones"]))
        self.assertNotIn((own, BEE, "signal"), canvas.contact_layers)
        self.assertTrue(canvas.states[BEE]["edge_off"])
        self.assertEqual(canvas.outcome_for_tile[BEE].string(), "Offline")
        window._atlas_layout_changed(self.store.current()["layout"], BEE)
        self.assertFalse(core_ways.crosses_on_map(self.store.current(), BEE))
        self.assertEqual(canvas.outcome_for_tile[BEE].string(), "Offline")
        window._ledger_edge(BEE)
        self.assertTrue(core_ways.has_way(self.store.current(), BEE))
        self.assertIn((own, BEE, "signal"), canvas.contact_layers)

    def test_gap_tile_edge_edit_waits_for_contact_without_moving_the_tile(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})],
                            [{"peer": BEE, "kind": "edge", "off": True}])
        settings = self.store.current()
        own = settings["machine_id"]
        settings["layout"] = {own: {"x": 160, "y": 100, "w": 160, "h": 100},
                              BEE: {"x": 600, "y": 400, "w": 160, "h": 100}}
        self.store.save_settings(settings)
        window._load_ways()
        window._reflect()
        self.assertEqual(window.atlas_canvas.states[BEE]["state"], "gap")
        window._ledger_edge(BEE)

        current = self.store.current()
        self.assertEqual(current["layout"][BEE], {"x": 600, "y": 400, "w": 160, "h": 100})
        self.assertFalse(core_ways.has_way(current, BEE))
        self.assertNotIn((own, BEE, "signal"), window.atlas_canvas.contact_layers)

    def test_corner_contact_does_not_move_the_tile_when_edge_text_is_clicked(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})],
                            [{"peer": BEE, "kind": "edge", "off": True}])
        settings = self.store.current()
        own = settings["machine_id"]
        settings["layout"] = {own: {"x": 160, "y": 100, "w": 160, "h": 100},
                              BEE: {"x": 320, "y": 200, "w": 160, "h": 100}}
        self.store.save_settings(settings)
        window._load_ways()
        window._reflect()

        window._ledger_edge(BEE)

        self.assertEqual(self.store.current()["layout"][BEE], {"x": 320, "y": 200, "w": 160, "h": 100})

    def test_edge_way_tick_covers_part_zones_and_turns_them_off(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})],
                            [{"peer": BEE, "kind": "part", "parts": ["start"]}])
        window._load_ways()

        self.assertIn("part", core_ways.ways(self.store.current(), BEE)["methods"])
        window._ledger_edge(BEE)
        self.assertFalse(core_ways.has_way(self.store.current(), BEE))

    def test_received_layout_refreshes_the_edge_sentence_from_compiled_zones(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"})],
                            [{"peer": BEE, "kind": "edge", "off": True}])
        settings = self.store.current()
        own = settings["machine_id"]
        settings["layout"][own] = {"x": 160, "y": 100, "w": 160, "h": 100}
        settings["layout"][BEE] = {"x": 500, "y": 100, "w": 160, "h": 100}
        self.store.save_settings(settings)
        window._design_peer_caps = lambda: {BEE: {"settings", "layout"}}
        incoming = dict(settings["layout"])
        incoming[BEE] = {"x": 320, "y": 100, "w": 160, "h": 100}
        data = settings_sync.message_data(False, 0, by=own,
                    layout_state={"set_at": settings["layout_set_at"] + 1, "by": BEE, "tiles": incoming})

        with mock.patch.object(window, "refresh"):
            self.assertTrue(window._receive_layout(data, BEE))

        self.assertNotIn("edge", core_ways.ways(self.store.current(), BEE)["methods"])
        window._show_arrangement()
        self.assertTrue(window.atlas_canvas.states[BEE]["edge_off"])

    def test_tile_outcome_invites_connection_without_old_set_elsewhere_label(self):
        self.assertEqual(diagram.outcome_for_tile("gap", False), "Drag to connect")
        self.assertEqual(diagram.outcome_for_tile("set-elsewhere", False), "Drag to connect")
        self.assertEqual(diagram.outcome_for_tile("between", True), "Drag to connect")

    def test_crossing_page_omits_the_old_picker_and_current_chain(self):
        window = self.layout()
        for name in ("machine_row", "route_note", "ways_heading", "atlas_shelf", "atlas_inspector"):
            self.assertFalse(hasattr(window, name), name)
        self.assertEqual(window.crossing_ledger.accessibilityRole(), AppKit.NSAccessibilityTableRole)

    def test_atlas_places_hint_legend_and_ledger_in_order(self):
        window = self.layout()
        arranged = list(window.atlas_legend.superview().arrangedSubviews())
        self.assertLess(arranged.index(window.atlas_canvas), arranged.index(window.atlas_canvas_hint.view))
        self.assertLess(arranged.index(window.atlas_canvas_hint.view), arranged.index(window.atlas_legend))
        # The ledger has its own card below the map's (1.5.5, Map first): find each card in the page.
        def card(view):
            while view.superview() is not None and view.superview() is not page:
                view = view.superview()
            return view
        page = window.atlas_canvas.superview()
        while page is not None and not any(v is not None and v.isKindOfClass_(AppKit.NSStackView)
                                           and window.crossing_ledger.isDescendantOf_(v) and v is not page
                                           for v in [page.superview()]):
            page = page.superview()
        page = page.superview()
        cards = list(page.arrangedSubviews())
        self.assertLess(cards.index(card(window.atlas_legend)), cards.index(card(window.crossing_ledger)))

    def test_left_out_ledger_row_has_status_and_named_put_back_action(self):
        window = self.build([(BEE, token(100), "Bee", {"left_out": True})])
        row = window.crossing_ledger.rows[BEE]
        self.assertEqual(row.name.view.stringValue(), "Bee")
        self.assertEqual(row.status_text, "Windows · Left out")
        button = row.leave
        self.assertEqual(button.title(), "Put back")
        self.assertEqual(button.accessibilityLabel(), "Put back Bee")
        button.performClick_(None)
        peer = next(entry for entry in self.store.current()["peers"] if entry["id"] == BEE)
        self.assertFalse(peer.get("left_out", False))

    def test_put_back_tooltip_and_shortcut_cycle_note_use_mac_wording(self):
        window = self.layout()
        window._set_atlas_left_out(BEE, True)
        self.assertEqual(window.crossing_ledger.rows[BEE].leave.toolTip(),
                         "Left out machines stay paired and linked. The shortcut, menu and clipboard still reach them.")
        window.shortcut_goes_to._choose("next")
        self.assertIn("This Mac is always in the cycle.", window.shortcut_cycle_note.view.stringValue())
        self.assertNotIn("Home is always in the cycle", window.shortcut_cycle_note.view.stringValue())

    def test_not_in_use_ledger_put_back_uses_the_machine_direction_handler(self):
        for left_out in (False, True):
            with self.subTest(left_out=left_out):
                window = self.build([(BEE, token(100), "Bee", {"in_use": False, "left_out": left_out})])
                window.direction_handler = mock.Mock()
                row = window.crossing_ledger.rows[BEE]
                row.leave.performClick_(None)
                window.direction_handler.assert_called_once_with(token(100), in_use=True)

    def test_left_out_ledger_machine_can_be_excluded_from_the_shortcut_cycle(self):
        window = self.layout()
        window.shortcut_goes_to._choose("ticked")
        window._set_atlas_left_out(BEE, True)
        tick = window.crossing_ledger.rows[BEE].cycle
        self.assertEqual(tick.state(), AppKit.NSControlStateValueOn)
        tick.performClick_(None)
        peer = next(peer for peer in self.store.current()["peers"] if peer["id"] == BEE)
        self.assertFalse(peer["shortcut_cycle"])
        self.assertNotIn(BEE, window.controller._shortcut_cycle())

    def test_machine_list_row_can_exclude_a_left_out_machine_from_the_cycle(self):
        window = self.layout()
        window.shortcut_goes_to._choose("ticked")
        window._set_atlas_left_out(BEE, True)
        window.panel.refresh()
        tick = window.panel._cycle_controls[token(100)]
        tick.view.accessibilityPerformPress()
        peer = next(peer for peer in self.store.current()["peers"] if peer["id"] == BEE)
        self.assertFalse(peer["shortcut_cycle"])

    def test_gap_machine_follows_visible_contacts_in_the_shortcut_cycle(self):
        window = self.layout()
        tiles = dict(window.atlas_canvas.tiles)
        tiles[BEE] = {"x": 480, "y": 100, "w": 160, "h": 100}
        window._atlas_layout_changed(tiles, BEE, snap=False)
        self.assertEqual(window.controller._shortcut_cycle(), [SEA, BEE, None])

    def test_distant_tile_with_a_notch_way_still_follows_placed_tiles(self):
        window = self.layout()
        tiles = dict(window.atlas_canvas.tiles)
        tiles[BEE] = {"x": 480, "y": 100, "w": 160, "h": 100}
        window._atlas_layout_changed(tiles, BEE, snap=False)
        settings = self.store.current()
        settings["zones"].append({"peer": BEE, "kind": "notch"})
        self.store.save_settings(settings)
        window._show_arrangement([])
        self.assertEqual(window.atlas_canvas.states[BEE]["state"], "gap")
        self.assertEqual(window.controller._shortcut_cycle(), [SEA, BEE, None])

    def test_moving_a_tile_keeps_an_inactive_machine_position_and_ways(self):
        window = self.layout()
        settings = self.store.current()
        original = dict(settings["layout"][SEA])
        next(peer for peer in settings["peers"] if peer["id"] == SEA)["in_use"] = False
        original_ways = copy.deepcopy(next(peer for peer in settings["peers"] if peer["id"] == SEA)["ways"])
        self.store.save_settings(settings)
        window._show_arrangement([])
        tiles = dict(window.atlas_canvas.tiles)
        tiles[BEE] = {"x": 0, "y": 100, "w": 160, "h": 100}
        window._atlas_layout_changed(tiles, BEE, snap=False)
        current = self.store.current()
        self.assertEqual(current["layout"].get(SEA), original)
        self.assertEqual(next(peer for peer in current["peers"] if peer["id"] == SEA)["ways"], original_ways)
        self.assertFalse(any(zone["peer"] == SEA for zone in current["zones"]))

    def test_incoming_layout_accepts_inactive_position_and_keeps_its_ways(self):
        window = self.layout()
        settings = self.store.current()
        own = settings["machine_id"]
        next(peer for peer in settings["peers"] if peer["id"] == SEA)["in_use"] = False
        foreign = protocol.id_text(bytes(range(110, 126)))
        next(peer for peer in settings["peers"] if peer["id"] == BEE)["paired_with"] = [foreign]
        original = copy.deepcopy(settings)
        self.store.save_settings(settings)
        window._design_peer_caps = lambda: {BEE: {"settings", "layout"}}
        incoming = copy.deepcopy(settings["layout"])
        incoming[BEE] = {"x": 0, "y": 100, "w": 160, "h": 100}
        incoming[SEA] = {"x": 320, "y": 100, "w": 160, "h": 100}
        incoming[foreign] = {"x": 500, "y": 100, "w": 160, "h": 100, "name": "Test box"}
        state = {"tiles": incoming, "set_at": settings["layout_set_at"] + 1, "by": BEE}
        data = settings_sync.message_data(False, 0, by=own, layout_state=state)
        with mock.patch.object(window, "refresh"):
            self.assertTrue(window._receive_layout(data, BEE))
        current = self.store.current()
        self.assertEqual(current["layout"][SEA], incoming[SEA])
        self.assertEqual(next(peer for peer in current["peers"] if peer["id"] == SEA)["ways"],
                         next(peer for peer in original["peers"] if peer["id"] == SEA)["ways"])
        self.assertFalse(any(zone["peer"] == SEA for zone in current["zones"]))
        self.assertNotIn("vouched_by", current["layout"][foreign])
        self.assertEqual(window.controller.cfg, self.store.load())

    def test_incoming_layout_keeps_a_named_third_party_from_the_current_paired_list(self):
        window = self.layout()
        settings = self.store.current()
        own = settings["machine_id"]
        fedora = protocol.id_text(bytes(range(110, 126)))
        next(peer for peer in settings["peers"] if peer["id"] == BEE)["paired_with"] = [fedora]
        settings["layout"][fedora] = {"x": 500, "y": 100, "w": 160, "h": 100, "name": "Test box"}
        self.store.save_settings(settings)
        incoming = {own: dict(settings["layout"][own]), BEE: dict(settings["layout"][BEE])}
        data = settings_sync.message_data(False, 0, by=own,
                    layout_state={"set_at": settings["layout_set_at"] + 1, "by": BEE, "tiles": incoming})
        window._design_peer_caps = lambda: {BEE: {"settings", "layout"}}

        with mock.patch.object(window, "refresh"):
            self.assertTrue(window._receive_layout(data, BEE))

        self.assertEqual(self.store.current()["layout"][fedora], settings["layout"][fedora])

    def test_crossing_map_drops_the_rc7_ghost_tile_reproduction(self):
        window = self.build([(BEE, token(100), "Desk PC", {"left_out": True,
                                                              "paired_with": ["PHONE", "BOX"]}),
                             (SEA, token(150), "Laptop", {"paired_with": ["OLDPC"]})])
        settings = self.store.current()
        own = settings["machine_id"]
        phone, box, old_pc = (protocol.id_text(bytes([value]) * 16) for value in (11, 12, 13))
        next(peer for peer in settings["peers"] if peer["id"] == BEE)["paired_with"] = [phone, box]
        next(peer for peer in settings["peers"] if peer["id"] == SEA)["paired_with"] = [old_pc]
        settings["peers"].append({"id": phone, "name": "Phone", "port": 0, "in_use": True})
        settings["layout"].update({
            phone: {"x": 480, "y": 100, "w": 160, "h": 100, "vouched_by": BEE},
            box: {"x": 640, "y": 100, "w": 160, "h": 100, "vouched_by": BEE},
            old_pc: {"x": 800, "y": 100, "w": 160, "h": 100, "vouched_by": SEA},
        })
        self.store.save_settings(settings)
        incoming = copy.deepcopy(settings["layout"])
        data = settings_sync.message_data(False, 0, by=own,
                    layout_state={"set_at": self.store.current()["layout_set_at"] + 1, "by": SEA, "tiles": incoming})
        window._design_peer_caps = lambda: {SEA: {"settings", "layout"}}

        with mock.patch.object(window, "refresh"):
            self.assertTrue(window._receive_layout(data, SEA))
        window._show_arrangement([])

        self.assertTrue({phone, box, old_pc}.isdisjoint(window.atlas_canvas.tiles))

    def test_shortcut_destination_labels_fit_without_wrapping(self):
        window = self.layout()
        window._select_page("crossing")
        control = window.shortcut_goes_to
        window.window.contentView().layoutSubtreeIfNeeded()
        for _value, cell, words in control.cells:
            self.assertGreaterEqual(cell.frame().size.width, words.view.intrinsicContentSize().width + 8)

    def test_canvas_focus_ring_follows_keyboard_navigation_and_leaves_with_focus(self):
        window = self.layout()
        window._select_page("crossing")
        window.window.keyboard_navigation = True
        window.window.makeFirstResponder_(window.atlas_canvas)
        window.window.refresh_input_focus()
        self.assertFalse(window.atlas_canvas.focus_layer.isHidden())
        window.window.makeFirstResponder_(window.crossing_ledger.rows[BEE].name_field)
        self.assertTrue(window.atlas_canvas.focus_layer.isHidden())

    def test_in_use_and_not_in_use_are_carried_into_the_tile_state(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right", "in_use": True}),
                             (SEA, token(150), "Sea", {"side": "left", "in_use": False})])
        self.assertTrue(window.atlas_canvas.states[BEE]["in_use"])
        self.assertFalse(window.atlas_canvas.states[SEA]["in_use"])

    def test_stamped_layout_adds_a_tile_for_a_new_peer(self):
        window = self.layout()
        settings = self.store.current()
        settings["peers"].append(entry(OAK, token(200), "Oak", own=settings["machine_id"]))
        self.store.save_settings(settings)
        window._show_arrangement([])
        self.assertIn(OAK, window.atlas_canvas.tiles)

    def test_arrow_key_moves_one_third_and_announces_the_move(self):
        window = self.layout()
        canvas = window.atlas_canvas
        before = canvas.tiles[BEE]["x"]
        event = mock.Mock()
        event.keyCode.return_value = 124
        event.modifierFlags.return_value = 0
        canvas.keyDown_(event)
        self.assertEqual(canvas.tiles[BEE]["x"], before + 53)
        self.assertEqual(canvas.accessibilityLabel(), "Bee, right of This Mac. Drag to connect.")

    def test_rename_action_uses_the_existing_peer_rename_path(self):
        window = self.layout()
        window._begin_atlas_rename(BEE)
        field = window.crossing_ledger.rows[BEE].name_field
        self.assertFalse(field.isHidden())
        window.window.contentView().layoutSubtreeIfNeeded()
        self.assertFalse(field.isHidden())
        window._apply_appearance("dark")
        view = window.window.contentView()
        rep = view.bitmapImageRepForCachingDisplayInRect_(view.bounds())
        view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
        self.assertFalse(field.isHidden())
        field.setStringValue_("Studio PC")
        window.control_textView_doCommandBySelector_(field, None, "insertNewline:")
        peer = next(peer for peer in self.store.current()["peers"] if peer["id"] == BEE)
        self.assertEqual(peer["label"], "Studio PC")

    def test_rename_stays_open_when_the_resized_page_has_not_laid_out(self):
        window = self.two()
        window._select_page("crossing")
        window.window.setContentSize_((1040, 900))
        window._begin_atlas_rename(BEE)
        row = window.crossing_ledger.rows[BEE]
        self.assertTrue(row.renaming)
        self.assertFalse(row.name_field.isHidden())
        cell = row.cells["machine"]
        self.assertLessEqual(row.name_field.frame().origin.x + row.name_field.frame().size.width,
                             cell.bounds().size.width)

    def test_leave_out_and_put_back_restore_the_machine_ways(self):
        window = self.layout()
        window._set_atlas_left_out(BEE, True)
        peer = next(peer for peer in self.store.current()["peers"] if peer["id"] == BEE)
        self.assertTrue(peer["left_out"])
        self.assertNotIn(BEE, window.atlas_canvas.tiles)
        self.assertEqual(window.chosen_peer, BEE)
        window._set_atlas_left_out(BEE, False)
        peer = next(peer for peer in self.store.current()["peers"] if peer["id"] == BEE)
        self.assertNotIn("left_out", peer)
        self.assertIn(BEE, window.atlas_canvas.tiles)

    def test_shortcut_mode_and_cycle_ticks_are_saved(self):
        window = self.layout()
        window.shortcut_goes_to._choose("ticked")
        window.atlas_canvas.toggle_cycle(BEE)
        window._apply_settings()
        self.assertEqual(self.store.current()["crossing"]["shortcut_goes_to"], "ticked")
        peer = next(peer for peer in self.store.current()["peers"] if peer["id"] == BEE)
        self.assertFalse(peer["shortcut_cycle"])

    def test_dragging_this_mac_never_changes_its_tile_or_compiles_a_layout(self):
        window = self.layout()
        canvas = window.atlas_canvas
        own = self.store.current()["machine_id"]
        before = dict(canvas.tiles[own])
        callback = mock.Mock()
        canvas.on_layout_change = callback
        canvas.window = mock.Mock()
        canvas.convertPoint_fromView_ = mock.Mock(return_value=AppKit.NSMakePoint(10, 10))
        canvas.tile_at = mock.Mock(return_value=own)
        event = mock.Mock()
        event.locationInWindow.return_value = AppKit.NSMakePoint(10, 10)
        event.clickCount.return_value = 1
        canvas.mouseDown_(event)
        canvas.mouseDragged_(event)
        canvas.mouseUp_(event)
        self.assertEqual(canvas.tiles[own], before)
        callback.assert_not_called()

    def test_snap_tolerance_stays_twelve_visible_pixels_at_half_scale(self):
        self.assertEqual(diagram.AtlasCanvas.snap_radius_for_scale(0.5), 24)

    def test_arrangement_box_centres_and_refits_after_a_peer_moves(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        def box():
            frames, _scale = canvas._map()
            return (min(frame[0] for frame in frames.values()), min(frame[1] for frame in frames.values()),
                    max(frame[0] + frame[2] for frame in frames.values()),
                    max(frame[1] + frame[3] for frame in frames.values()))

        before = box()
        self.assertAlmostEqual((before[0]+before[2])/2, 240)
        self.assertAlmostEqual((before[1]+before[3])/2, canvas.HEIGHT/2)
        before_scale = canvas._map()[1]
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "x": 520, "y": 180}
        after = box()
        scale = canvas._map()[1]
        self.assertNotEqual(after, before)
        self.assertAlmostEqual((after[0]+after[2])/2, 240)
        self.assertAlmostEqual((after[1]+after[3])/2, canvas.HEIGHT/2)
        self.assertNotEqual(scale, before_scale)

    def test_fit_centres_a_column_when_this_mac_is_at_one_end(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        own = canvas.own_id
        canvas.tiles[own] = {**canvas.tiles[own], "x": 0, "y": 0}
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "x": 0, "y": 100}
        canvas.tiles[SEA] = {**canvas.tiles[SEA], "x": 0, "y": 200}
        frames, scale = canvas._map()
        left = min(frame[0] for frame in frames.values())
        top = min(frame[1] for frame in frames.values())
        right = max(frame[0] + frame[2] for frame in frames.values())
        bottom = max(frame[1] + frame[3] for frame in frames.values())
        expected = min((480-2*canvas.INSET)/160, (canvas.HEIGHT-2*canvas.INSET)/300, 1.15)
        self.assertAlmostEqual(scale, expected)
        self.assertAlmostEqual((left+right)/2, 240)
        self.assertAlmostEqual((top+bottom)/2, canvas.HEIGHT/2)

    def test_map_fit_includes_split_display_offsets(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        base_scale = canvas._map()[1]
        canvas.tiles[BEE] = {**canvas.tiles[BEE],
                             "displays": {"main": {"x": 0, "y": 0, "w": 160, "h": 100}},
                             "display_offsets": {"main": {"x": 400, "y": 0}}}

        self.assertLess(canvas._map()[1], base_scale)

    def test_small_tiles_hide_detail_and_keep_title_clear_of_display_numbers(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "y": 1000}
        canvas._draw(False)
        frame = canvas._map()[0][BEE]

        self.assertLess(frame[3], 32)
        self.assertTrue(canvas.state_for_tile[BEE].isHidden())
        self.assertTrue(canvas.dot_for_tile[BEE].isHidden())
        self.assertTrue(canvas.outcome_for_tile[BEE].isHidden())
        title = canvas.label_for_tile[BEE].frame()
        self.assertAlmostEqual(title.origin.y, frame[1] + 11)
        self.assertGreater(title.size.width, 0)
        self.assertTrue(AppKit.NSContainsRect(diagram._rect(frame), title))
        badges = canvas.display_number_layers[BEE]
        self.assertTrue(badges)
        for badge in badges:
            self.assertFalse(AppKit.NSIntersectsRect(title, badge.frame()))

    def test_edge_seam_lights_the_whole_contact_and_part_only_the_picked_thirds(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        own = canvas.own_id
        canvas.tiles[own] = {**canvas.tiles[own], "x": 0, "y": 0, "w": 160, "h": 90}
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "x": 160, "y": 20, "w": 160, "h": 90}
        frames, scale = canvas._map()

        def lit_height(lit):
            canvas.states[BEE] = {**canvas.states.get(BEE, {}), "state": "edge", "crosses": True,
                                  "thirds": ["middle", "end"], "lit_thirds": lit}
            canvas._draw(False)
            box = Quartz.CGPathGetBoundingBox(canvas.contact_layers[(own, BEE, "signal")].path())
            return box.size.height / scale

        self.assertAlmostEqual(lit_height([]), 70 - 4 / scale, delta=1)
        # The selected machine's seam starts half the break gap plus its round cap after the third boundary.
        self.assertAlmostEqual(lit_height(["end"]), 30 - (2 + diagram.THIRD_GAP / 2 + 2) / scale, delta=1)

    def test_display_drag_keeps_its_map_transform_frozen(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas.tiles[BEE] = {**canvas.tiles[BEE],
                             "displays": {"main": {"x": 0, "y": 0, "w": 160, "h": 100}}}
        frames, scale = canvas._map()
        canvas.display_at = mock.Mock(return_value=(BEE, "main"))
        canvas.window = mock.Mock()
        canvas.convertPoint_fromView_ = mock.Mock(return_value=AppKit.NSMakePoint(10, 10))
        event = mock.Mock()
        event.locationInWindow.return_value = AppKit.NSMakePoint(10, 10)
        event.clickCount.return_value = 1
        event.modifierFlags.return_value = AppKit.NSEventModifierFlagOption

        with mock.patch.object(diagram.AppKit, "NSCursor"):
            canvas.mouseDown_(event)

        self.assertEqual(canvas.dragging["map_x"], frames[canvas.own_id][0]
                         - canvas.tiles[canvas.own_id]["x"] * scale)
        self.assertEqual(canvas.dragging["map_y"], frames[canvas.own_id][1]
                         - canvas.tiles[canvas.own_id]["y"] * scale)
        self.assertEqual(canvas.dragging["scale"], scale)
        own_frame = canvas._map()[0][canvas.own_id]
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "x": 1000}
        self.assertEqual(canvas._map()[0][canvas.own_id], own_frame)

    def test_selection_relayout_does_not_change_either_drag_transform(self):
        for display in (None, "main"):
            with self.subTest(display=display):
                window = self.layout()
                canvas = window.atlas_canvas
                canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
                canvas.tiles[BEE] = {**canvas.tiles[BEE],
                                     "displays": {"main": {"x": 0, "y": 0, "w": 160, "h": 100}}}
                frames, scale = canvas._map()
                own_frame = frames[canvas.own_id]
                left = min(frame[0] for frame in frames.values())
                right = max(frame[0] + frame[2] for frame in frames.values())
                self.assertAlmostEqual((left+right)/2, 240)
                canvas.on_select = lambda _key: canvas.setFrame_(((0, 0), (560, canvas.HEIGHT)))
                canvas.display_at = mock.Mock(return_value=(BEE, display))
                canvas.window = mock.Mock()
                canvas.convertPoint_fromView_ = mock.Mock(return_value=AppKit.NSMakePoint(10, 10))
                event = mock.Mock()
                event.locationInWindow.return_value = AppKit.NSMakePoint(10, 10)
                event.clickCount.return_value = 1
                event.modifierFlags.return_value = AppKit.NSEventModifierFlagOption

                with mock.patch.object(diagram.AppKit, "NSCursor"):
                    canvas.mouseDown_(event)

                self.assertEqual(canvas.dragging["scale"], scale)
                self.assertEqual(canvas._map()[0][canvas.own_id], own_frame)
                canvas.tiles[BEE] = {**canvas.tiles[BEE], "x": 1000, "y": -500}
                self.assertEqual(canvas._map()[0][canvas.own_id], own_frame)

    def test_selected_and_own_tile_borders_use_neutral_roles(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas.selected = BEE
        canvas.hovered = BEE
        with mock.patch.object(diagram.theme, "tint", wraps=diagram.theme.tint) as tint:
            canvas._draw(False)
        for key, role, width in ((BEE, "ink_2", 2), (canvas.own_id, "edge", 1)):
            tile = canvas.layer_for_tile[key]
            call = next(call for call in tint.call_args_list if call.args[0] is tile)
            self.assertEqual(call.kwargs["border"], role)
            self.assertNotEqual(call.kwargs["border"], "signal")
            self.assertEqual(tile.borderWidth(), width)
        self.assertTrue(Quartz.CGColorEqualToColor(canvas.layer_for_tile[canvas.own_id].backgroundColor(),
                                                  diagram.theme.cg("well")))

    def test_corner_crossing_uses_square_pad_without_contact_dot(self):
        window = self.layout(bee_x=320, bee_y=0)
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        canvas.states[BEE] = {"state": "corner", "crosses": True, "corner_ways": ["top_right"]}
        canvas._draw(False)
        self.assertNotIn((canvas.own_id, BEE, "signal"), canvas.contact_layers)
        pad = canvas.mark_layers[(BEE, "corner", "top_right")]
        bounds = Quartz.CGPathGetBoundingBox(pad.path())
        self.assertEqual((bounds.size.width, bounds.size.height), (16, 16))

    def test_recentre_places_selected_peer_and_commits_once(self):
        window = self.layout()
        canvas = window.atlas_canvas
        own = canvas.tiles[canvas.own_id]
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "x": own["x"] + own["w"] + 24, "y": own["y"] + 18}
        canvas.selected = BEE
        callback = mock.Mock()
        canvas.on_layout_change = callback

        self.assertTrue(canvas.recentre_selected())

        peer = canvas.tiles[BEE]
        self.assertEqual(peer["x"], own["x"] + own["w"])
        self.assertEqual(peer["y"], own["y"] + (own["h"] - peer["h"]) / 2)
        callback.assert_called_once_with(canvas.tiles, BEE, snap=False)

    def test_snap_preview_appears_only_inside_snap_radius(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        frames, scale = canvas._map()
        own = canvas.tiles[canvas.own_id]
        canvas.dragging = {"key": BEE, "scale": scale,
                           "map_x": frames[canvas.own_id][0] - own["x"] * scale,
                           "map_y": frames[canvas.own_id][1] - own["y"] * scale}
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "y": own["y"] + 5}
        self.assertEqual(canvas.snap_preview()["y"], canvas.tiles[canvas.own_id]["y"])
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "x": own["x"] + own["w"]
                             + round(3 * canvas.snap_radius_for_scale(scale))}
        self.assertIsNone(canvas.snap_preview())

    def test_release_commits_a_centre_snap_between_base_and_extended_radius(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (480, canvas.HEIGHT)))
        frames, scale = canvas._map()
        own = canvas.tiles[canvas.own_id]
        radius = canvas.snap_radius_for_scale(scale)
        distance = round(radius * 1.25)
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "x": own["x"] + own["w"] + distance, "y": own["y"]}
        self.assertGreater(distance, radius)
        self.assertLessEqual(distance, radius * 1.5)
        canvas.dragging = {"key": BEE, "moved": True, "scale": scale,
                           "map_x": frames[canvas.own_id][0] - own["x"] * scale,
                           "map_y": frames[canvas.own_id][1] - own["y"] * scale}
        self.assertEqual(canvas.snap_preview(), {**canvas.tiles[BEE], "x": own["x"] + own["w"]})
        callback = mock.Mock()
        canvas.on_layout_change = callback
        canvas.window = mock.Mock()
        canvas.convertPoint_fromView_ = mock.Mock(return_value=AppKit.NSMakePoint(10, 10))
        event = mock.Mock()
        event.locationInWindow.return_value = AppKit.NSMakePoint(10, 10)

        with mock.patch.object(diagram.AppKit, "NSCursor"):
            canvas.mouseUp_(event)

        callback.assert_called_once_with(canvas.tiles, BEE, snap=False)
        self.assertEqual(canvas.tiles[BEE]["x"], own["x"] + own["w"])

    def test_c_key_recentres_selected_peer(self):
        window = self.layout()
        canvas = window.atlas_canvas
        own = canvas.tiles[canvas.own_id]
        canvas.tiles[BEE] = {**canvas.tiles[BEE], "x": own["x"] + own["w"] + 24, "y": own["y"] + 18}
        canvas.selected = BEE
        callback = mock.Mock()
        canvas.on_layout_change = callback
        event = mock.Mock()
        event.keyCode.return_value = 8

        canvas.keyDown_(event)

        self.assertEqual(canvas.tiles[BEE]["x"], own["x"] + own["w"])
        callback.assert_called_once_with(canvas.tiles, BEE, snap=False)

    def test_non_touching_tile_invites_connection(self):
        self.assertEqual(diagram.outcome_for_tile("between", False), "Drag to connect")

    def test_a_drop_ten_visible_pixels_from_a_snap_point_snaps_on_a_small_map(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.setFrame_(((0, 0), (240, canvas.HEIGHT)))
        tiles = dict(canvas.tiles)
        tiles[BEE] = {"x": 0, "y": 137, "w": 160, "h": 100}
        window._atlas_layout_changed(tiles, BEE)
        self.assertEqual(self.store.current()["layout"][BEE]["y"], 150)

    def test_in_use_off_peer_keeps_its_ledger_row_without_map_tile(self):
        window = self.build([(BEE, token(100), "Bee", {"in_use": False})])
        self.assertNotIn(BEE, window.atlas_canvas.tiles)
        self.assertIn(BEE, window.crossing_ledger.rows)
        self.assertEqual(window.crossing_ledger.rows[BEE].leave.title(), "Put back")

    def test_mouse_drop_announces_the_compiled_position(self):
        window = self.layout(bee_x=320)
        canvas = window.atlas_canvas
        announced = []
        canvas.announce = announced.append
        tiles = dict(canvas.tiles)
        tiles[BEE] = {"x": 0, "y": 100, "w": 160, "h": 100}
        canvas.tiles = tiles
        canvas.dragging = {"key": BEE, "moved": True}
        canvas.window = mock.Mock()
        canvas.convertPoint_fromView_ = mock.Mock(return_value=AppKit.NSMakePoint(10, 10))
        event = mock.Mock()
        event.locationInWindow.return_value = AppKit.NSMakePoint(10, 10)
        with mock.patch.object(diagram.AppKit, "NSCursor"):
            canvas.mouseUp_(event)
        self.assertTrue(announced)
        self.assertIn("Bee", announced[-1])

    def test_accessible_tile_and_tick_expose_names_values_and_press_actions(self):
        window = self.layout()
        window.shortcut_goes_to._choose("ticked")
        window._apply_settings()
        canvas = window.atlas_canvas
        children = canvas.accessibilityChildren()
        bee = next(child for child in children if child.key == BEE and child.kind == "tile")
        tick = next(child for child in children if child.key == BEE and child.kind == "cycle")
        self.assertEqual(bee.accessibilityLabel(), "Bee")
        self.assertEqual(tick.accessibilityValue(), True)
        sea = next(child for child in children if child.key == SEA and child.kind == "tile")
        sea.accessibilityPerformPress()
        self.assertEqual(window.chosen_peer, SEA)
        tick.accessibilityPerformPress()
        self.assertFalse(tick.accessibilityValue())

    def test_cycle_checkbox_is_keyboard_operable(self):
        window = self.layout()
        canvas = window.atlas_canvas
        canvas.cycle_enabled = True
        canvas.selected = BEE
        event = mock.Mock()
        event.keyCode.return_value = 49
        event.modifierFlags.return_value = 0
        canvas.keyDown_(event)
        self.assertFalse(canvas.cycle[BEE])

    def test_shortcut_cycle_uses_the_canvas_reading_order(self):
        window = self.layout(bee_x=0)
        self.assertEqual(window.controller._shortcut_cycle(), [SEA, BEE, None])

    def test_left_out_peer_position_does_not_put_it_ahead_of_visible_peers(self):
        window = self.two()
        own = self.store.current()["machine_id"]
        settings = self.store.current()
        settings["layout"] = {
            own: {"x": 160, "y": 100, "w": 160, "h": 100},
            BEE: {"x": 0, "y": 100, "w": 160, "h": 100},
            SEA: {"x": 320, "y": 100, "w": 160, "h": 100},
        }
        next(peer for peer in settings["peers"] if peer["id"] == BEE)["left_out"] = True
        settings["layout_set_at"] = 1
        self.store.save_settings(settings)
        self.assertEqual(window.controller._shortcut_cycle(), [SEA, BEE, None])


class WrapAroundSeams(unittest.TestCase):
    """The map's wrap-around edges and corner pads, drawn from the per-machine lists the page builds."""

    def canvas(self, **state):
        canvas = diagram.AtlasCanvas.alloc().init().setup()
        canvas.own_id, canvas.selected = "own", BEE
        canvas.tiles = {"own": {"x": 100, "y": 100, "w": 160, "h": 100},
                        BEE: {"x": 260, "y": 100, "w": 160, "h": 100}}
        canvas.labels = {"own": "This Mac", BEE: "Bee"}
        canvas.states = {BEE: {"state": "edge", "crosses": True, "linked": True, "picked_thirds": list(core_ways.PARTS),
                              "lit_thirds": [], "wrap_sides": [], "kept_sides": [], "corner_ways": [], **state}}
        return canvas, {"own": (30, 40, 160, 100), BEE: (190, 40, 160, 100)}

    def bounds(self, layer):
        box = Quartz.CGPathGetBoundingBox(layer.path())
        return (box.origin.x, box.origin.y, box.size.width, box.size.height)

    def test_each_wrap_around_side_has_a_four_point_seam_on_this_macs_own_side(self):
        canvas, frames = self.canvas(wrap_sides=["left", "top"])
        canvas._draw_wraps(frames)
        self.assertEqual(set(canvas.wrap_layers), {(BEE, "left"), (BEE, "top")})
        seam, _arrow, _text = canvas.wrap_layers[(BEE, "left")]
        self.assertEqual(seam.lineWidth(), 4)
        self.assertEqual(self.bounds(seam), (30, 42, 0, 96))
        seam, _arrow, _text = canvas.wrap_layers[(BEE, "top")]
        self.assertEqual(self.bounds(seam), (32, 40, 156, 0))

    def test_a_seam_stops_thirteen_points_short_of_a_corner_pads_vertex(self):
        canvas, frames = self.canvas(wrap_sides=["left", "top"], corner_ways=["top_left", "bottom_right"])
        canvas._draw_wraps(frames)
        self.assertEqual(self.bounds(canvas.wrap_layers[(BEE, "left")][0]), (30, 53, 0, 85))
        self.assertEqual(self.bounds(canvas.wrap_layers[(BEE, "top")][0]), (43, 40, 145, 0))

    def test_another_machines_pad_also_stops_the_seam(self):
        canvas, frames = self.canvas(wrap_sides=["bottom"])
        canvas.states[SEA] = {"corner_ways": ["bottom_left"], "in_use": True}
        frames[SEA] = (-130, 40, 160, 100)
        canvas._draw_wraps(frames)
        self.assertEqual(self.bounds(canvas.wrap_layers[(BEE, "bottom")][0]), (43, 140, 145, 0))

    def test_label_and_arrow_sit_inside_the_tile_by_side(self):
        for side in ("left", "right", "top", "bottom"):
            canvas, frames = self.canvas(wrap_sides=[side])
            canvas._draw_wraps(frames)
            _seam, arrow, text = canvas.wrap_layers[(BEE, side)]
            self.assertEqual(text.string(), "wraps to Bee")
            self.assertEqual(arrow.isHidden(), side in ("top", "bottom"))
            frame = text.frame()
            with self.subTest(side=side):
                if side == "left":
                    self.assertEqual((frame.origin.x, frame.origin.y), (56, 40 + 50 + 10))
                    box = self.bounds(arrow)
                    self.assertEqual((box[0], box[0] + box[2]), (46, 55))
                if side == "right":
                    self.assertAlmostEqual(frame.origin.x + frame.size.width, 30 + 160 - 26)
                    box = self.bounds(arrow)
                    self.assertEqual((box[0], box[0] + box[2]), (165, 174))
                if side == "top":
                    self.assertAlmostEqual(frame.origin.x + frame.size.width, 30 + 160 - 12)
                    self.assertEqual(frame.origin.y, 40 + 51 + 4)
                if side == "bottom":
                    self.assertAlmostEqual(frame.origin.x + frame.size.width, 30 + 160 - 12)
                    self.assertEqual(frame.origin.y, 40 + 100 - 22)

    def test_no_wrap_label_ever_lands_on_the_name_the_status_line_or_another_label(self):
        """rc4: with Top on, "wraps to <the PC>" was drawn over the "This Mac" title, and with Top,
        Right and Bottom three of them stacked. Every set of sides for one machine, at every height,
        and for two machines at once."""
        def rect(layer):
            frame = layer.frame()
            return frame.origin.x, frame.origin.y, frame.origin.x + frame.size.width, frame.origin.y + frame.size.height

        def meet(one, two):
            return one[0] < two[2] and two[0] < one[2] and one[1] < two[3] and two[1] < one[3]

        sets = [list(sides) for count in range(0, 5) for sides in itertools.combinations(core_ways.EDGES, count)]
        cases = [(height, sides, []) for height in (48, 52, 60, 64, 72, 88, 104, 140, 190) for sides in sets[1:]]
        cases += [(104, sides, others) for sides in sets for others in sets[1:]]
        for height, sides, others in cases:
            canvas, _frames = self.canvas(wrap_sides=sides)
            canvas.tiles[SEA] = {"x": 100, "y": 100 - height, "w": 160, "h": height}
            canvas.labels[SEA] = "Sea"
            canvas.states[SEA] = dict(canvas.states[BEE], wrap_sides=others)
            canvas.tiles["own"]["h"] = canvas.tiles[BEE]["h"] = height
            canvas.setFrame_(((0, 0), (1000, 1000)))
            canvas._draw(False)
            x, y, width, tall = canvas._map()[0]["own"]
            title, state = canvas.label_for_tile["own"], canvas.state_for_tile["own"]
            title_box = rect(title)
            name = diagram._text_width(title.string(), title.font())
            middle = (title_box[0] + title_box[2]) / 2
            fixed = [(middle - name / 2, title_box[1], middle + name / 2, title_box[3])]
            if not state.isHidden():
                fixed.append(rect(state))
            labels = [rect(text) for _seam, _arrow, text in canvas.wrap_layers.values() if not text.isHidden()]
            with self.subTest(height=height, sides=sides, others=others):
                for index, label in enumerate(labels):
                    self.assertTrue(x <= label[0] and label[2] <= x + width and y <= label[1] and label[3] <= y + tall)
                    for other in fixed + labels[index + 1:]:
                        self.assertFalse(meet(label, other), (label, other))
                if tall >= 100:
                    self.assertEqual(len(labels), bool(sides) + bool(others))
                elif 56 <= tall < 72 or tall >= 77:
                    self.assertTrue(labels, "a tile with room for one row shows a label")

    def test_opacity_follows_the_seam_rules_offline_and_not_selected(self):
        canvas, frames = self.canvas(wrap_sides=["left"], linked=False)
        canvas._draw_wraps(frames)
        seam, arrow, text = canvas.wrap_layers[(BEE, "left")]
        self.assertAlmostEqual(seam.opacity(), 0.55)
        self.assertAlmostEqual(text.opacity(), 0.55)
        canvas.states[BEE]["linked"] = True
        canvas.selected = "own"
        canvas._draw_wraps(frames)
        self.assertAlmostEqual(seam.opacity(), 0.7)
        canvas.selected = BEE
        canvas._draw_wraps(frames)
        self.assertAlmostEqual(seam.opacity(), 1.0)

    def test_a_kept_side_is_a_two_point_dashed_seam_that_says_edge_is_off(self):
        canvas, frames = self.canvas(kept_sides=["left"])
        canvas._draw_wraps(frames)
        seam, _arrow, text = canvas.wrap_layers[(BEE, "left")]
        self.assertEqual(seam.lineWidth(), 2)
        self.assertTrue(seam.lineDashPattern())
        self.assertAlmostEqual(seam.opacity(), 0.35)
        self.assertEqual(text.string(), "wrap kept, Edge off")

    def test_a_side_that_stops_wrapping_loses_its_layers(self):
        canvas, frames = self.canvas(wrap_sides=["left"])
        canvas._draw_wraps(frames)
        layers = canvas.wrap_layers[(BEE, "left")]
        canvas.states[BEE]["wrap_sides"] = []
        canvas._draw_wraps(frames)
        self.assertEqual(canvas.wrap_layers, {})
        self.assertTrue(all(layer.superlayer() is None for layer in layers))

    def test_a_pad_is_drawn_for_every_corner_in_a_machines_list(self):
        canvas, frames = self.canvas(corner_ways=["top_left", "top_right", "bottom_right"])
        canvas._draw_marks(frames)
        self.assertEqual(sorted(key[2] for key in canvas.mark_layers),
                         ["bottom_right", "top_left", "top_right"])
        self.assertEqual(sorted(key[2] for key in canvas.corner_halo_layers),
                         ["bottom_right", "top_left", "top_right"])

    def test_outcome_is_crosses_for_a_wrap_around_edge_even_with_the_contact_unpicked(self):
        canvas, _frames = self.canvas(wrap_sides=["left"], unpicked=True, picked_thirds=[])
        canvas.setFrame_(((0, 0), (480, 260)))
        canvas._draw(False)
        self.assertEqual(canvas.outcome_for_tile[BEE].string(), "Crosses")
        canvas.states[BEE].update(wrap_sides=[], crosses=False, edge_off=True)
        canvas._draw(False)
        self.assertEqual(canvas.outcome_for_tile[BEE].string(), "Edge off")


if __name__ == "__main__":
    unittest.main()
