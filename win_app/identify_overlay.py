"""Brief, input-transparent display numbers, on the GUI thread."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import sys
import time

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QWidget

sys.path.append(str(Path(__file__).resolve().parent.parent))

from core.identify_screens import OVERLAY_SECONDS
from core.i18n import tr

import edge_glow
import theme
import tokens

LOGGER = logging.getLogger(__name__)
DURATION_MS = round(OVERLAY_SECONDS * 1000)
CARD_SIZE = 208


def _position(rect):
    return (rect.get("x", 0), rect.get("y", 0)) if isinstance(rect, dict) else (rect.x, rect.y)


def screen_assignments(screens, displays, labels, *, wayland=False):
    """Native monitor enumeration may differ from Qt's; match origins before indices.

    The portal's display keys already index Qt's cached screens, whereas Windows and
    XRandR enumerate physical monitors independently of Qt.
    """
    remaining = list(screens)
    result = []
    for key, number in labels.items():
        if not remaining:
            break
        screen = None
        if wayland and key.isdecimal() and int(key) < len(screens):
            candidate = screens[int(key)]
            if candidate in remaining:
                screen = candidate
        rect = displays.get(key)
        if screen is None and rect is not None:
            x, y = _position(rect)
            screen = next((one for one in remaining
                           if (one.geometry().x(), one.geometry().y()) == (x, y)), None)
            if screen is None:
                screen = next((one for one in remaining
                               if (round(one.geometry().x() * one.devicePixelRatio()),
                                   round(one.geometry().y() * one.devicePixelRatio())) == (x, y)), None)
        if screen is None:
            # A single-display legacy tile has no physical geometry to match.
            if len(labels) == 1 and len(screens) == 1:
                screen = remaining[0]
            else:
                continue
        remaining.remove(screen)
        result.append((screen, number))
    return result


class ScreenNumber(QWidget):
    def __init__(self, screen, number, geometry=None):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput
                         | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.number = number
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setWindowTitle(tr("Beamer — Identify screens"))
        self.setScreen(screen)
        self.setGeometry(geometry or screen.geometry())
        if QGuiApplication.platformName() == "xcb":
            self.setWindowFlags((self.windowFlags() & ~Qt.WindowType.WindowType_Mask)
                                | Qt.WindowType.ToolTip | Qt.WindowType.X11BypassWindowManagerHint)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        size = min(CARD_SIZE, self.width() - 16, self.height() - 16)
        card = QRectF((self.width() - size) / 2, (self.height() - size) / 2, size, size)
        painter.setBrush(QColor(tokens.PALETTE["panel"]))
        painter.setPen(QPen(QColor(tokens.PALETTE["edge"]), 1.0))
        painter.drawRoundedRect(card, 12, 12)
        painter.setFont(theme.mono_font(size * 0.54, 700))
        painter.setPen(QColor(tokens.PALETTE["ink"]))
        painter.drawText(card, Qt.AlignmentFlag.AlignCenter, str(self.number))


class IdentifyScreens(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.windows = []
        self._process = None
        self._ready = False
        self._backend = ""
        self.helper_focus = None
        self._helper_stderr = b""
        self._pending = []
        self._last_request_at = None
        self._expiry = QTimer(self)
        self._expiry.setSingleShot(True)
        self._expiry.timeout.connect(self.stop)
        self._startup = QTimer(self)
        self._startup.setSingleShot(True)
        self._startup.timeout.connect(self._fallback)

    def show_numbers(self, labels, displays):
        now = time.monotonic()
        if self._last_request_at is not None and now - self._last_request_at < OVERLAY_SECONDS:
            return
        self._last_request_at = now
        self.stop()
        self.helper_focus = None
        wayland = QGuiApplication.platformName().startswith("wayland")
        assignments = screen_assignments(QGuiApplication.screens(), displays, labels, wayland=wayland)
        if not assignments:
            return
        self._pending = assignments
        if sys.platform.startswith("linux") and wayland:
            self._start_layer_shell(assignments)
        else:
            self._show_qt()

    def _start_layer_shell(self, assignments):
        self._backend = "layer-shell"
        self._helper_stderr = b""
        process = QProcess(self)
        self._process = process
        environment = QProcessEnvironment.systemEnvironment()
        for name in ("PYTHONHOME", "PYTHONPATH"):
            environment.remove(name)
        environment.insert("GDK_BACKEND", "wayland")
        if getattr(sys, "frozen", False):
            environment.insert("LD_LIBRARY_PATH", os.environ.get("LD_LIBRARY_PATH_ORIG", ""))
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(lambda: self._helper_output(process))
        process.readyReadStandardError.connect(lambda: self._read_helper_stderr(process))
        process.errorOccurred.connect(lambda _error: self._helper_finished(process))
        process.finished.connect(lambda *_args: self._helper_finished(process))
        payload = [{"geometry": [s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height()],
                    "number": number} for s, number in assignments]
        self._startup.start(5000)
        process.start("/usr/bin/python3", [str(Path(__file__).with_name("identify_layer_shell.py")),
                                          json.dumps(payload), json.dumps(tokens.PALETTE), theme.mono()])

    def _start_x11(self):
        self._backend = "XWayland frameless per-screen windows"
        self._helper_stderr = b""
        process = QProcess(self)
        self._process = process
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("QT_QPA_PLATFORM", "xcb")
        environment.remove("XDG_ACTIVATION_TOKEN")
        process.setProcessEnvironment(environment)
        process.readyReadStandardOutput.connect(lambda: self._helper_output(process))
        process.readyReadStandardError.connect(lambda: self._read_helper_stderr(process))
        process.errorOccurred.connect(lambda _error: self._helper_finished(process))
        process.finished.connect(lambda *_args: self._helper_finished(process))
        payload = [{"geometry": [s.geometry().x(), s.geometry().y(), s.geometry().width(), s.geometry().height()],
                    "name": s.name(), "number": number} for s, number in self._pending]
        entry = [] if getattr(sys, "frozen", False) else [str(Path(__file__))]
        self._startup.start(5000)
        process.start(sys.executable, entry + ["--identify-x11", json.dumps(payload)])

    def _helper_output(self, process):
        while process is self._process and process.canReadLine():
            line = bytes(process.readLine()).strip()
            if line.startswith(b"focus "):
                self.helper_focus = json.loads(line[6:])
            elif line == b"ready":
                self._ready = True
                self._startup.stop()
                self._expiry.start(DURATION_MS + 250)
                LOGGER.info("Identify screens: %s", self._backend)

    def _helper_finished(self, process):
        if process is not self._process:
            return
        ready = self._ready
        if not ready:
            self._log_helper_failure(process, "exited before readiness")
        self._process = None
        process.deleteLater()
        if not ready:
            self._fallback()

    def _read_helper_stderr(self, process):
        if process is self._process:
            self._helper_stderr = (self._helper_stderr + bytes(process.readAllStandardError()))[-2048:]

    def _log_helper_failure(self, process, reason):
        self._read_helper_stderr(process)
        detail = self._helper_stderr.decode("utf-8", errors="replace").strip() or process.errorString()
        LOGGER.warning("Identify screens: %s helper %s (exit %s, %s): %s", self._backend, reason,
                       process.exitCode(), process.exitStatus(), detail)

    def _fallback(self):
        self._startup.stop()
        process = self._process
        if process is not None:
            self._log_helper_failure(process, "did not become ready before the startup deadline")
            self._process = None
            process.kill()
            process.waitForFinished(250)
            process.deleteLater()
        if self._backend == "layer-shell" and os.environ.get("DISPLAY"):
            LOGGER.info("Identify screens: Wayland layer-shell unavailable; using XWayland frameless per-screen windows")
            self._start_x11()
            return
        if QGuiApplication.platformName().startswith("wayland"):
            LOGGER.warning("Identify screens: this Wayland session has neither working layer-shell nor XWayland; no non-focusing overlay is available")
            return
        self._show_qt()

    def _show_qt(self):
        for screen, number in self._pending:
            window = ScreenNumber(screen, number)
            self.windows.append(window)
            window.show()
            edge_glow.keep_on_top(window)
        self._expiry.start(DURATION_MS)

    def stop(self):
        self._startup.stop()
        self._expiry.stop()
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.windows.clear()
        process, self._process = self._process, None
        if process is not None:
            process.kill()
            process.waitForFinished(250)
            process.deleteLater()
        self._ready = False
        self._backend = ""
        self._pending = []


def x11_screen_regions(screens, payload):
    """Match outputs across Qt backends by connector or geometry, then desktop fraction.

    XWayland may expose one virtual output for the whole desktop. In that case the
    existing crossing helper's desktop-fraction mapping retains each source region.
    """
    from effect_overlay_x11 import mapped_point

    source = QRect()
    target = QRect()
    for item in payload:
        source = source.united(QRect(*item["geometry"]))
    for screen in screens:
        target = target.united(screen.geometry())
    remaining = list(screens)
    found = []
    for item in payload:
        rect = QRect(*item["geometry"])
        screen = (next((s for s in remaining if s.name() == item["name"]), None)
                  if len(screens) == len(payload) else None)
        if screen is None and len(screens) == len(payload):
            screen = next((s for s in remaining if s.geometry() == rect), None)
        if screen is None and len(screens) == len(payload):
            x, y = mapped_point(rect.center().x(), rect.center().y(),
                                [source.x(), source.y(), source.width(), source.height()], target)
            screen = min(remaining, key=lambda s: (s.geometry().center().x()-x)**2
                         + (s.geometry().center().y()-y)**2)
        if screen is not None:
            remaining.remove(screen)
            found.append((screen, item["number"], screen.geometry()))
        elif len(screens) == 1:
            x, y = mapped_point(rect.x(), rect.y(), [source.x(), source.y(), source.width(), source.height()], target)
            region = QRect(round(x), round(y), round(rect.width()*target.width()/source.width()),
                           round(rect.height()*target.height()/source.height()))
            found.append((screens[0], item["number"], region))
        else:
            raise ValueError("XWayland output geometry does not match the desktop")
    return found


def run_x11_helper(payload):
    from PySide6.QtWidgets import QApplication

    app = QApplication([])
    app.setQuitOnLastWindowClosed(False)
    theme.init_fonts()
    windows = []
    for screen, number, geometry in x11_screen_regions(app.screens(), json.loads(payload)):
        window = ScreenNumber(screen, number, geometry)
        windows.append(window)
        window.show()
    def mapped():
        focus = {"focus_window": app.focusWindow() is not None,
                 "active_window": app.activeWindow() is not None,
                 "active_overlay": any(window.isActiveWindow() for window in windows)}
        print("focus " + json.dumps(focus), flush=True)
        if any(focus.values()):
            app.exit(1)
            return
        print("ready", flush=True)
        QTimer.singleShot(DURATION_MS, app.quit)

    QTimer.singleShot(100, mapped)
    result = app.exec()
    for window in windows:
        window.close()
    return result


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--identify-x11":
        raise SystemExit(2)
    raise SystemExit(run_x11_helper(sys.argv[2]))
