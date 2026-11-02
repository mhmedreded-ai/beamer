"""Screen numbers with synthetic displays/settings; run from Claude, one process."""

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QEventLoop, QRect, QRectF, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import identify_overlay
from core import protocol
from core.identify_screens import display_numbers
from core.return_edge import Rect
from diagram import AtlasCanvas
from sender import LinkSender
import test_crossing_page_parity as fixture


class IdentifyOverlayTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])

    def fake_screen(self, x, y, w=1920, h=1080, ratio=1, name="display"):
        return SimpleNamespace(geometry=lambda: QRect(x, y, w, h), devicePixelRatio=lambda: ratio, name=lambda: name)

    def test_native_monitor_order_and_mixed_dpi_match_qt(self):
        left, right = self.fake_screen(-1920, 0), self.fake_screen(0, 0, 1280, 720, 2)
        displays = {"0": Rect(0, 0, 2560, 1440), "1": Rect(-1920, 0, 1920, 1080)}
        assignments = identify_overlay.screen_assignments([left, right], displays, {"1": 1, "0": 2})
        self.assertEqual(assignments, [(left, 1), (right, 2)])
        scaled = self.fake_screen(1280, 0, 960, 540, 2)
        assignments = identify_overlay.screen_assignments([right, scaled],
            {"0": Rect(0, 0, 2560, 1440), "1": Rect(2560, 0, 1920, 1080)}, {"0": 1, "1": 2})
        self.assertEqual(assignments, [(right, 1), (scaled, 2)])

    def test_wayland_uses_portal_qt_keys_even_when_zone_origins_differ(self):
        a, b = self.fake_screen(0, 0), self.fake_screen(1920, 0)
        assignments = identify_overlay.screen_assignments([a, b],
            {"0": Rect(40, 50, 1920, 1080), "1": Rect(1960, 50, 1920, 1080)}, {"1": 1, "0": 2}, wayland=True)
        self.assertEqual(assignments, [(b, 1), (a, 2)])

    def test_xwayland_uses_connectors_or_geometry_not_enumeration(self):
        left = self.fake_screen(-1920, 0, name="left")
        right = self.fake_screen(0, 0, 1280, 720, name="right")
        payload = [{"geometry": [0, 0, 1280, 720], "name": "right", "number": 2},
                   {"geometry": [-1920, 0, 1920, 1080], "name": "left", "number": 1}]
        self.assertEqual(identify_overlay.x11_screen_regions([left, right], payload),
                         [(right, 2, right.geometry()), (left, 1, left.geometry())])

    def test_xwayland_single_virtual_output_keeps_each_physical_region(self):
        desktop = self.fake_screen(0, 0, 3840, 1080, name="XWAYLAND0")
        payload = [{"geometry": [0, 0, 1920, 1080], "name": "left", "number": 1},
                   {"geometry": [1920, 0, 1920, 1080], "name": "right", "number": 2}]
        self.assertEqual(identify_overlay.x11_screen_regions([desktop], payload),
                         [(desktop, 1, QRect(0, 0, 1920, 1080)), (desktop, 2, QRect(1920, 0, 1920, 1080))])

    def test_failed_layer_shell_uses_xwayland_but_never_focusable_native_fallback(self):
        controller = identify_overlay.IdentifyScreens()
        self.addCleanup(controller.stop)
        controller._backend = "layer-shell"
        with patch.dict(identify_overlay.os.environ, {"DISPLAY": ":123"}), \
                patch.object(controller, "_start_x11") as x11, patch.object(controller, "_show_qt") as native:
            controller._fallback()
            x11.assert_called_once()
            native.assert_not_called()
        controller._backend = "XWayland frameless per-screen windows"
        with patch.object(identify_overlay.QGuiApplication, "platformName", return_value="wayland"), \
                patch.object(controller, "_show_qt") as native:
            controller._fallback()
            native.assert_not_called()

    @patch("time.monotonic", return_value=0.0)
    def test_flags_geometry_repeat_does_not_replace_or_extend_three_second_expiry(self, clock):
        controller = identify_overlay.IdentifyScreens()
        self.addCleanup(controller.stop)
        screen = self.app.primaryScreen()
        geometry = screen.geometry()
        display = {"0": Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())}
        controller.show_numbers({"0": 1}, display)
        first = controller.windows[0]
        self.assertEqual(first.geometry(), geometry)
        self.assertEqual(first.number, 1)
        for flag in (Qt.WindowType.FramelessWindowHint, Qt.WindowType.WindowTransparentForInput,
                     Qt.WindowType.WindowDoesNotAcceptFocus, Qt.WindowType.WindowStaysOnTopHint):
            self.assertTrue(first.windowFlags() & flag)
        self.assertTrue(first.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
        self.assertTrue(first.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertEqual(controller._expiry.interval(), 3000)
        clock.return_value = 2.9
        remaining = controller._expiry.remainingTime()
        for _ in range(100):
            controller.show_numbers({"0": 2}, display)
        self.assertIs(controller.windows[0], first)
        self.assertTrue(first.isVisible())
        self.assertEqual(len(controller.windows), 1)
        self.assertEqual(controller.windows[0].number, 1)
        self.assertLessEqual(controller._expiry.remainingTime(), remaining)
        # Observe expiry directly; a separate wait timer can finish before an offscreen timeout
        # is delivered, and a generous deadline allows for event-loop scheduling under load.
        loop = QEventLoop()
        deadline = QTimer()
        expired = []
        deadline.setSingleShot(True)
        deadline.setTimerType(Qt.TimerType.PreciseTimer)
        deadline.timeout.connect(loop.quit)
        controller._expiry.timeout.connect(lambda: expired.append(True))
        controller._expiry.timeout.connect(loop.quit)
        deadline.start(5000)
        loop.exec()
        deadline.stop()
        self.assertTrue(expired, "the three-second overlay expiry timer did not fire within five seconds")
        self.assertFalse(controller.windows)
        clock.return_value = 3.0
        controller.show_numbers({"0": 2}, display)
        self.assertEqual(controller.windows[0].number, 2)

    @patch("time.monotonic", return_value=0.0)
    def test_wayland_helper_flood_is_limited_even_after_failure_and_stop(self, clock):
        controller = identify_overlay.IdentifyScreens()
        self.addCleanup(controller.stop)
        screen = self.fake_screen(0, 0)
        with patch.object(identify_overlay.QGuiApplication, "platformName", return_value="wayland"), \
                patch.object(identify_overlay.QGuiApplication, "screens", return_value=[screen]), \
                patch.object(identify_overlay.sys, "platform", "linux"), \
                patch.dict(identify_overlay.os.environ, {"DISPLAY": ""}), \
                patch.object(controller, "_start_layer_shell") as start:
            controller.show_numbers({"0": 1}, {"0": Rect(0, 0, 1920, 1080)})
            controller._backend = "layer-shell"
            process = Mock()
            process.readAllStandardError.return_value = b"unavailable"
            controller._process = process
            controller._helper_finished(process)
            controller.stop()
            clock.return_value = 2.9
            for _ in range(100):
                controller.show_numbers({"0": 2}, {"0": Rect(0, 0, 1920, 1080)})
            self.assertEqual(start.call_count, 1)
            clock.return_value = 3.0
            controller.show_numbers({"0": 2}, {"0": Rect(0, 0, 1920, 1080)})
            self.assertEqual(start.call_count, 2)

    def test_helper_failed_start_and_timeout_both_fall_back(self):
        controller = identify_overlay.IdentifyScreens()
        self.addCleanup(controller.stop)
        failed = Mock()
        failed.readAllStandardError.return_value = b"optional binding missing"
        controller._process = failed
        controller._pending = [("screen", 1)]
        with patch.object(controller, "_show_qt") as show:
            controller._helper_finished(failed)
            show.assert_called_once()
        timed_out = Mock()
        timed_out.readAllStandardError.return_value = b""
        controller._process = timed_out
        with patch.object(controller, "_show_qt") as show:
            controller._fallback()
            timed_out.kill.assert_called_once()
            timed_out.waitForFinished.assert_called_once_with(250)
            show.assert_called_once()

    def test_helper_failure_logs_backend_and_bounded_stderr(self):
        controller = identify_overlay.IdentifyScreens()
        self.addCleanup(controller.stop)
        process = Mock()
        process.readAllStandardError.return_value = b"x" * 4096
        controller._process = process
        controller._backend = "layer-shell"
        with self.assertLogs(identify_overlay.LOGGER, level="WARNING") as recorded:
            controller._log_helper_failure(process, "exited before readiness")
        self.assertEqual(len(controller._helper_stderr), 2048)
        self.assertIn("layer-shell helper exited before readiness", recorded.output[0])

    def test_map_badges_and_overlay_assignments_use_same_numbers(self):
        tile = {"x": 0, "y": 0, "w": 320, "h": 100, "displays": {
            "left": {"x": -1920, "y": 0, "w": 1920, "h": 1080},
            "right": {"x": 0, "y": 0, "w": 1920, "h": 1080}}}
        canvas = AtlasCanvas()
        self.addCleanup(canvas.deleteLater)
        canvas.resize(600, 260)
        canvas.set_tiles({"own": tile}, {"own": "This PC"}, {"own": {"state": "this"}}, "own", own_id="own")
        canvas.grab()
        badges = {display: number for _, display, number, _ in canvas.display_number_rects}
        self.assertEqual(badges, display_numbers(tile))
        for _, display, _, box in canvas.display_number_rects:
            self.assertTrue(canvas.display_rect("own", display).contains(box))

    def test_sender_dispatch_and_older_peer_no_op(self):
        callback = Mock()
        sender = SimpleNamespace(links=SimpleNamespace(caps=lambda peer: {"identify_screens"}),
                                 identify_callback=callback, _call=lambda fn, *args: fn(*args))
        message = protocol.identify_screens_msg({"0": 1})
        LinkSender.on_message(sender, b"peer", message)
        callback.assert_called_once_with(b"peer", {"0": 1})
        callback.reset_mock()
        sender.links.caps = lambda peer: set()
        LinkSender.on_message(sender, b"peer", message)
        callback.assert_not_called()

    def test_frozen_x11_entry_bypasses_singleton_and_real_settings(self):
        import kvm_bridge_win as kvm

        with patch.object(kvm, "parse_args", return_value=SimpleNamespace(identify_x11="[]")), \
                patch.object(identify_overlay, "run_x11_helper", return_value=0) as child, \
                patch.object(kvm, "_claim_instance") as claim, \
                patch.object(kvm.app_config, "load_config") as load:
            with self.assertRaises(SystemExit) as exit_result:
                kvm.main()
            self.assertEqual(exit_result.exception.code, 0)
            child.assert_called_once_with("[]")
            claim.assert_not_called()
            load.assert_not_called()

    def test_stacked_display_badges_leave_text_rows_clear(self):
        badges = [QRectF(5, 29, 18, 16), QRectF(5, 79, 18, 16)]
        for row in (QRectF(8, 7, 304, 22), QRectF(24, 31, 288, 16), QRectF(7, 76, 306, 16)):
            room = AtlasCanvas._beside_display_numbers(row, badges)
            self.assertTrue(row.contains(room))
            self.assertTrue(all(not room.intersects(badge) for badge in badges))

    def test_compact_tiles_keep_number_row_separate_from_readable_names(self):
        canvas = AtlasCanvas()
        self.addCleanup(canvas.deleteLater)
        canvas.resize(600, 260)
        for height in (25, 28, 31, 36, 48):
            width = 40 if height < 32 else 140
            tiles = {"own": {"x": 0, "y": 0, "w": width, "h": height},
                     "peer": {"x": width, "y": 0, "w": width, "h": height}}
            canvas.set_tiles(tiles, {"own": "This PC", "peer": "Laptop"},
                             {"own": {"state": "this"}, "peer": {"state": "edge"}}, "peer",
                             cycle_mode="ticked", cycle={"peer": True}, own_id="own")
            canvas._geometry()
            for peer in tiles:
                tile = canvas.tile_rect(peer)
                title = canvas._title_rect(peer, tile)
                badges = [box for _, _, box in canvas._display_number_boxes(peer, tile)]
                self.assertGreater(title.width(), 24 if height < 32 else 65)
                self.assertTrue(tile.contains(title))
                self.assertTrue(all(not title.intersects(box) for box in badges))
                if peer == "peer":
                    self.assertTrue(tile.contains(canvas._cycle_box(tile)))
                    self.assertTrue(all(not canvas._cycle_box(tile).intersects(box) for box in badges))


class IdentifyPageTests(unittest.TestCase):
    setUp = fixture.CrossingPageParityTests.setUp
    tearDown = fixture.CrossingPageParityTests.tearDown
    _open_window = fixture.CrossingPageParityTests._open_window
    _close_window = fixture.CrossingPageParityTests._close_window
    _stop_patches = fixture.CrossingPageParityTests._stop_patches

    def test_both_receive_links_ignore_phone_and_unknown_peer_before_overlay(self):
        window = self.window
        desktop = protocol.read_id(self.peers[0]["id"])
        phone, unknown = bytes([90]) * 16, bytes([91]) * 16
        peers = self.peers + [{"id": protocol.id_text(phone), "port": 0}]
        with patch.object(window.book, "peers", return_value=peers), \
                patch.object(window.identify_screens, "show_numbers") as show:
            for callback in (window.sender.identify_callback, window.server._callbacks["identify"]):
                with self.subTest(callback=callback):
                    callback(phone, {"0": 1})
                    callback(unknown, {"0": 1})
                    QApplication.processEvents()
                    show.assert_not_called()
                    callback(desktop, {"0": 1})
                    QApplication.processEvents()
                    show.assert_called_once()
                    show.reset_mock()

    @patch("time.monotonic", return_value=0.0)
    def test_local_button_and_both_receive_links_share_overlay_limit(self, clock):
        window = self.window
        peer = protocol.read_id(self.peers[0]["id"])
        geometry = self.app.primaryScreen().geometry()
        displays = {"0": Rect(geometry.x(), geometry.y(), geometry.width(), geometry.height())}
        with patch.object(window, "_peer_caps", return_value={}), \
                patch.object(window, "_displays_changed"), \
                patch.object(window, "_refresh_own_displays"), \
                patch.object(window, "_own_displays", return_value=displays), \
                patch.object(window.identify_screens, "_show_qt") as show:
            window._identify_screens()
            clock.return_value = 2.9
            for callback in (window.sender.identify_callback, window.server._callbacks["identify"]):
                callback(peer, {"0": 1})
            window._identify_screens()
            QApplication.processEvents()
            self.assertEqual(show.call_count, 1)
            clock.return_value = 3.0
            window._identify_screens()
            self.assertEqual(show.call_count, 2)

    def test_button_identifies_local_and_all_capable_linked_peers(self):
        window = self.window
        self.assertIn("identify_screens", window._identity()["caps"])
        capable, old = (protocol.read_id(peer["id"]) for peer in self.peers)
        window._peer_caps = lambda: {capable: {"identify_screens"}, old: {"settings"}}
        with patch.object(window, "_send_to", return_value=True) as send, \
                patch.object(window.identify_screens, "show_numbers") as show:
            window.identify_button.click()
            show.assert_called_once()
            send.assert_called_once()
            self.assertEqual(send.call_args.args[0], capable)
            self.assertEqual(protocol.read_identify_screens(send.call_args.args[1]),
                             display_numbers(window.atlas_canvas.tiles.get(protocol.id_text(capable))))

    def test_stale_partial_labels_use_complete_live_numbering(self):
        window = self.window
        displays = {"0": Rect(1920, 0, 1920, 1080), "1": Rect(0, 0, 1920, 1080)}
        with patch.object(window, "_refresh_own_displays"), patch.object(window, "_own_displays", return_value=displays), \
                patch.object(window.identify_screens, "show_numbers") as show:
            window._on_identify_screens({"0": 1})
            show.assert_called_once_with({"1": 1, "0": 2}, displays)

    def test_linked_shelved_peer_gets_all_its_display_numbers(self):
        window = self.window
        peer = self.peers[0]["id"]
        tile = {"x": 160, "y": 0, "w": 320, "h": 100, "displays": {
            "0": {"x": 1920, "y": 0, "w": 1920, "h": 1080},
            "1": {"x": 0, "y": 0, "w": 1920, "h": 1080}}}
        window._config.layout[peer] = tile
        window._peer_caps = lambda: {protocol.read_id(peer): {"identify_screens"}}
        window.atlas_canvas.tiles.pop(peer, None)
        with patch.object(window, "_displays_changed"), \
                patch.object(window, "_crossing_view_settings", return_value={"machine_id": window._config.machine_id,
                             "peers": [], "layout": window._config.layout}), \
                patch.object(window, "_on_identify_screens"), patch.object(window, "_send_to") as send:
            window._identify_screens()
            self.assertEqual(protocol.read_identify_screens(send.call_args.args[1]), {"1": 1, "0": 2})


if __name__ == "__main__":
    unittest.main()
