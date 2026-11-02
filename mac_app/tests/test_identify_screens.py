"""Synthetic map numbering, both Mac links, and non-activating display panels."""

import logging
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import AppKit

AppKit.NSApplication.sharedApplication()

import bridge
import diagram
import identify_overlay
from core import identify_screens, protocol, ways
from core.return_edge import Rect
from test_crossing_page_machines import Page

OWN = protocol.id_text(bytes(range(1, 17)))
PEER = protocol.id_text(bytes(range(21, 37)))
OLDER = protocol.id_text(bytes(range(41, 57)))
PHONE = protocol.id_text(bytes(range(61, 77)))
DISPLAYS = {"0": dict(x=1920, y=0, w=1512, h=982),
            "1": dict(x=0, y=0, w=1920, h=1080)}


class OverlayTests(unittest.TestCase):
    def setUp(self):
        self.overlay = identify_overlay.IdentifyOverlay(logging.getLogger("identify-test"))
        self.addCleanup(self.overlay.hide)
        self.later = []
        patcher = mock.patch.object(identify_overlay.AppHelper, "callLater",
                                   lambda delay, callback, *args: self.later.append((delay, callback, args)))
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(identify_overlay.desktop_mac, "display_map", return_value={
            "0": Rect(1920, 0, 1512, 982), "1": Rect(0, 0, 1920, 1080)})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_numbered_panels_ignore_clicks_and_never_become_key_or_main(self):
        app = AppKit.NSApplication.sharedApplication()
        key, main = app.keyWindow(), app.mainWindow()
        self.overlay.show({"0": 2, "1": 1})
        self.assertEqual([panel.identify_number for panel in self.overlay.panels], [2, 1])
        for panel in self.overlay.panels:
            self.assertTrue(panel.ignoresMouseEvents())
            self.assertFalse(panel.canBecomeKeyWindow())
            self.assertFalse(panel.canBecomeMainWindow())
            self.assertTrue(panel.styleMask() & AppKit.NSWindowStyleMaskNonactivatingPanel)
            self.assertTrue(panel.collectionBehavior() & AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary)
        self.assertEqual(app.keyWindow(), key)
        self.assertEqual(app.mainWindow(), main)

    def test_each_label_is_centred_on_its_physical_display(self):
        self.overlay.show({"0": 2, "1": 1})
        for panel, centre in zip(self.overlay.panels, (2676, 960)):
            frame = panel.frame()
            self.assertEqual(frame.origin.x + frame.size.width / 2, centre)

    def test_timeout_hides_and_releases_all_panels_after_three_seconds(self):
        self.overlay.show({"0": 2, "1": 1})
        panels = list(self.overlay.panels)
        delay, expire, args = self.later[-1]
        self.assertEqual(delay, 3.0)
        expire(*args)
        self.assertEqual(self.overlay.panels, [])
        self.assertFalse(any(panel.isVisible() for panel in panels))

    @mock.patch("time.monotonic", return_value=0.0)
    def test_repeat_does_not_replace_panels_or_extend_expiry_and_reopens_at_boundary(self, clock):
        self.overlay.show({"0": 2, "1": 1})
        previous = list(self.overlay.panels)
        _delay, old_expire, old_args = self.later[-1]
        clock.return_value = 2.9
        for _ in range(100):
            self.overlay.show({"0": 1, "1": 2})
        self.assertEqual(self.overlay.panels, previous)
        self.assertEqual(len(self.later), 1)
        clock.return_value = 3.0
        self.overlay.show({"0": 2, "1": 1})
        old_expire(*old_args)
        self.assertEqual(len(self.overlay.panels), 2)
        self.assertTrue(self.overlay.panels[0].isVisible())
        self.assertFalse(any(panel.isVisible() for panel in previous))
        _delay, expire, args = self.later[-1]
        expire(*args)
        self.assertEqual(self.overlay.panels, [])

    @mock.patch("time.monotonic", return_value=0.0)
    def test_failed_show_and_explicit_hide_do_not_reset_request_limit(self, clock):
        with mock.patch.object(identify_overlay.desktop_mac, "display_map", side_effect=RuntimeError("unavailable")):
            self.overlay.show({"0": 1})
        self.overlay.hide()
        clock.return_value = 2.9
        self.overlay.show({"0": 1})
        self.assertEqual(self.overlay.panels, [])
        clock.return_value = 3.0
        self.overlay.show({"0": 1})
        self.assertEqual(len(self.overlay.panels), 2)

    def test_stale_request_is_replaced_by_numbers_for_all_current_displays(self):
        self.overlay.show({"missing": 3})
        self.assertEqual([panel.identify_number for panel in self.overlay.panels], [1, 2])

    def test_newly_attached_display_is_identified_with_the_same_live_numbering(self):
        self.overlay.show({"1": 1})
        self.assertEqual([panel.identify_number for panel in self.overlay.panels], [1, 2])


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.controller = object.__new__(bridge.KVMController)
        self.settings = dict(machine_id=OWN, peers=[dict(id=PEER, port=24820),
                            dict(id=OLDER, port=24820), dict(id=PHONE, port=0)],
                            layout_set_at=1, layout={OWN: dict(x=0, y=0, w=320, h=100, displays=DISPLAYS),
                            PEER: dict(x=320, y=0, w=320, h=100, displays=DISPLAYS)})
        self.controller.book = SimpleNamespace(lock=threading.RLock(), _load=lambda: self.settings,
                                              peers=lambda: self.settings["peers"])
        self.controller.identity = lambda: {"id": protocol.read_id(OWN)}
        self.controller.crossing_displays_changed = mock.Mock()
        self.controller.on_identify_screens = mock.Mock()
        self.controller.logger = logging.getLogger("identify-routing-test")
        self.controller._peers_up = {}
        self.controller.responder = None

    def link(self, peer, capable=True):
        return SimpleNamespace(peer_id=peer, live=lambda: True, post=mock.Mock(),
                               caps={"identify_screens"} if capable else set())

    def test_local_and_peer_labels_match_the_map_and_older_peers_receive_nothing(self):
        current, older, phone = self.link(PEER), self.link(OLDER, False), self.link(PHONE)
        self.controller._peers_up = {PEER: current, OLDER: older, PHONE: phone}
        self.controller.identify_screens()
        expected = identify_screens.display_numbers(ways.derive_layout(self.settings, gap=0)[OWN])
        self.controller.on_identify_screens.assert_called_once_with(expected)
        self.assertEqual(protocol.read_identify_screens(current.post.call_args.args[0]), expected)
        older.post.assert_not_called()
        phone.post.assert_not_called()

    def test_inbound_only_link_receives_once_and_dual_link_is_not_duplicated(self):
        current = self.link(PEER)
        self.controller._peers_up = {PEER: current}
        server = SimpleNamespace(links=lambda: [protocol.read_id(PEER), protocol.read_id(OLDER)],
                                 caps_of=lambda _peer: {"identify_screens"}, send=mock.Mock())
        self.controller.responder = server
        self.controller.identify_screens()
        self.assertEqual(current.post.call_count, 1)
        self.assertEqual(server.send.call_count, 1)
        self.assertEqual(server.send.call_args.args[0], protocol.read_id(OLDER))

    def test_received_request_is_local_only_and_requires_capable_desktop(self):
        labels = {"0": 2, "1": 1}
        self.controller._link_message(self.link(PEER), protocol.identify_screens_msg(labels))
        self.controller.on_identify_screens.assert_called_once_with(labels)
        self.controller.on_identify_screens.reset_mock()
        self.controller._link_message(self.link(OLDER, False), protocol.identify_screens_msg(labels))
        self.controller._link_message(self.link(PHONE), protocol.identify_screens_msg(labels))
        self.controller.on_identify_screens.assert_not_called()


class MapTests(unittest.TestCase):
    def test_smallest_valid_tiles_keep_title_number_and_cycle_within_the_tile(self):
        smallest = ways.resize_tile(dict(x=0, y=0, w=160, h=100), 40)
        self.assertEqual((smallest["w"], smallest["h"]), (40, 25))
        for height in (25, 28, 31):
            canvas = diagram.AtlasCanvas.alloc().init().setup()
            canvas.setFrame_(((0, 0), (600, 300)))
            tiles = {OWN: dict(smallest, h=height), PEER: dict(smallest, x=40, h=height)}
            canvas.show_layout(tiles, {}, {OWN: "This Mac", PEER: "A long peer name"}, OWN, OWN,
                               cycle={PEER: True}, cycle_enabled=True)
            frames = {OWN: (20, 20, 40, height), PEER: (60, 20, 40, height)}
            with mock.patch.object(canvas, "_map", return_value=(frames, 1)):
                canvas._draw(False)
            for key, (x, y, width, tile_height) in frames.items():
                title = canvas.label_for_tile[key]
                number = canvas.display_number_layers[key][0]
                cycle = canvas.cycle_for_tile[key]
                visible = [title, number] + ([] if cycle.isHidden() else [cycle])
                self.assertGreaterEqual(title.frame().size.width, 24)
                self.assertGreaterEqual(title.frame().size.height, 12)
                for layer in visible:
                    box = layer.frame()
                    self.assertGreaterEqual(box.origin.x, x)
                    self.assertGreaterEqual(box.origin.y, y)
                    self.assertLessEqual(box.origin.x + box.size.width, x + width)
                    self.assertLessEqual(box.origin.y + box.size.height, y + tile_height)
                for index, first in enumerate(visible):
                    for second in visible[index + 1:]:
                        self.assertFalse(AppKit.NSIntersectsRect(first.frame(), second.frame()))
                if not cycle.isHidden():
                    tick = cycle.sublayers()[0].frame()
                    self.assertLessEqual(tick.origin.x + tick.size.width, cycle.frame().size.width)
                    self.assertLessEqual(tick.origin.y + tick.size.height, cycle.frame().size.height)

    def test_compact_titles_keep_readable_width_and_separate_number_and_cycle_rows(self):
        from test_map_ticks import canvas_for

        for size in ((260, 260), (200, 260)):
            canvas = canvas_for(size=size)
            for key, tile in canvas._map()[0].items():
                title = canvas.label_for_tile[key].frame()
                self.assertGreaterEqual(title.size.width, min(18, tile[2] - 16))
                self.assertGreaterEqual(title.origin.y, tile[1])
                self.assertLessEqual(title.origin.y + title.size.height, tile[1] + tile[3])
                cycle = canvas.cycle_for_tile[key]
                for badge in canvas.display_number_layers[key]:
                    self.assertFalse(AppKit.NSIntersectsRect(title, badge.frame()))
                    if not cycle.isHidden():
                        self.assertFalse(AppKit.NSIntersectsRect(cycle.frame(), badge.frame()))

    def test_display_badges_match_overlay_labels_including_single_display(self):
        canvas = diagram.AtlasCanvas.alloc().init().setup()
        canvas.setFrame_(((0, 0), (600, 300)))
        tiles = {OWN: dict(x=0, y=0, w=320, h=100, displays=DISPLAYS),
                 PEER: dict(x=320, y=0, w=160, h=100)}
        canvas.show_layout(tiles, {}, {OWN: "This Mac", PEER: "Peer"}, OWN, OWN)
        canvas._draw(False)
        for peer, tile in tiles.items():
            expected = [str(value) for value in identify_screens.display_numbers(tile).values()]
            self.assertEqual([str(layer.string()) for layer in canvas.display_number_layers[peer]], expected)

    def test_numbers_do_not_overlap_name_status_or_footer_on_stacked_or_split_displays(self):
        for displays in (DISPLAYS, {"0": dict(x=0, y=0, w=1920, h=1080),
                                    "1": dict(x=0, y=1080, w=1920, h=1080)}):
            canvas = diagram.AtlasCanvas.alloc().init().setup()
            canvas.setFrame_(((0, 0), (600, 300)))
            tile = dict(x=0, y=0, w=320, h=100, displays=displays)
            canvas.show_layout({OWN: tile}, {OWN: {"platform": "macOS"}}, {OWN: "This Mac"}, OWN, OWN)
            canvas._draw(False)
            for badge in canvas.display_number_layers[OWN]:
                for text in (canvas.label_for_tile[OWN], canvas.state_for_tile[OWN], canvas.outcome_for_tile[OWN]):
                    if not text.isHidden():
                        self.assertFalse(AppKit.NSIntersectsRect(text.frame(), badge.frame()))


class ButtonTests(Page):
    def test_crossing_button_runs_identify_without_modifying_settings(self):
        with mock.patch.object(bridge.KVMController, "identify_screens") as identify:
            window = self.build([])
            before = self.store.current()
            button = window.identify_screens_button
            self.assertEqual(button.title, "Identify screens")
            self.assertTrue(button.view.isEnabled())
            button.target.fire_(button.view)
            identify.assert_called_once_with()
            self.assertEqual(self.store.current(), before)


if __name__ == "__main__":
    unittest.main()
