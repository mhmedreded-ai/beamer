"""The offscreen Crossing page: stable per-machine ledger rows and menu routing."""

import base64
import copy
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import AppKit
import rumps.rumps as rumps_module

AppKit.NSApplication.sharedApplication()

import bridge
import kvm_bridge_app
import settings_store
from bridge_fakes import PAIRED_TOKEN
from core import protocol, ways
from fake_link import FakeLink
from wake import WakingController

BEE = protocol.id_text(bytes(range(40, 56)))
SEA = protocol.id_text(bytes(range(70, 86)))
OAK = protocol.id_text(bytes(range(90, 106)))


def token(start):
    return base64.urlsafe_b64encode(bytes(range(start, start + 32))).decode("ascii").rstrip("=")


def entry(ident, secret, name, side="", own="", **extra):
    return {**settings_store.PEER_DEFAULTS, "id": ident, "name": name, "platform": "windows", "token": secret,
            "host": "192.0.2.30", "port": 24820, "paired_at": 1_700_000_000, "linked": True, "side": side,
            "side_set_at": 100 if side else 0, "side_by": own if side else "", **extra}


def press(button):
    button._ledger_target.fire_(button)


class Page(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "config.json"
        raw = settings_store.config_to_raw(settings_store.editable_default_config())
        raw.update(host="192.0.2.20", auth_token=PAIRED_TOKEN)
        path.write_text(json.dumps(raw))
        self.store = settings_store.SettingsStore(path)
        self.later = []
        patcher = mock.patch.object(kvm_bridge_app.AppHelper, "callLater", lambda _delay, call: self.later.append(call))
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, peers, zones=()):
        settings = copy.deepcopy(self.store.current())
        own = settings["machine_id"]
        settings["peers"] = [entry(*peer[:3], **{"own": own, **(peer[3] if len(peer) > 3 else {})}) for peer in peers]
        settings["zones"] = list(zones)
        self.store.save_settings(settings)
        logger = logging.getLogger("test-crossing-page-machines")
        book, identity = bridge.links_from_store(self.store, "1.5.2")
        self.controller = WakingController(self.store.load(), logger=logger, book=book, identity=identity,
                                           link_factory=FakeLink)
        self.controller.start_input_capture = mock.Mock()
        with mock.patch.object(kvm_bridge_app, "accessibility_granted", return_value=True), \
                mock.patch.object(kvm_bridge_app, "input_monitoring_granted", return_value=True):
            self.window = kvm_bridge_app.ControlWindow.alloc().initWithController_settingsStore_logger_(
                self.controller, self.store, logger)
        self.addCleanup(self.window.appearance_watch.stop)
        self.window.windows_input = mock.Mock()
        self.window.windows_input.send_arrangement.return_value = True
        self.window.windows_input.server.links.return_value = [protocol.read_id(peer[0]) for peer in peers]
        self.window.windows_input.server.caps_of.return_value = None
        self.window.windows_input.server.owner = None
        self.sent = []
        self.sent_by = []
        self.controller.send_arrangement = lambda peer, edge, set_at, by=None: (
            self.sent.append((peer, edge, set_at)) or self.sent_by.append(by) or True)
        self.window._show_arrangement()
        return self.window

    def two(self):
        return self.build(
            [(BEE, token(100), "Bee", {"side": "right"}), (SEA, token(150), "Sea", {"side": "left"})],
            [{"peer": BEE, "kind": "edge"}, {"peer": SEA, "kind": "part", "parts": ["start"]},
             {"peer": SEA, "kind": "corner", "corner": "bottom_left", "edge": "left"}])

    def ways_of(self, peer):
        return ways.ways(self.store.current(), peer)

    def methods(self):
        return self.ways_of(self.window.chosen_peer)["methods"]

    def toggle(self, way):
        peer = self.window.chosen_peer
        if way == "edge":
            press(self.window.crossing_ledger.rows[peer].contact())
        elif way == "corner":
            self.window._ledger_corner(peer, self.ways_of(peer)["corner"])
        elif way == "notch":
            self.window._ledger_notch(peer, "notch" not in self.methods())
        elif way == "shortcut":
            self.window.method_boxes[way]._toggle()
        else:
            raise ValueError(way)

    def pick(self, peer):
        self.window._atlas_selected(peer)


class Machines(Page):
    def test_no_paired_machine_has_no_ledger_rows(self):
        self.assertEqual(self.build([]).crossing_ledger.rows, {})

    def test_each_paired_desktop_has_a_named_row_and_phone_has_none(self):
        window = self.build([(BEE, token(100), "Bee", {"side": "right"}),
                             (SEA, token(150), "Phone", {"platform": "ios", "port": 0})])
        self.assertEqual(list(window.crossing_ledger.rows), [BEE])
        self.assertEqual(window.crossing_ledger.rows[BEE].name.text, "Bee")

    def test_selecting_a_row_only_changes_map_and_row_selection(self):
        window = self.two()
        before = copy.deepcopy(self.store.current())
        self.pick(SEA)
        self.assertEqual(window.chosen_peer, SEA)
        self.assertTrue(window.crossing_ledger.rows[SEA].selected)
        self.assertFalse(window.crossing_ledger.rows[BEE].selected)
        self.assertEqual(window.atlas_canvas.selected, SEA)
        self.assertEqual(self.store.current(), before)

    def test_peer_way_change_saves_immediately_and_preserves_other_peers(self):
        window = self.two()
        before = self.ways_of(BEE)
        self.pick(SEA)
        press(window.crossing_ledger.rows[SEA].contact())
        self.assertFalse(set(self.ways_of(SEA)["methods"]) & {"edge", "part"})
        self.assertEqual(self.ways_of(BEE), before)
        self.assertFalse(window._save_pending)

    def test_way_change_rearms_the_engine(self):
        window = self.two()
        with mock.patch.object(self.controller, "zones_changed") as rebuilt:
            press(window.crossing_ledger.rows[BEE].contact())
        rebuilt.assert_called()

    def test_pairing_and_forgetting_add_and_remove_rows(self):
        window = self.two()
        row = window.crossing_ledger.rows[BEE]
        self.store.add_peer(entry(OAK, token(180), "Oak"))
        window.peers_changed()
        self.assertIn(OAK, window.crossing_ledger.rows)
        self.store.remove_peer(token(150))
        window.peers_changed()
        self.assertNotIn(SEA, window.crossing_ledger.rows)
        self.assertIs(window.crossing_ledger.rows[BEE], row)

    def test_link_name_changes_update_the_existing_row(self):
        window = self.two()
        row = window.crossing_ledger.rows[SEA]
        settings = self.store.current()
        next(peer for peer in settings["peers"] if peer["id"] == SEA)["name"] = "Sea Studio"
        self.store.save_settings(settings)
        window._show_arrangement()
        self.assertIs(window.crossing_ledger.rows[SEA], row)
        self.assertEqual(row.name.text, "Sea Studio")

    def test_same_on_both_offers_granular_groups(self):
        window = self.two()
        self.assertEqual(set(window.same_checks), {"crossing", "design", "shortcut", "names", "everything"})


class TheMenuBar(Page):
    def tray(self):
        tray = mock.Mock()
        tray.controller = self.controller
        tray.control_window = self.window
        return tray

    def menu_tray(self):
        tray = self.tray()
        tray.menu = rumps_module.Menu()
        tray.toggle_item = kvm_bridge_app.rumps.MenuItem(kvm_bridge_app.TOGGLE_ITEM)
        tray.menu.add(tray.toggle_item)
        tray.menu.add(kvm_bridge_app.rumps.MenuItem("Pause crossing"))
        tray._send_keys = []
        return tray

    def titles(self, tray):
        return [item.title for item in tray.menu.values() if not item.hidden]

    def test_a_machine_menu_item_routes_input_to_that_peer(self):
        self.two()
        self.controller.event_tap = object()
        self.controller.input_error = None
        with mock.patch.object(self.controller, "set_redirecting", return_value=True) as switch:
            kvm_bridge_app.TrayApp._send_to(self.tray(), SEA)
        switch.assert_called_once_with(True, peer=SEA, reason="menu")

    def test_the_current_machine_menu_item_brings_input_back(self):
        self.two()
        self.controller.event_tap = object()
        self.controller.input_error = None
        self.controller.owner.on = SEA
        self.controller.redirecting = True
        with mock.patch.object(self.controller, "set_redirecting", return_value=True) as switch:
            kvm_bridge_app.TrayApp._send_to(self.tray(), SEA)
        switch.assert_called_once_with(False)

    def test_another_machine_menu_item_moves_input_directly(self):
        self.two()
        self.controller.event_tap = object()
        self.controller.input_error = None
        self.controller.owner.on = BEE
        self.controller.redirecting = True
        with mock.patch.object(self.controller, "set_redirecting", return_value=True) as switch:
            kvm_bridge_app.TrayApp._send_to(self.tray(), SEA)
        switch.assert_called_once_with(True, peer=SEA, reason="menu")

    def test_two_machines_have_two_menu_items_and_current_says_bring_back(self):
        self.two()
        tray = self.menu_tray()
        kvm_bridge_app.TrayApp._machine_items(tray)
        self.assertEqual(self.titles(tray), ["Send input to Bee", "Send input to Sea", "Pause crossing"])
        self.controller.owner.on = SEA
        self.controller.redirecting = True
        kvm_bridge_app.TrayApp._machine_items(tray)
        self.assertEqual(self.titles(tray), ["Send input to Bee", "Bring input back", "Pause crossing"])

    def test_one_machine_keeps_the_single_menu_item(self):
        self.build([(BEE, token(100), "Bee", {"side": "left"})])
        tray = self.menu_tray()
        kvm_bridge_app.TrayApp._machine_items(tray)
        self.assertEqual(self.titles(tray), [kvm_bridge_app.TOGGLE_ITEM, "Pause crossing"])

    def test_a_send_disabled_machine_leaves_the_menu(self):
        self.two()
        tray = self.menu_tray()
        self.store.set_peer(token(150), send=False)
        kvm_bridge_app.TrayApp._machine_items(tray)
        self.assertEqual(self.titles(tray), [kvm_bridge_app.TOGGLE_ITEM, "Pause crossing"])

    def test_permissions_missing_prevent_the_menu_sending_input(self):
        self.two()
        self.controller.event_tap = None
        tray = self.tray()
        with mock.patch.object(self.controller, "set_redirecting") as switch:
            kvm_bridge_app.TrayApp._send_to(tray, SEA)
        switch.assert_not_called()
        tray.notify_user.assert_called_once()


if __name__ == "__main__":
    unittest.main()
