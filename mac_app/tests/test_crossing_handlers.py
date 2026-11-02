"""Crossing outcomes rebuild and re-arm before sending over either Mac link."""

import copy
import json
import logging
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import AppKit
AppKit.NSApplication.sharedApplication()

import bridge
from bridge import KVMController, links_from_store
from bridge_fakes import FakeQuartz, PAIRED_TOKEN, make_config
from core import crossing_state, protocol, receiver, ways as core_ways
import kvm_bridge_app
import settings_store


PEER = protocol.id_text(bytes(range(1, 17)))
OTHER = protocol.id_text(bytes(range(21, 37)))


class CrossingOutcomeTests(unittest.TestCase):
    def controller(self):
        controller = KVMController(make_config(), quartz=FakeQuartz,
                                   logger=logging.getLogger("crossing-outcome-test"))
        own = protocol.id_text(controller.identity()["id"])
        self.events = []
        self.held = dict(machine_id=own, peers=[dict(id=PEER, side="right", side_set_at=100,
                        side_by=own, port=24820, send=True, in_use=True)],
                        zones=[dict(peer=PEER, kind="edge", side="right")],
                        layout={own: dict(x=0, y=0, w=160, h=100),
                                PEER: dict(x=160, y=0, w=160, h=100)})

        def save(settings):
            self.events.append("save")
            self.held = copy.deepcopy(settings)

        controller.book = receiver.PeerBook(lambda: self.held, save, threading.RLock())
        controller.responder = SimpleNamespace(
            peers_changed=lambda: self.events.append("peers_changed"),
            rearm=lambda: self.events.append("rearm"), links=lambda: [],
            send=lambda *args: self.events.append("inbound_tell"))
        controller.zones_changed = lambda: self.events.append("zones_changed")
        controller.send_arrangement = lambda *args: self.events.append("tell") or True
        controller.announce = lambda: [protocol.settings_msg({"own": True})]
        controller._send_crossing_picture = lambda data, source=None: self.events.append(("picture", data, source))
        return controller

    def test_saved_ways_rearm_before_telling_the_peer(self):
        controller = self.controller()
        outcome = crossing_state.edit_ways(self.held, PEER, platform="mac", edge=False,
                                           live=None, gone=None, now=200, capable={PEER})
        controller.carry_crossing(outcome)
        self.assertEqual(self.events, ["save", "zones_changed", "peers_changed", "rearm", "tell"])
        self.assertFalse(self.held["peers"][0]["ways"]["edge"])
        self.assertEqual(self.held["zones"], [])

    def test_event_compute_and_save_hold_the_book_but_rearm_does_not(self):
        controller = self.controller()
        original_save = controller.book._save

        def save(settings):
            self.assertTrue(controller.book.lock._is_owned())
            original_save(settings)

        def handler(settings, **kwargs):
            self.assertTrue(controller.book.lock._is_owned())
            return crossing_state.edit_ways(settings, PEER, edge=False, **kwargs)

        controller.book._save = save
        controller.responder.rearm = lambda: self.assertFalse(controller.book.lock._is_owned())
        controller.crossing_event(handler, platform="mac", live=None, gone=None, now=200)
        self.assertEqual(self.events.count("save"), 1)
        self.assertEqual(self.events[:3], ["save", "zones_changed", "peers_changed"])

    def test_forwarded_picture_keeps_the_arrival_and_excludes_its_source(self):
        controller = self.controller()
        state = dict(set_at=200, by=PEER, tiles=copy.deepcopy(self.held["layout"]))
        state["tiles"][PEER].update(x=0, y=-100)
        outcome = crossing_state.receive_picture(self.held, state, source=PEER, platform="mac",
            capable={PEER}, live=None, gone=None, own_displays={}, now=201)
        arrival = {"layout": state, "unrelated": "retained"}
        controller.carry_crossing(outcome, source=PEER, arrived=arrival)
        self.assertEqual(self.events[:4], ["save", "zones_changed", "peers_changed", "rearm"])
        self.assertEqual(self.events[-1], ("picture", arrival, PEER))
        self.assertEqual(self.held["peers"][0]["side"], "top")

    def test_old_picture_does_not_reset_a_push_in_progress(self):
        controller = self.controller()
        self.held.update(layout_set_at=200, layout_by=PEER)
        outcome = crossing_state.receive_picture(self.held,
            dict(set_at=199, by=PEER, tiles=self.held["layout"]), source=PEER, platform="mac",
            capable={PEER}, live=None, gone=None, own_displays={}, now=201)
        controller.carry_crossing(outcome)
        self.assertEqual(self.events, [])

    def test_a_picture_peer_arrangement_keeps_position_and_sends_no_reply(self):
        controller = self.controller()
        before = copy.deepcopy(self.held["layout"])
        outcome = crossing_state.receive_arrangement(self.held, PEER,
            dict(edge="top", set_at=200, by=protocol.read_id(PEER), way_back=False),
            platform="mac", capable={PEER}, live=None, gone=None, now=201)
        controller.carry_crossing(outcome)
        self.assertEqual(self.events, ["save", "zones_changed", "peers_changed", "rearm"])
        self.assertEqual(self.held["layout"], before)
        self.assertEqual(self.held["peers"][0]["side"], "right")
        self.assertFalse(self.held["peers"][0]["way_back"])

    def test_a_tell_uses_the_inbound_link_when_no_outbound_link_is_up(self):
        controller = self.controller()
        controller.send_arrangement = lambda *args: False
        outcome = crossing_state.edit_ways(self.held, PEER, platform="mac", edge=False,
                                           live=None, gone=None, now=200, capable={PEER})
        controller.carry_crossing(outcome)
        self.assertEqual(self.events[-2:], ["rearm", "inbound_tell"])

    def test_pictures_use_only_layout_peers_and_do_not_duplicate_two_links(self):
        controller = self.controller()
        self.held["peers"].append(dict(id=OTHER, port=24820))
        old = protocol.id_text(bytes(range(41, 57)))
        self.held["peers"].append(dict(id=old, port=24820))
        sent = []

        def outbound(peer, caps):
            return SimpleNamespace(caps=caps, live=lambda: True,
                                   post=lambda message: sent.append((peer, message)))

        controller._peers_up = {PEER: outbound(PEER, {"settings", "layout"}),
                                old: outbound(old, {"settings"})}
        controller.responder.links = lambda: [protocol.read_id(PEER), protocol.read_id(OTHER)]
        controller.responder.caps_of = lambda peer: {"settings", "layout"}
        controller.responder.send = lambda peer, message: sent.append((protocol.id_text(peer), message))
        KVMController._send_crossing_picture(controller, {"same_on_both": False})
        self.assertEqual([peer for peer, _message in sent], [PEER, OTHER])
        sent.clear()
        KVMController._send_crossing_picture(controller, {"same_on_both": False}, source=PEER)
        self.assertEqual([peer for peer, _message in sent], [OTHER])

    def test_disconnecting_a_picture_peer_keeps_its_observed_capability(self):
        controller = self.controller()
        controller._peers_up = {PEER: SimpleNamespace(caps={"layout"})}
        self.assertIn(PEER, controller.crossing_capable())
        controller._peers_up.clear()
        self.assertIn(PEER, controller.crossing_capable())
        self.held["peers"].clear()
        self.assertNotIn(PEER, controller.crossing_capable())


class ShortcutCycleStore:
    """A store that injects a link-up settings save between an unlocked read and write."""

    def __init__(self):
        self.lock = threading.RLock()
        self.settings = {"peers": [{"id": PEER, "shortcut_cycle": False},
                                    {"id": OTHER, "shortcut_cycle": True}],
                         "link_up_displays": ["old"]}
        self.injected = False
        self.saved_under_lock = False

    def current(self):
        if not self.lock._is_owned() and not self.injected:
            self.injected = True
            snapshot = copy.deepcopy(self.settings)
            self.settings["link_up_displays"] = ["new"]
            return snapshot
        if self.lock._is_owned() and not self.injected:
            self.injected = True
            self.settings["link_up_displays"] = ["new"]
        return copy.deepcopy(self.settings)

    def save_settings(self, settings):
        self.saved_under_lock = self.lock._is_owned()
        self.settings = copy.deepcopy(settings)

    def load(self):
        return {"loaded": True}


class CrossingHandlerTests(unittest.TestCase):
    def stored_controller(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = settings_store.SettingsStore(Path(directory.name) / "settings.json")
        settings = copy.deepcopy(store.current())
        settings["peers"] = [{**settings_store.PEER_DEFAULTS, "id": PEER,
                               "token": PAIRED_TOKEN, "platform": "windows", "host": "192.0.2.10",
                               "port": 24820, "side": "right"}]
        settings["zones"] = [{"peer": PEER, "kind": "edge"}]
        store.save_settings(settings)
        book, identity = links_from_store(store, "1.5.2")
        controller = KVMController(store.load(), quartz=FakeQuartz, book=book, identity=identity)
        return store, controller

    def test_crossing_save_keeps_the_immediate_full_screen_hold_before_debounce(self):
        store, controller = self.stored_controller()
        window = SimpleNamespace(controller=controller, _changed=lambda: None)
        controller.full_screen_app = "Steam"
        kvm_bridge_app.ControlWindow._hold_changed(window, True)
        self.assertEqual(controller.full_screen_app, "Steam")

        outcome = controller.crossing_event(crossing_state.edit_ways, PEER,
            platform="mac", edge=False, live=None, gone=None, now=200)

        self.assertTrue(outcome["changed"])
        self.assertEqual(controller.full_screen_app, "Steam")
        self.assertFalse(store.current()["crossing"]["hold_full_screen"])
        self.assertNotIn("edge", controller.cfg.crossing["methods"])
        self.assertFalse(controller.crossing.armed)
        kvm_bridge_app.ControlWindow._hold_changed(window, False)
        self.assertIsNone(controller.full_screen_app)

    def test_a_full_screen_toggle_during_the_crossing_config_copy_is_not_lost(self):
        _store, controller = self.stored_controller()
        snapshot = threading.Event()
        toggle_attempt = threading.Event()
        errors = []
        original_lock = controller._cfg_lock
        original_replace = bridge.dataclasses.replace

        class ObservedLock:
            def __enter__(self):
                if threading.current_thread().name == "hold-toggle":
                    toggle_attempt.set()
                original_lock.acquire()

            def __exit__(self, *_args):
                original_lock.release()

        controller._cfg_lock = ObservedLock()
        window = SimpleNamespace(controller=controller, _changed=toggle_attempt.set)

        def replace(*args, **kwargs):
            snapshot.set()
            if not toggle_attempt.wait(2):
                raise AssertionError("the hold toggle never attempted its update")
            return original_replace(*args, **kwargs)

        def toggle():
            try:
                if not snapshot.wait(2):
                    raise AssertionError("the crossing config was never copied")
                kvm_bridge_app.ControlWindow._hold_changed(window, True)
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=toggle, name="hold-toggle")
        worker.start()
        try:
            with mock.patch.object(bridge.dataclasses, "replace", side_effect=replace):
                controller.crossing_event(crossing_state.edit_ways, PEER,
                    platform="mac", edge=False, live=None, gone=None, now=200)
        finally:
            snapshot.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        controller.full_screen_app = "Steam"
        self.assertEqual(controller.full_screen_app, "Steam")

    def test_shortcut_cycle_reads_and_saves_under_lock_without_losing_link_state(self):
        store = ShortcutCycleStore()
        window = SimpleNamespace(
            _flush=lambda: None,
            settings_store=store,
            controller=SimpleNamespace(cfg=None),
            _show_arrangement=lambda: None,
            logger=logging.getLogger("crossing-handler-test"),
        )

        kvm_bridge_app.ControlWindow._set_shortcut_cycle(window, PEER, True)

        self.assertTrue(store.saved_under_lock)
        self.assertTrue(store.settings["peers"][0]["shortcut_cycle"])
        self.assertTrue(store.settings["peers"][1]["shortcut_cycle"])
        self.assertEqual(store.settings["link_up_displays"], ["new"])

    def test_ledger_handlers_ignore_left_out_or_not_in_use_peers(self):
        with tempfile.TemporaryDirectory() as directory:
            raw = settings_store.config_to_raw(settings_store.editable_default_config())
            path = Path(directory) / "config.json"
            path.write_text(json.dumps(raw))
            store = settings_store.SettingsStore(path)
            settings = copy.deepcopy(store.current())
            own = settings["machine_id"]
            settings["peers"] = [{**settings_store.PEER_DEFAULTS, "id": PEER,
                                   "token": "test-peer-token", "platform": "windows", "port": 24820,
                                   "side": "right"}]
            settings["layout"] = {own: {"x": 0, "y": 0, "w": 100, "h": 100},
                                   PEER: {"x": 100, "y": 0, "w": 100, "h": 100}}
            settings["zones"] = [{"peer": PEER, "kind": "edge", "side": "right"}]
            store.save_settings(settings)
            window = SimpleNamespace(settings_store=store, has_notch=True,
                                     chosen_peer=PEER, _write_ways=mock.Mock(),
                                     _reflect=mock.Mock(), _load_ways=mock.Mock())

            for exclusion in ({"left_out": True}, {"in_use": False}):
                with self.subTest(exclusion=exclusion):
                    active = copy.deepcopy(store.current())
                    peer = next(item for item in active["peers"] if item["id"] == PEER)
                    peer.update(left_out=False, in_use=True)
                    peer.update(exclusion)
                    store.save_settings(active)
                    tiles = core_ways.derive_layout(store.current(), gap=0)
                    self.assertEqual(core_ways._rect_contact(tiles[own], tiles[PEER])[0], "right")
                    window._write_ways.reset_mock()

                    kvm_bridge_app.ControlWindow._ledger_edge(window, PEER)
                    kvm_bridge_app.ControlWindow._ledger_corner(window, PEER, "top_left")
                    kvm_bridge_app.ControlWindow._ledger_notch(window, PEER, True)

                    window._write_ways.assert_not_called()


if __name__ == "__main__":
    unittest.main()
