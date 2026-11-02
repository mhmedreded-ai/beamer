import unittest
import threading
from ctypes import wintypes
from unittest import mock

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage

import cursor_overlay
import input_injector

from cursor_overlay import needs_overlay


class CursorDecisionTests(unittest.TestCase):
    def test_remote_move_on_mouseless_target_has_a_pointer(self):
        self.assertTrue(needs_overlay(True, True, 0, False))

    def test_visible_native_pointer_wins(self):
        self.assertFalse(needs_overlay(True, True, 1, False))

    def test_suppressed_native_pointer_is_not_drawn(self):
        self.assertTrue(needs_overlay(True, True, 2, False))
        self.assertTrue(needs_overlay(True, True, 3, False))

    def test_driver_leaves(self):
        self.assertFalse(needs_overlay(False, True, 0, False))

    def test_no_pointer_until_remote_movement(self):
        self.assertFalse(needs_overlay(True, False, 0, False))

    def test_mouse_plugged_in_mid_session(self):
        self.assertTrue(needs_overlay(True, True, 0, False))
        self.assertFalse(needs_overlay(True, True, 0, True))

    def test_apps_can_intentionally_hide_an_attached_mouse(self):
        self.assertFalse(needs_overlay(True, True, 0, True))

    def test_unknown_cursor_state_fails_closed(self):
        self.assertFalse(needs_overlay(True, True, None, False))


class CursorLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.moved = threading.Event()
        self.overlay = cursor_overlay.CursorOverlay(self.moved, lambda: True)
        self.addCleanup(self.overlay.close)

    def test_idle_ownership_does_not_poll_or_create_a_native_window(self):
        self.overlay.set_active(True)
        with mock.patch.object(self.overlay, "_state") as state:
            self.overlay._tick()
        state.assert_not_called()
        self.assertFalse(self.overlay.isVisible())

    def test_driver_leaves_stops_polling_and_clears_old_movement(self):
        self.overlay.set_active(True)
        self.moved.set()
        self.overlay.set_active(False)
        self.assertFalse(self.overlay._timer.isActive())
        self.assertFalse(self.moved.is_set())
        self.assertFalse(self.overlay.isVisible())
        self.overlay.set_active(True)
        with mock.patch.object(self.overlay, "_state") as state:
            self.overlay._tick()
        state.assert_not_called()

    def test_failed_cursor_query_hides_the_overlay(self):
        self.overlay.set_active(True)
        self.moved.set()
        with mock.patch.object(self.overlay, "_state", side_effect=OSError("unavailable")):
            with self.assertLogs(cursor_overlay.LOGGER, "ERROR"):
                self.overlay._tick()
        self.assertFalse(self.overlay.isVisible())

    def test_successful_shared_injection_marks_movement(self):
        input_injector.cursor_moved.clear()
        self.addCleanup(input_injector.cursor_moved.clear)
        with mock.patch.object(input_injector, "user32"), \
                mock.patch.object(input_injector, "_send_input"), \
                mock.patch.object(input_injector, "watch_foreground"):
            input_injector.inject_mouse_move(3, 4)
        self.assertTrue(input_injector.cursor_moved.is_set())

    def test_failed_injection_does_not_start_drawing(self):
        input_injector.cursor_moved.clear()
        self.addCleanup(input_injector.cursor_moved.clear)
        with mock.patch.object(input_injector, "user32"), \
                mock.patch.object(input_injector, "_send_input", side_effect=OSError("blocked")):
            with self.assertRaises(OSError):
                input_injector.inject_mouse_move(3, 4)
        self.assertFalse(input_injector.cursor_moved.is_set())

    def test_local_drag_wakeup_cannot_leave_stale_remote_movement(self):
        input_injector.cursor_moved.clear()
        self.addCleanup(input_injector.cursor_moved.clear)
        with mock.patch.object(input_injector, "user32"), \
                mock.patch.object(input_injector, "_send_input"), \
                mock.patch.object(input_injector, "watch_foreground"):
            input_injector.inject_mouse_move(0, 0)
        self.assertFalse(input_injector.cursor_moved.is_set())

    def test_arrow_contents_refresh_when_windows_reuses_its_handle(self):
        first = QImage(16, 16, QImage.Format_ARGB32)
        first.fill(0xff010203)
        second = QImage(16, 16, QImage.Format_ARGB32)
        second.fill(0xff040506)
        with mock.patch.object(cursor_overlay, "user32") as api, \
                mock.patch.object(QImage, "fromHICON", side_effect=[first, second]):
            api.LoadCursorW.return_value = 1
            api.GetIconInfo.return_value = True
            api.CreateIconIndirect.return_value = 2
            self.overlay._load_arrow()
            self.assertEqual(self.overlay._image.pixel(0, 0), 0xff010203)
            self.overlay._load_arrow()
            self.assertEqual(self.overlay._image.pixel(0, 0), 0xff040506)

    def test_keyboard_owner_cannot_keep_a_departed_pointers_overlay_alive(self):
        self.overlay._receiving = lambda: False
        self.overlay.set_active(True)
        self.moved.set()
        with mock.patch.object(self.overlay, "_state", return_value=(1, False, wintypes.POINT())) as state:
            self.overlay._tick()
        state.assert_not_called()
        self.assertFalse(self.moved.is_set())
