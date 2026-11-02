"""Profiles over fake links and a synthetic book; no application or real input."""

import copy
import json
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import bridge
import settings_store
from bridge_fakes import FakeClipboard, FakeQuartz, make_config, quiet_logger
from fake_link import FakeLink
from core import crossing_state, profiles, protocol, receiver
from core.tests.test_profiles import A, B, C, raw


class ProfileSyncTests(unittest.TestCase):
    def setUp(self):
        self.held, _ = profiles.before_load(raw(), now=100)
        self.held, _ = profiles.after_load(self.held, platform="mac", now=100)
        self.held["peers"].append(dict(id=C, port=24820, send=True, allow_drive=True))
        self.saved = []

        def save(settings):
            self.saved.append(copy.deepcopy(settings))
            self.held = copy.deepcopy(settings)

        self.controller = bridge.KVMController(make_config(), quartz=FakeQuartz,
            clipboard=FakeClipboard(), logger=quiet_logger(), link_factory=FakeLink)
        self.controller.book = receiver.PeerBook(lambda: self.held, save, threading.RLock())
        self.controller.announce = lambda: [protocol.settings_msg(dict(
            on=False, set_at=9, by=A, groups=[], layout={"tiles": {}}, design_sync={}))]
        self.sent = []
        self.controller._peers_up = {B: self.link(B), C: self.link(C)}
        self.controller.zones_changed = lambda: None
        # What linked peers were last told, as after the app's first publish at start.
        self.controller._desk_published = profiles.desk_message(self.held)

    def link(self, peer, caps=("settings", "profiles", "layout")):
        return SimpleNamespace(peer_id=peer, caps=frozenset(caps), live=lambda: True,
            post=lambda message: self.sent.append((peer, message)))

    def desks(self):
        return [(peer, message["data"]) for peer, message in self.sent
                if message["type"] == protocol.MSG_SETTINGS and "desk" in message["data"]]

    def test_local_register_edits_use_separate_frames_and_only_profile_peers(self):
        self.controller._peers_up[C] = self.link(C, ("settings", "layout"))
        outcome = crossing_state.set_same_ways(self.held, True, platform="mac", now=200)
        with mock.patch.object(profiles, "MAX_DESK_BYTES", 240):
            self.controller.carry_crossing(outcome)
        desks = self.desks()
        self.assertGreater(len(desks), 1)
        self.assertEqual({peer for peer, _ in desks}, {B})
        for _, data in desks:
            self.assertEqual(data["on"], False)
            self.assertEqual(data["set_at"], 9)
            self.assertEqual(data["by"], A)
            self.assertNotIn("layout", data)
            self.assertNotIn("design_sync", data)
        self.assertTrue(any("on" in data["desk"] for _, data in desks))

    def test_a_change_made_outside_a_crossing_event_still_reaches_linked_peers(self):
        # App review: pairing (and a load that reconciled) saved desk changes no event carried.
        changed = crossing_state.set_same_ways(self.held, True, platform="mac", now=200)["settings"]
        self.held = changed
        self.controller._peers_up[C] = self.link(C, ("settings", "layout"))
        self.controller.publish_desk()
        desks = self.desks()
        self.assertEqual({peer for peer, _ in desks}, {B}, "only peers with profiles")
        sent = [data["desk"] for _, data in desks]
        self.assertTrue(any("on" in desk for desk in sent))
        self.sent.clear()
        self.controller.publish_desk()
        self.assertEqual(self.desks(), [], "an unchanged desk is not sent again")

    def test_forward_goes_to_others_and_answer_only_to_source(self):
        forward = {"on": dict(value=True, set_at=202, by=B)}
        answer = {"active": dict(value=profiles.DESK_ID, set_at=203, by=A)}
        outcome = crossing_state._outcome(self.held, forward=forward, answer=answer)
        self.controller.carry_crossing(outcome, source=B)
        self.assertEqual([(peer, data["desk"]) for peer, data in self.desks()],
                         [(C, forward), (B, answer)])

    def test_inbound_link_gets_desk_without_duplicating_outbound(self):
        self.controller.responder = SimpleNamespace(links=lambda: [protocol.read_id(B), protocol.read_id(C)],
            caps_of=lambda ident: ("settings", "profiles"),
            send=lambda ident, message: self.sent.append((protocol.id_text(ident), message)))
        self.controller._peers_up.pop(C)
        self.controller._send_desk({"on": dict(value=False, set_at=0, by="")})
        self.assertEqual([peer for peer, _ in self.desks()], [B, C])

    def test_link_up_sends_whole_desk_only_to_that_peer(self):
        self.controller._announce_paired = lambda link: None
        self.controller.crossing_link_up = lambda peer: None
        self.controller._announce_to(self.controller._peers_up[B], {})
        desks = self.desks()
        self.assertEqual({peer for peer, _ in desks}, {B})
        self.assertEqual(desks[0][1]["desk"]["profiles"][profiles.DESK_ID]["name"]["value"], "Desk")

    def test_arrival_runs_crossing_event_and_saves_before_rearming(self):
        stages = []
        self.controller.zones_changed = lambda: stages.append("zones")
        self.controller.responder = SimpleNamespace(links=lambda: [],
            peers_changed=lambda: stages.append("peers"), rearm=lambda: stages.append("rearm"))
        data = dict(on=False, set_at=200, by=B,
                    desk={"on": dict(value=True, set_at=200, by=B)})
        outcome = self.controller.receive_desk(data, B, linked=True, now=201)
        self.assertTrue(outcome["accepted"])
        self.assertEqual(self.held["crossing_desk"]["on"]["value"], True)
        self.assertTrue(self.saved)
        self.assertEqual(stages, ["zones", "peers", "rearm"])
        self.assertTrue(self.controller.desk_last_heard[B])

    def test_unlinked_and_malformed_arrivals_do_not_change_settings(self):
        data = dict(on=False, set_at=200, by=B,
                    desk={"on": dict(value=True, set_at=200, by=B)})
        self.assertFalse(self.controller.receive_desk(data, B, linked=False, now=201)["accepted"])
        self.assertIsNone(self.controller.receive_desk({"desk": {}}, B, linked=True, now=201))
        self.assertFalse(self.saved)
        self.assertFalse(self.controller.desk_last_heard.get(B, False))

    def test_profile_capability_and_last_heard_reset_survive_reconnect(self):
        self.assertIn(B, self.controller.profiles_capable())
        self.controller.desk_last_heard[B] = True
        self.controller.reset_desk_peer(B)
        self.controller._peers_up.clear()
        self.assertIn(B, self.controller.profiles_capable())
        self.assertFalse(self.controller.desk_last_heard.get(B, False))

    def test_new_profile_event_does_not_inject_unsupported_capable_keyword(self):
        outcome = self.controller.crossing_event(crossing_state.new_profile, "Sofa", platform="mac", now=201)
        self.assertTrue(outcome["accepted"])
        self.assertIn("Sofa", [item["name"] for item in profiles.profile_list(self.held)])

    def test_inbound_announcement_sends_full_desk_and_caches_capability(self):
        from windows_input import WindowsInput
        inbound = WindowsInput.__new__(WindowsInput)
        inbound._controller = self.controller
        inbound.server = SimpleNamespace(links=lambda: [protocol.read_id(B)],
            caps_of=lambda ident: frozenset(("settings", "layout", "profiles")),
            send=lambda ident, message: self.sent.append((protocol.id_text(ident), message)))
        self.controller.responder = inbound.server
        self.controller._peers_up.clear()
        self.controller.crossing_link_up = lambda peer: None
        inbound._announce(protocol.read_id(B))
        self.assertEqual({peer for peer, _ in self.desks()}, {B})
        self.controller._peers_up.clear()
        inbound.server.links = lambda: []
        self.assertIn(B, self.controller.profiles_capable())


class LoadNoteTests(unittest.TestCase):
    def test_older_version_note_is_in_memory_and_consumed_once(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[2]) as directory:
            store = settings_store.SettingsStore(Path(directory) / "settings.json")
            held, _ = profiles.before_load(raw(), now=100)
            held, _ = profiles.after_load(held, platform="mac", now=100)
            held = store.save_settings(held)
            held["peers"][0]["ways"]["thirds"] = ["end"]
            store.path.write_text(json.dumps(held))
            loaded = store.load_settings()
            self.assertEqual(loaded["crossing_own"]["row"][B]["ways"]["thirds"], ["end"])
            self.assertTrue(store.take_profile_load_note("older_version"))
            self.assertFalse(store.take_profile_load_note("older_version"))
            self.assertNotIn("profile_load_notes", loaded)


if __name__ == "__main__":
    unittest.main()
