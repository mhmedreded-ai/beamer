"""A system arrow for a remotely driven Windows desktop with no mouse cursor."""

import ctypes
from ctypes import wintypes
import logging
import sys

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QWidget

import desktop_win


LOGGER = logging.getLogger(__name__)
CURSOR_SHOWING = 1
CURSOR_SUPPRESSED = 2
SM_MOUSEPRESENT = 19


def needs_overlay(driven, moved, flags, mouse_present):
    # An attached mouse's hidden cursor can be deliberate (games and video players).
    return bool(driven and moved and flags is not None and not mouse_present
                and (not flags & CURSOR_SHOWING or flags & CURSOR_SUPPRESSED))


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("hCursor", wintypes.HANDLE), ("ptScreenPos", wintypes.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", wintypes.BOOL), ("xHotspot", wintypes.DWORD),
                ("yHotspot", wintypes.DWORD), ("hbmMask", wintypes.HBITMAP),
                ("hbmColor", wintypes.HBITMAP)]


user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True) if sys.platform == "win32" else None
if user32 is not None:
    user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
    user32.GetCursorInfo.restype = wintypes.BOOL
    user32.LoadCursorW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
    user32.LoadCursorW.restype = wintypes.HANDLE
    user32.GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ICONINFO)]
    user32.GetIconInfo.restype = wintypes.BOOL
    user32.CreateIconIndirect.argtypes = [ctypes.POINTER(ICONINFO)]
    user32.CreateIconIndirect.restype = wintypes.HANDLE
    user32.DestroyIcon.argtypes = [wintypes.HANDLE]
    user32.DestroyIcon.restype = wintypes.BOOL
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos.restype = wintypes.BOOL
    gdi32.DeleteObject.argtypes = [wintypes.HANDLE]
    gdi32.DeleteObject.restype = wintypes.BOOL


class CursorOverlay(QWidget):
    def __init__(self, moved, receiving, parent=None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.NoFocus)
        self._remote_move = moved
        self._receiving = receiving
        self._driven = False
        self._moved = False
        self._image = QImage()
        self._hotspot = (0, 0)
        self._failed = False
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_active(self, driven):
        self._driven = driven
        if driven:
            self._timer.start()
        else:
            self._timer.stop()
            self._remote_move.clear()
            self._moved = False
            self.hide()

    def _state(self):
        info = CURSORINFO(cbSize=ctypes.sizeof(CURSORINFO))
        if not user32.GetCursorInfo(ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.flags, bool(user32.GetSystemMetrics(SM_MOUSEPRESENT)), info.ptScreenPos

    def _tick(self):
        if not self._driven or not self._receiving():
            self._remote_move.clear()
            self._moved = False
            self.hide()
            return
        self._moved = self._moved or self._remote_move.is_set()
        if not self._moved:
            self.hide()
            return
        try:
            flags, mouse_present, point = self._state()
            if not needs_overlay(self._driven, self._moved, flags, mouse_present):
                self.hide()
                return
            if not desktop_win._on_default_desktop():
                self.hide()
                return
            self._load_arrow()
            x, y = point.x - self._hotspot[0], point.y - self._hotspot[1]
            if not self.isVisible():
                self.move(x, y)
                self.show()
            hwnd = int(self.winId())
            desktop_win.set_click_through(hwnd, True)
            # Win32 cursor coordinates and window placement must share physical pixels at any DPI.
            if not user32.SetWindowPos(hwnd, wintypes.HWND(-1), x, y, self._image.width(),
                                      self._image.height(), 0x0010):  # SWP_NOACTIVATE
                raise ctypes.WinError(ctypes.get_last_error())
            self._failed = False
        except Exception:
            self.hide()
            if not self._failed:
                LOGGER.exception("Could not draw the remote pointer")
                self._failed = True

    def _load_arrow(self):
        arrow = user32.LoadCursorW(None, 32512)  # IDC_ARROW is the user's current system arrow.
        if not arrow:
            raise ctypes.WinError(ctypes.get_last_error())
        info = ICONINFO()
        if not user32.GetIconInfo(arrow, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            # Qt's HICON conversion rejects monochrome HCURSORs; use the same bitmaps as an icon.
            info.fIcon = True
            icon = user32.CreateIconIndirect(ctypes.byref(info))
            if not icon:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                image = QImage.fromHICON(icon)
            finally:
                user32.DestroyIcon(icon)
            if image.isNull():
                raise OSError("Windows returned an empty system arrow")
            ratio = self.devicePixelRatioF()
            image.setDevicePixelRatio(ratio)
            self._image = image
            self._hotspot = (info.xHotspot, info.yHotspot)
            self.resize(round(image.width() / ratio), round(image.height() / ratio))
            self.update()
        finally:
            for bitmap in (info.hbmMask, info.hbmColor):
                if bitmap:
                    gdi32.DeleteObject(bitmap)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawImage(0, 0, self._image)

    def closeEvent(self, event):
        self.set_active(False)
        super().closeEvent(event)
