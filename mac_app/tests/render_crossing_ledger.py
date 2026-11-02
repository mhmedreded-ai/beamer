"""Render one of the seven ledger states without ordering a window or capturing the screen."""
import copy
from pathlib import Path
import sys
from unittest import mock

from mac_app.tests.test_crossing_page_machines import Page, BEE, SEA, OAK, token
from mac_app.tests.design_offscreen import AppKit, settle
from mac_app.tests.settings_layout_offscreen import render, measure
import theme


def built_state(number, destination):
    fixture = Page()
    fixture.setUp()
    try:
        side = "left" if number in (2, 3) else "top"
        peers = [(BEE, token(100), "Gaming PC", {"side": side})]
        if number == 6:
            peers += [(SEA, token(150), "Fedora laptop", {"side": "left", "platform": "linux", "jump_key": "cmd+f18"}),
                      (OAK, token(200), "Studio Mac", {"side": "right", "platform": "macos", "shortcut_cycle": False})]
        zones = [{"peer": peer[0], "kind": "edge"} for peer in peers]
        if number == 3:
            zones = [{"peer": BEE, "kind": "part", "parts": ["start", "end"]}]
        if number in (4, 7):
            zones += [{"peer": BEE, "kind": "corner", "corner": "top_left", "edge": "top"}]
        if number == 5:
            zones[0]["off"] = True
        if number == 6:
            zones = [{"peer": BEE, "kind": "edge"},
                     {"peer": SEA, "kind": "part", "parts": ["middle"]},
                     {"peer": SEA, "kind": "corner", "corner": "bottom_left", "edge": "left"},
                     {"peer": OAK, "kind": "edge"}]
        theme.init_fonts()
        with mock.patch.object(theme, "system_dark", return_value=True):
            control = fixture.build(peers, zones)
        settings = copy.deepcopy(fixture.store.current())
        settings["name"] = "Studio MacBook"
        own = settings["machine_id"]
        settings["layout"] = {own: dict(x=0, y=0, w=160, h=100)}
        for peer, _, _, fields in peers:
            x, y = {"left": (-160, 0), "right": (160, 0), "top": (0, -100)}[fields["side"]]
            settings["layout"][peer] = dict(x=x, y=y, w=160, h=100)
        fixture.store.save_settings(settings)
        control.controller.cfg = fixture.store.load()
        control._design_peer_caps = lambda: {peer[0]: {"layout"} for peer in peers}
        control._live = lambda peer: number != 6 or peer != OAK
        control.key_recorder.set_value("cmd_r")
        control.has_notch = True
        control._select_page("crossing")
        control.window.setFrame_display_(((100000, 100000), (640 if number == 7 else 960, 900)), False)
        control._load_ways()
        control._reflect()
        for _ in range(4):
            settle(control.window)
            control.crossing_ledger.reflow()
        destination = Path(destination)
        destination.mkdir(parents=True, exist_ok=True)
        page = control.pages["crossing"].documentView()
        render(page, 2, destination / f"mac-state-{number}.png")
        geometry = measure(control, "crossing")
        assert not control.window.isVisible()
        assert not control.pages["crossing"].hasHorizontalScroller()
        assert not geometry["overflows"], geometry["overflows"]
        assert (control.crossing_ledger.bounds().size.width < control.crossing_ledger.narrow_below()) == (number == 7)
        print(f"state {number}: {page.bounds().size.width:g} x {page.bounds().size.height:g} pt, "
              f"{len(control.crossing_ledger.rows)} rows, ledger {control.crossing_ledger.bounds().size.width:g} pt, "
              f"{len(geometry['overflows'])} overflows")
    finally:
        fixture.doCleanups()


if __name__ == "__main__":
    built_state(int(sys.argv[1]), sys.argv[2])
