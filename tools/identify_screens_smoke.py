"""Native runner only: temporary screen labels, without opening the Beamer app or settings."""

import logging
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "win_app"))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from core.identify_screens import display_numbers
from core.return_edge import Rect
from identify_overlay import IdentifyScreens


def main():
    logging.basicConfig(level=logging.INFO)
    app = QApplication([])
    screens = app.screens()
    print(f"platform={app.platformName()} screens={len(screens)}", flush=True)
    displays = {str(index): Rect(screen.geometry().x(), screen.geometry().y(),
                                screen.geometry().width(), screen.geometry().height())
                for index, screen in enumerate(screens)}
    tile = {"x": 0, "y": 0, "w": 160, "h": 100, "displays": {
        key: {"x": rect.x, "y": rect.y, "w": rect.width, "h": rect.height}
        for key, rect in displays.items()}}
    overlay = IdentifyScreens()
    state = {"shown": False, "focus": False, "helper_focus": False}
    prior = app.activeWindow()

    def inspect():
        state["shown"] = overlay._ready or bool(screens) and len(overlay.windows) == len(screens)
        if not state["shown"]:
            return
        polling.stop()
        state["focus"] = app.activeWindow() is prior
        helper_focus = getattr(overlay, "helper_focus", None)
        state["helper_focus"] = helper_focus is None or not any(helper_focus.values())
        print(f"platform={app.platformName()} screens={len(screens)} "
              f"backend={overlay._backend or 'native Qt'} helper_ready={overlay._ready} "
              f"qt_windows={len(overlay.windows)} "
              f"focus_preserved={state['focus']} helper_focus={helper_focus}", flush=True)
        QTimer.singleShot(4000, app.quit)

    polling = QTimer()
    polling.timeout.connect(inspect)
    polling.start(25)
    overlay.show_numbers(display_numbers(tile), displays)
    QTimer.singleShot(9000, app.quit)
    app.exec()
    expired = not overlay.windows and overlay._process is None
    overlay.stop()
    passed = state["shown"] and state["focus"] and state["helper_focus"] and expired
    print(f"shown={state['shown']} expired={expired} result={'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
