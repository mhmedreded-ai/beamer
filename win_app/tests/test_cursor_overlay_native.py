"""Opt-in Win32 proof; its window stays outside every monitor and never injects input."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from unittest import mock

from PySide6.QtCore import QPoint
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QApplication

import cursor_overlay


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("BEAMER_CURSOR_NATIVE_PROOF") == "1",
                     "opt-in native offscreen proof")
class NativeCursorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_native_window_recovers_and_releases_without_taking_input(self):
        api = cursor_overlay.user32
        api.GetForegroundWindow.restype = wintypes.HWND
        api.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        api.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        api.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        foreground = api.GetForegroundWindow()
        moved = threading.Event()
        overlay = cursor_overlay.CursorOverlay(moved, lambda: True)
        self.addCleanup(overlay.close)
        live_flags, live_mouse, live_point = overlay._state()
        # Running Beamer can temporarily blank shared cursors. Read its configured arrow without restoring them.
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as key:
            arrow_file = os.path.expandvars(winreg.QueryValueEx(key, "Arrow")[0])
        if not arrow_file:
            self.skipTest("native proof needs an explicit system arrow file")
        api.LoadCursorFromFileW.argtypes = [wintypes.LPCWSTR]
        api.LoadCursorFromFileW.restype = wintypes.HANDLE
        api.DestroyCursor.argtypes = [wintypes.HANDLE]
        arrow = api.LoadCursorFromFileW(arrow_file)
        self.assertTrue(arrow)
        self.addCleanup(api.DestroyCursor, arrow)
        arrow_source = mock.patch.object(api, "LoadCursorW", return_value=arrow)
        arrow_source.start()
        self.addCleanup(arrow_source.stop)
        x = api.GetSystemMetrics(76) + api.GetSystemMetrics(78) + 1000
        y = api.GetSystemMetrics(77) + api.GetSystemMetrics(79) + 1000
        point = wintypes.POINT(x, y)
        overlay.set_active(True)
        moved.set()
        with mock.patch.object(overlay, "_state", return_value=(0, False, point)):
            overlay._tick()
            self.app.processEvents()
            overlay._tick()
        self.assertFalse(overlay._failed)
        self.assertTrue(overlay.isVisible())
        hwnd = int(overlay.winId())
        style = api.GetWindowLongPtrW(hwnd, -20)
        required = 0x20 | 0x80000 | 0x8 | 0x08000000
        self.assertEqual(style & required, required)
        self.assertEqual(api.GetForegroundWindow(), foreground)
        rect = wintypes.RECT()
        self.assertTrue(api.GetWindowRect(hwnd, ctypes.byref(rect)))
        self.assertEqual((rect.left, rect.top), (x - overlay._hotspot[0], y - overlay._hotspot[1]))
        self.assertEqual((rect.right - rect.left, rect.bottom - rect.top),
                         (overlay._image.width(), overlay._image.height()))
        self.assertGreater(overlay._image.width(), 0)
        point.x += 100
        point.y += 70
        with mock.patch.object(overlay, "_state", return_value=(0, False, point)):
            overlay._tick()
        self.assertTrue(api.GetWindowRect(hwnd, ctypes.byref(rect)))
        self.assertEqual((rect.left, rect.top),
                         (point.x - overlay._hotspot[0], point.y - overlay._hotspot[1]))
        shot = overlay.grab().toImage()
        self.assertTrue(any(shot.pixelColor(a, b).alpha() > 0
                            for a in range(shot.width()) for b in range(shot.height())))
        folder = os.environ.get("BEAMER_CURSOR_PROOF_DIR")
        if folder:
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            self.assertTrue(shot.save(str(target / "native-arrow.png")))
            canvas = QImage(320, 160, QImage.Format_ARGB32)
            canvas.fill(QColor("#f4f4f4"))
            painter = QPainter(canvas)
            painter.fillRect(160, 0, 160, 160, QColor("#202020"))
            painter.drawImage(QPoint(64, 48), shot)
            painter.drawImage(QPoint(224, 48), shot)
            painter.end()
            self.assertTrue(canvas.save(str(target / "native-arrow-backgrounds.png")))
            (target / "native-proof.json").write_text(json.dumps({
                "live_cursor_flags": live_flags, "live_mouse_present": live_mouse,
                "live_position": [live_point.x, live_point.y],
                "forced_flags": 0, "forced_mouse_present": False,
                "arrow_source": "configured system arrow file; shared cursor left untouched",
                "extended_style": hex(style), "required_style": hex(required),
                "window_rect": [rect.left, rect.top, rect.right, rect.bottom],
                "hotspot": overlay._hotspot, "image_size": [shot.width(), shot.height()],
                "device_pixel_ratio": overlay.devicePixelRatioF(),
                "foreground_unchanged": api.GetForegroundWindow() == foreground,
            }, indent=2), encoding="utf-8")
        with mock.patch.object(overlay, "_state", return_value=(1, False, point)):
            overlay._tick()
        self.assertFalse(overlay.isVisible())
        with mock.patch.object(overlay, "_state", return_value=(0, False, point)):
            overlay._tick()
        self.assertTrue(overlay.isVisible())
        with mock.patch.object(overlay, "_state", return_value=(0, True, point)):
            overlay._tick()
        self.assertFalse(overlay.isVisible())
        with mock.patch.object(overlay, "_state", return_value=(0, False, point)), \
                mock.patch.object(cursor_overlay.desktop_win, "_on_default_desktop", return_value=False):
            overlay._tick()
        self.assertFalse(overlay.isVisible())
        overlay.set_active(False)
        self.assertFalse(overlay.isVisible())
        self.assertFalse(overlay._timer.isActive())
