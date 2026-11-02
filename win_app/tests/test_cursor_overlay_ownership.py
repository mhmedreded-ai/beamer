import sys
from types import SimpleNamespace
import unittest
from unittest import mock

from PySide6.QtWidgets import QApplication

import kvm_bridge_win


@unittest.skipUnless(sys.platform == "win32", "Windows cursor fallback")
class OwnershipTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def window(self, blocked=False):
        return SimpleNamespace(
            sender=mock.Mock(), _driven=False, _idle_pointer=None, _closing=False,
            _input_controller_blocked=blocked, _cursor_overlay=None,
            server=SimpleNamespace(owner=b"peer", pointer_owner=b"peer"),
            _refresh_tray_state=mock.Mock(), _refresh_tray_machines=mock.Mock(),
            _refresh_redirect_controls=mock.Mock(),
        )

    def test_shared_owner_signal_starts_and_releases_fallback(self):
        window = self.window()
        moved = kvm_bridge_win.platform_parts.injector.cursor_moved
        moved.clear()
        self.addCleanup(moved.clear)
        kvm_bridge_win.WindowsApplication._on_owner(window, "peer-one")
        overlay = window._cursor_overlay
        self.addCleanup(overlay.close)
        self.assertTrue(overlay._timer.isActive())
        moved.set()
        kvm_bridge_win.WindowsApplication._on_owner(window, "peer-two")
        self.assertIs(window._cursor_overlay, overlay)
        kvm_bridge_win.WindowsApplication._on_owner(window, "")
        self.assertFalse(overlay._timer.isActive())
        self.assertFalse(moved.is_set())

    def test_non_controller_process_does_not_create_a_window(self):
        window = self.window(blocked=True)
        kvm_bridge_win.WindowsApplication._on_owner(window, "peer")
        self.assertIsNone(window._cursor_overlay)

    def test_local_ownership_does_not_create_a_window(self):
        window = self.window()
        kvm_bridge_win.WindowsApplication._on_owner(window, "")
        self.assertIsNone(window._cursor_overlay)

    def test_independent_keyboard_owner_does_not_count_as_pointer_control(self):
        window = self.window()
        kvm_bridge_win.WindowsApplication._on_owner(window, "peer")
        overlay = window._cursor_overlay
        self.addCleanup(overlay.close)
        self.assertTrue(overlay._receiving())
        window.server.pointer_owner = None
        self.assertFalse(overlay._receiving())

    def test_legacy_receiver_uses_its_single_owner(self):
        window = self.window()
        del window.server.pointer_owner
        kvm_bridge_win.WindowsApplication._on_owner(window, "peer")
        overlay = window._cursor_overlay
        self.addCleanup(overlay.close)
        self.assertTrue(overlay._receiving())
        window.server.owner = None
        self.assertFalse(overlay._receiving())
