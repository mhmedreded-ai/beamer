"""Same on all machines in the real window: the Overview switch, a PC's settings arriving, and a
change here going out. Built as test_appearance builds it, never put on screen; the two links'
sends are recorded instead."""

import copy
import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import AppKit  # noqa: E402

AppKit.NSApplication.sharedApplication()

import kvm_bridge_app  # noqa: E402
import pages  # noqa: E402
import settings_store  # noqa: E402
from bridge_fakes import PAIRED_TOKEN  # noqa: E402
from core import protocol, settings_sync  # noqa: E402
from fake_link import FakeLink, bring_up  # noqa: E402
from wake import WakingController  # noqa: E402

PEER = protocol.id_text(bytes(range(1, 17)))
LOW = protocol.id_text(bytes(15) + b"\x01")
HIGH = protocol.id_text(b"\xff" * 16)


def pc_message(on, set_at, by="", **values):
    base = dict(shortcut=False, resistance_px=60,
                block_while_dragging=False, trigger_key="cmd_r", trigger_style="hold", double_tap_ms=400,
                glow_style="beam", glow_colour="ocean", effect_length="long", shortcut_arrival=False,
                shortcut_arrival_style="locator")
    base.update(values)
    return settings_sync.message_data(on, set_at, base, by=by,
                                      groups=settings_sync.ALL_GROUPS if on else [])


class SameOnBothTests(unittest.TestCase):
    def test_idle_same_ticks_keep_one_native_follow_picker(self):
        import gc
        import objc
        import weakref

        owners = []
        original = self.window.follow_select
        for _ in range(200):
            with objc.autorelease_pool():
                self.window._show_same()
                owners.append(weakref.ref(self.window.follow_select))
        gc.collect()
        self.assertIs(self.window.follow_select, original)
        self.assertEqual(len({id(owner()) for owner in owners if owner() is not None}), 1)
        self.assertEqual(len(self.window.follow_host.arrangedSubviews()), 1)

    def test_follow_picker_recovers_when_a_peer_announces_design(self):
        self.pair_sources(PEER)
        self.window._design_peer_caps = lambda: {PEER: frozenset({"settings", "design_sync"})}
        self.window._refresh_design_follow()
        choice = self.window.follow_select
        cell = next(cell for value, cell in choice.items if value == PEER)
        self.assertFalse(cell.isEnabled())
        self.window._design_states[PEER] = {"set_at": 100, "by": PEER,
                                          "values": {"effect_length": "long"}}
        self.window._refresh_design_follow()
        self.assertIsNot(self.window.follow_select, choice)
        recovered = next(cell for value, cell in self.window.follow_select.items if value == PEER)
        self.assertTrue(recovered.isEnabled())
        self.assertEqual(recovered.toolTip(), recovered.title())

    def setUp(self):
        capture = mock.patch.object(WakingController, "start_input_capture")
        capture.start()
        self.addCleanup(capture.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "config.json"
        raw = settings_store.config_to_raw(settings_store.editable_default_config())
        raw.update(host="192.0.2.20", auth_token=PAIRED_TOKEN)
        raw["crossing"]["hold_full_screen"] = True
        raw["crossing"]["methods"] = ["shortcut", "edge", "notch"]
        path.write_text(json.dumps(raw))
        self.store = settings_store.SettingsStore(path)
        logger = logging.getLogger("test-same-on-both")
        with mock.patch.object(kvm_bridge_app, "accessibility_granted", return_value=True), \
                mock.patch.object(kvm_bridge_app, "input_monitoring_granted", return_value=True):
            self.window = kvm_bridge_app.ControlWindow.alloc().initWithController_settingsStore_logger_(
                WakingController(self.store.load(), logger=logger, link_factory=FakeLink, book=self.store.book()), self.store, logger
            )
        self.addCleanup(self.window.appearance_watch.stop)
        self.sent = []
        self.sources = []
        self.window.controller.send_settings = lambda data, source=None: self.sent.append(data) or self.sources.append(source) or True
        self.window._show_same()

    def pair_sources(self, *ids):
        for ident in ids:
            self.store.add_peer({**settings_store.PEER_DEFAULTS, "id": ident, "token": f"test-{ident}",
                                 "name": "Paired test PC", "host": "192.0.2.20", "platform": "windows",
                                 "port": protocol.DEFAULT_PORT})

    def test_off_by_default_with_each_page_its_own(self):
        self.assertFalse(self.window.same_checks["everything"].state())
        self.assertEqual(set(self.window.same_checks), {"crossing", "design", "shortcut", "names", "everything"})
        self.assertEqual(self.window.scope_labels["design"].text, pages.SCOPE["design"])
        self.assertTrue(all(note.view.isHidden() for note in self.window.own_notes))

    def test_everything_can_be_turned_off(self):
        self.window._set_same_groups(settings_sync.ALL_GROUPS)
        everything = self.window.same_checks["everything"]
        everything.setState_(AppKit.NSControlStateValueOff)
        self.window.sameGroupsChanged_(everything)

        self.assertEqual(self.window.controller.cfg.same_groups, [])

    def test_revert_restores_the_saved_values_and_announces_them(self):
        self.pair_sources(PEER)
        data = settings_sync.message_data(True, 100, {"resistance_px": 60}, by=PEER,
                                          groups=["crossing"])
        self.window.apply_same(data, peer=PEER)
        self.assertTrue(settings_sync.snapshot_available(self.store.current().get("same_before_sync")))

        self.window.revertSame_(None)

        self.assertEqual(self.window.controller.cfg.crossing["resistance_px"], 120)
        self.assertEqual(self.window.controller.cfg.same_groups, ["crossing"])
        self.assertEqual(self.sent[-1]["crossing"]["resistance_px"], 120)

    def test_revert_restores_shortcut_in_the_canonical_settings_field(self):
        self.pair_sources(PEER)
        data = settings_sync.message_data(True, 100, {"shortcut": False}, by=PEER,
                                          groups=["shortcut"])
        self.window.apply_same(data, peer=PEER)
        self.assertFalse(self.store.current()["shortcut"])

        self.window.revertSame_(None)

        self.assertTrue(self.store.current()["shortcut"])

    def test_revert_clears_a_first_remote_label_with_a_new_stamp(self):
        self.pair_sources(PEER)
        data = settings_sync.message_data(True, 100, by=PEER, groups=["names"], names={
            PEER: {"label": "Named here", "set_at": 100, "by": PEER},
        })
        self.window.apply_same(data, peer=PEER)
        entry = next(item for item in self.store.current()["peers"] if item["id"] == PEER)
        self.assertEqual(entry["label"], "Named here")

        self.window.revertSame_(None)

        restored = next(item for item in self.store.current()["peers"] if item["id"] == PEER)
        self.assertEqual(restored["label"], "")
        self.assertGreater(restored["label_set_at"], 100)

    def test_legacy_off_state_stays_off_when_announced_to_a_group_peer(self):
        self.window.controller.cfg.same_groups = ["design"]
        self.window.apply_same({"on": False, "set_at": 100, "by": PEER}, peer=None)

        self.assertFalse(self.window.same_state()["on"])
        self.assertEqual(self.window.same_state()["groups"], ["design"])

    def test_revert_keeps_the_current_trigger_when_a_jump_chord_now_uses_it(self):
        self.pair_sources(PEER)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw["trigger_key"] = "alt_r"
        self.window.controller.apply_settings(self.store.save(raw))
        original_groups = list(self.window.controller.cfg.same_groups)
        data = settings_sync.message_data(True, 100, {"trigger_key": "f13"}, by=PEER,
                                          groups=["shortcut"])
        self.window.apply_same(data, peer=PEER)
        settings = self.store.current()
        settings["peers"][0]["jump_key"] = "alt+cmd+3"
        self.store.save_settings(settings)

        self.window.revertSame_(None)

        self.assertEqual(self.window.controller.cfg.trigger_key, "f13")
        self.assertEqual(self.window.controller.cfg.same_groups, ["shortcut"])

    def test_older_settings_peer_without_layout_capability_shows_group_note(self):
        self.window._design_peer_caps = lambda: {PEER: {"settings"}}

        self.window._show_same()

        self.assertFalse(self.window.same_old_note.view.isHidden())

    def test_full_screen_hold_switch_names_this_macs_edges(self):
        self.assertEqual(self.window.hold_box.words.text, "Hold this Mac's edges while an app on it is full screen")
    def test_this_machines_design_state_is_sent_even_when_same_is_off(self):
        data = self.window.same_state()

        self.assertFalse(data["on"])
        self.assertEqual(set(data["design_sync"]["values"]), set(settings_sync.DESIGN_KEYS))
        self.assertEqual(data["design_sync"]["values"]["effect_size"], "medium")

    def test_a_followed_machine_changes_this_design_and_this_mac_announces_its_own_state(self):
        self.pair_sources(PEER)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw["design_follow_peer"] = PEER
        self.window.controller.apply_settings(self.store.save(raw))
        received = settings_sync.message_data(
            False, 50, by=PEER,
            design_state={"set_at": 100, "by": PEER,
                          "values": {"effect_length": "long", "effect_size": "large"}},
        )

        self.window.apply_same(received, peer=PEER)

        self.assertEqual(self.window.controller.cfg.crossing["effect_length"], "long")
        self.assertEqual(self.window.size_select.value, "large")
        self.assertGreater(self.window.controller.cfg.design_set_at, 100)
        self.assertEqual(self.window.controller.cfg.design_by, self.window.own_id())
        self.assertTrue(self.sent)
        self.assertEqual(self.sent[-1]["design_sync"]["values"]["effect_length"], "long")
        self.assertEqual((self.sent[-1]["design_sync"]["set_at"], self.sent[-1]["design_sync"]["by"]),
                         (self.window.controller.cfg.design_set_at, self.window.own_id()))

    def test_a_design_state_in_a_malformed_settings_envelope_is_dropped_whole(self):
        self.pair_sources(PEER)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw["design_follow_peer"] = PEER
        self.window.controller.apply_settings(self.store.save(raw))
        state = {"set_at": 100, "by": PEER, "values": {"effect_length": "long"}}

        for outer in ({"on": "no"}, {"set_at": True}, {"by": "not an id"}):
            data = settings_sync.message_data(False, 50, by=PEER, design_state=state)
            data.update(outer)
            self.window.apply_same(data, peer=PEER)

        self.assertEqual(self.window.controller.cfg.crossing["effect_length"], "normal")
        self.assertNotIn(PEER, self.window._design_states)
        self.assertEqual(self.sent, [])

    def test_follow_accepts_the_leaders_next_change_after_this_mac_reannounces(self):
        self.pair_sources(PEER)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw["design_follow_peer"] = PEER
        raw["design_set_at"] = 50
        raw["design_by"] = PEER
        self.window.controller.apply_settings(self.store.save(raw))

        self.window._receive_design(settings_sync.message_data(
            False, 50, by=PEER,
            design_state={"set_at": 100, "by": PEER, "values": {"effect_length": "long"}},
        ), PEER)
        self.window._receive_design(settings_sync.message_data(
            False, 51, by=PEER,
            design_state={"set_at": 101, "by": PEER, "values": {"effect_length": "short"}},
        ), PEER)

        self.assertEqual(self.window.controller.cfg.crossing["effect_length"], "short")
        self.assertGreater(self.window.controller.cfg.design_set_at, 101)
        self.assertEqual(self.window.controller.cfg.design_by, self.window.own_id())
        self.assertEqual(self.sent[-1]["design_sync"]["values"]["effect_length"], "short")
        self.assertEqual((self.sent[-1]["design_sync"]["set_at"], self.sent[-1]["design_sync"]["by"]),
                         (self.window.controller.cfg.design_set_at, self.window.own_id()))

    def test_same_on_design_change_is_announced_with_a_new_local_design_stamp(self):
        self.pair_sources(HIGH)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw["same_on_both"] = True
        raw["same_groups"] = list(settings_sync.ALL_GROUPS)
        raw["same_set_at"] = 10
        raw["same_by"] = PEER
        raw["design_set_at"] = 20
        raw["design_by"] = self.window.own_id()
        raw["crossing"]["effect_length"] = "short"
        self.window.controller.apply_settings(self.store.save(raw))
        incoming = settings_sync.message_data(
            True, 11, values={"effect_length": "long"}, by=HIGH,
            design_state={"set_at": 100, "by": HIGH, "values": {"effect_length": "long"}},
        )

        self.window.apply_same(incoming, peer=HIGH)

        cfg = self.window.controller.cfg
        self.assertEqual(cfg.crossing["effect_length"], "long")
        self.assertGreater(cfg.design_set_at, 100)
        self.assertEqual(cfg.design_by, self.window.own_id())
        self.assertEqual(self.sent[-1]["design_sync"]["set_at"], cfg.design_set_at)
        self.assertNotEqual(self.sent[-1]["design_sync"]["by"], HIGH)

    def test_same_off_from_another_machine_resumes_the_cached_followed_design(self):
        self.pair_sources(PEER, HIGH)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw["same_on_both"] = True
        raw["same_groups"] = list(settings_sync.ALL_GROUPS)
        raw["same_set_at"] = 10
        raw["same_by"] = HIGH
        raw["design_follow_peer"] = PEER
        raw["crossing"]["effect_length"] = "short"
        self.window.controller.apply_settings(self.store.save(raw))
        self.window._design_states[PEER] = {
            "set_at": 100, "by": PEER, "values": {"effect_length": "long"},
        }
        incoming = settings_sync.message_data(False, 11, by=HIGH)

        self.window.apply_same(incoming, peer=HIGH)

        self.assertEqual(self.window.controller.cfg.crossing["effect_length"], "long")
        self.assertEqual(self.sent[-1]["design_sync"]["values"]["effect_length"], "long")
        self.assertEqual((self.sent[-1]["design_sync"]["set_at"], self.sent[-1]["design_sync"]["by"]),
                         (self.window.controller.cfg.design_set_at, self.window.own_id()))

    def test_same_on_all_machines_takes_precedence_without_clearing_following(self):
        self.pair_sources(PEER)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw.update(design_follow_peer=PEER, same_on_both=True, same_set_at=100,
                   same_groups=list(settings_sync.ALL_GROUPS))
        self.window.controller.apply_settings(self.store.save(raw))
        received = settings_sync.message_data(
            True, 101, settings_sync.mac_values(raw), by=PEER,
            design_state={"set_at": 202, "by": PEER, "values": {"effect_length": "long"}},
        )

        self.window.apply_same(received, peer=PEER)

        self.assertEqual(self.window.controller.cfg.crossing["effect_length"], "normal")
        self.assertEqual(self.window.controller.cfg.design_follow_peer, PEER)

    def follow(self, **crossing):
        self.pair_sources(PEER)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw["design_follow_peer"] = PEER
        # A preset set alone would lose to the held slider value when the store resolves them.
        raw["crossing"].update(settings_sync._with_continuous(crossing))
        self.window.controller.apply_settings(self.store.save(raw))
        self.window._load_shared(raw)
        self.window._refresh_design_follow()

    def followed_controls(self):
        w = self.window
        return [w.length_select, w.size_select, w.landing_box, *w.glow_style_select.rows,
                *w.switch_style_select.rows, *w.glow_colour_select.rows]

    def test_following_locks_the_followed_design_controls_under_a_banner(self):
        self.follow()

        self.assertFalse(any(control.enabled for control in self.followed_controls()))
        self.assertTrue(self.window.glow_box.enabled)
        self.assertFalse(self.window.follow_banner.isHidden())
        self.assertEqual(self.window.follow_banner_label.text, "Following Paired test PC's design")

    def test_a_click_on_a_locked_control_changes_nothing_and_keeps_following(self):
        self.follow(effect_length="long")
        self.sent.clear()
        slider = self.window.length_select.slider
        slider.keyDown_(mock.Mock(keyCode=lambda: 124))
        self.window.landing_box.view.press()

        self.assertEqual(self.window.length_select.value, "long")
        self.assertEqual(self.window.controller.cfg.crossing["effect_length"], "long")
        self.assertEqual(self.window.controller.cfg.design_follow_peer, PEER)
        self.assertEqual(self.sent, [])

    def test_stop_following_unlocks_the_controls_and_keeps_the_shown_design(self):
        self.follow(effect_length="long", glow_colour="ocean")

        self.window.stopFollowing_(None)

        cfg = self.window.controller.cfg
        self.assertEqual(cfg.design_follow_peer, "")
        self.assertTrue(all(control.enabled for control in self.followed_controls()))
        self.assertTrue(self.window.follow_banner.isHidden())
        self.assertEqual((self.window.length_select.value, cfg.crossing["effect_length"]), ("long", "long"))
        self.assertEqual((self.window.glow_colour_select.value, cfg.crossing["glow_colour"]), ("ocean", "ocean"))
        self.assertEqual(self.sent[-1]["design_sync"]["values"]["effect_length"], "long")

    def test_removing_the_followed_machine_resets_the_local_selection(self):
        entry = {**settings_store.PEER_DEFAULTS, "id": PEER, "token": "peer-token", "name": "Peer",
                 "platform": "windows", "port": protocol.DEFAULT_PORT}
        self.store.add_peer(entry)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw["design_follow_peer"] = PEER
        self.window.controller.apply_settings(self.store.save(raw))

        self.store.remove_peer("peer-token")
        self.window.peers_changed()

        self.assertEqual(self.window.controller.cfg.design_follow_peer, "")

    def test_a_connected_peer_that_has_not_sent_a_design_state_is_disabled(self):
        entry = {**settings_store.PEER_DEFAULTS, "id": PEER, "token": "peer-token", "name": "Old PC",
                 "platform": "windows", "port": protocol.DEFAULT_PORT}
        self.store.add_peer(entry)
        self.window._design_peer_caps = lambda: {PEER: frozenset({"settings", "design_sync"})}

        self.window._refresh_design_follow()

        cell = next(cell for value, cell in self.window.follow_select.items if value == PEER)
        self.assertFalse(cell.isEnabled())
        self.assertEqual(next(title for value, title in self.window.follow_select.choices if value == PEER), "Old PC")
        self.assertEqual(cell.toolTip(), "Design not available yet: connect to this machine with Beamer 1.5.0 beta.6 or later.")
        self.assertEqual(self.window.follow_note.text, cell.toolTip())

    def test_the_pcs_settings_arrive_and_this_macs_own_stay(self):
        local_ways = {peer["id"]: (copy.deepcopy(peer.get("ways")), peer.get("jump_key"))
                      for peer in self.store.current()["peers"]}
        self.window.apply_same(pc_message(True, 100))
        cfg = self.window.controller.cfg
        self.assertTrue(cfg.same_on_both)
        self.assertEqual(cfg.crossing["methods"], ["edge", "notch"])
        self.assertEqual((cfg.crossing["glow_style"], cfg.crossing["effect_length"]), ("beam", "long"))
        self.assertEqual((cfg.trigger_key, cfg.trigger_style, cfg.double_tap_ms), ("cmd_r", "hold", 400))
        self.assertEqual(self.store.load().crossing["glow_colour"], "ocean")
        self.assertEqual(self.window.glow_style_select.value, "beam")
        self.assertEqual(self.window.resistance_ruler.value, 60)
        self.assertTrue(self.window.same_checks["crossing"].state())
        self.assertEqual({peer["id"]: (peer.get("ways"), peer.get("jump_key"))
                          for peer in self.store.current()["peers"]}, local_ways)

    def test_arriving_settings_leave_a_half_typed_address_and_the_pairing_card(self):
        self.window.host_field.setStringValue_("192.0.2.99")
        self.window.panel._say("That code was not accepted.", "fault")
        self.window.apply_same(pc_message(True, 100))
        self.assertEqual(self.window.host_field.stringValue(), "192.0.2.99")
        self.assertEqual(self.window.panel.pair_status.text, "That code was not accepted.")

    def test_the_hold_switch_reaches_the_controller_before_the_debounce(self):
        self.window.hold_box.value = True
        self.window.controller.full_screen_app = "Game"
        self.window.hold_box._toggle()
        self.assertIs(self.window.controller.cfg.crossing["hold_full_screen"], False)
        self.assertIsNone(self.window.controller.full_screen_app)
        self.window.hold_box._toggle()
        self.assertEqual(self.window.controller.full_screen_app, "Game")

    def test_settings_arriving_from_the_pc_leave_the_hold_switch_as_chosen(self):
        self.window.hold_box.value = True
        self.window.hold_box._toggle()
        self.window.apply_same(pc_message(True, 100))
        self.assertFalse(self.window.hold_box.value)
        self.window._apply_settings()
        self.assertFalse(self.store.load().crossing["hold_full_screen"])

    def test_an_older_message_changes_nothing(self):
        self.window.apply_same(pc_message(True, 100))
        self.window.apply_same(pc_message(True, 90, glow_style="glow"))
        self.assertEqual(self.window.controller.cfg.crossing["glow_style"], "beam")

    def test_turning_it_on_here_sends_this_macs_values(self):
        self.window._set_same(True)
        self.assertEqual(len(self.sent), 1)
        self.assertTrue(self.sent[0]["on"])
        self.assertIs(self.sent[0]["crossing"]["shortcut"], True)

    def test_a_change_here_while_on_is_sent_and_one_to_this_macs_own_is_not(self):
        self.window._set_same(True)
        self.sent.clear()
        self.window.notch_after_select.value = 3000
        self.window._apply_settings()
        self.assertEqual(self.sent, [])
        self.window.length_select.value = "short"
        self.window._apply_settings()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["design"]["effect_length"], "short")

    def test_a_shared_change_here_stamps_only_that_value_and_sends_the_stamps(self):
        self.window._set_same(True)
        own = self.store.current()["machine_id"]
        before = dict(self.window.controller.cfg.same_stamps)
        self.assertEqual(set(before), set(settings_sync.STAMPED_KEYS), "the seed is stored when Same changes")
        self.sent.clear()
        self.window.length_select.value = "short"
        self.window._apply_settings()
        stamps = self.store.load().same_stamps
        changed = {key for key in stamps if stamps[key] != before[key]}
        self.assertEqual(changed, {"effect_length", "effect_pace"})
        self.assertEqual(stamps["effect_length"][1], own)
        self.assertGreater(stamps["effect_length"][0], max(stamp for stamp, _by in before.values()))
        sent = self.sent[-1]
        self.assertEqual(sent["stamps"]["effect_length"], stamps["effect_length"])
        self.assertEqual(sent["stamps"]["glow_colour"], before["glow_colour"])
        self.assertGreaterEqual(sent["set_at"], stamps["effect_length"][0])

    def test_a_change_here_while_off_is_stamped_and_kept_over_an_older_group_value(self):
        self.window.length_select.value = "short"
        self.window._apply_settings()
        stamp = self.window.controller.cfg.same_stamps["effect_length"]
        self.assertEqual(stamp[1], self.store.current()["machine_id"])
        self.sent.clear()
        self.window.apply_same(pc_message(True, 100))
        cfg = self.window.controller.cfg
        self.assertTrue(cfg.same_on_both)
        self.assertEqual(cfg.crossing["effect_length"], "short")
        self.assertEqual(cfg.crossing["glow_style"], "beam")
        self.assertEqual(cfg.same_stamps["effect_length"], stamp)
        self.assertEqual(cfg.same_stamps["glow_style"], [100, ""])
        self.assertEqual(self.sent[-1]["design"]["effect_length"], "short", "the newer value is answered")

    def test_a_design_change_while_off_announces_its_local_design_state(self):
        self.window.length_select.value = "short"
        self.window._apply_settings()
        self.assertEqual(len(self.sent), 1)
        self.assertFalse(self.sent[0]["on"])
        self.assertEqual(self.sent[0]["design_sync"]["values"]["effect_length"], "short")

    def test_changing_size_redraws_the_design_tiles(self):
        from unittest import mock

        stills = [mock.Mock() for _ in self.window.effect_stills]
        self.window.effect_stills = stills
        with mock.patch.object(self.window.previews, "repaint") as repaint:
            self.window.size_select.value = "small"
            self.window._reflect()
        self.assertTrue(stills)
        for still in stills:
            still.setNeedsDisplay_.assert_called_once_with(True)
        repaint.assert_called_once_with()

    def test_an_old_pc_keeps_the_picker_enabled_and_shows_the_compatibility_note(self):
        self.window._set_same(True)
        bring_up(self.window.controller, caps=("clipboard",))
        self.assertIs(self.window.controller.peer_settings, False)
        self.window._show_same()
        self.assertTrue(self.window.same_checks["crossing"].state())
        self.assertTrue(self.window.same_checks["crossing"].isEnabled())
        self.assertFalse(self.window.same_old_note.view.isHidden())

    def test_names_only_keeps_design_following_and_local_scope(self):
        self.pair_sources(PEER)
        local_resistance = self.window.controller.cfg.crossing["resistance_px"]
        local_drag_guard = self.window.controller.cfg.crossing["block_while_dragging"]
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw.update(same_on_both=True, same_groups=["names"], design_follow_peer=PEER)
        self.window.controller.apply_settings(self.store.save(raw))
        self.window._design_states[PEER] = {"set_at": 100, "by": PEER, "values": {"effect_length": "long"}}
        self.window._apply_followed_design(force=True)
        self.window._show_same()
        self.assertEqual(self.window.controller.cfg.crossing["effect_length"], "long")
        self.assertEqual(self.window.controller.cfg.crossing["resistance_px"], local_resistance)
        self.assertEqual(self.window.controller.cfg.crossing["block_while_dragging"], local_drag_guard)
        self.assertEqual(self.window.resistance_ruler.value, local_resistance)
        self.assertEqual(self.window.scope_labels["design"].text, pages.SCOPE["design"])
        self.assertIn("Names", self.window.same_note.text)

    def test_revert_restores_this_machines_name_and_shares_it(self):
        self.pair_sources(PEER)
        own = self.window.own_id()
        original = self.store.current()["name"]
        data = settings_sync.message_data(True, 100, by=PEER, groups=["names"],
            names={own: {"label": "Remote name", "set_at": 100, "by": PEER}})
        self.window.apply_same(data, peer=PEER)
        self.assertEqual(self.store.current()["name"], "Remote name")
        self.window.revertSame_(None)
        self.assertEqual(self.store.current()["name"], original)
        self.assertTrue(self.window.controller.cfg.same_on_both)
        self.assertEqual(self.sent[-1]["names"][own]["label"], original)

    def test_stale_names_policy_cannot_rename_after_names_was_deselected(self):
        self.pair_sources(PEER)
        self.window.apply_same(settings_sync.message_data(True, 200, by=PEER, groups=["crossing"]), peer=PEER)
        self.window.apply_same(settings_sync.message_data(True, 100, by=PEER, groups=["names"],
            names={PEER: {"label": "Delayed", "set_at": 300, "by": PEER}}), peer=PEER)
        entry = next(item for item in self.store.current()["peers"] if item["id"] == PEER)
        self.assertNotEqual(entry.get("label"), "Delayed")

    def test_a_remote_selection_that_drops_design_resumes_the_cached_leader(self):
        self.pair_sources(PEER, HIGH)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw.update(same_on_both=True, same_groups=["design"], same_set_at=10,
                   design_follow_peer=PEER)
        self.window.controller.apply_settings(self.store.save(raw))
        self.window._design_states[PEER] = {"set_at": 100, "by": PEER, "values": {"effect_length": "long"}}
        self.window._followed_version = (PEER, 100, PEER)
        self.window.apply_same(settings_sync.message_data(True, 11, by=HIGH, groups=["names"]), peer=HIGH)
        self.assertEqual(self.window.controller.cfg.crossing["effect_length"], "long")

    def test_selection_only_revert_keeps_a_later_local_design_edit(self):
        self.pair_sources(PEER)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw.update(same_on_both=True, same_groups=["crossing"], same_set_at=10)
        self.window.controller.apply_settings(self.store.save(raw))
        self.window.apply_same(settings_sync.message_data(True, 11, by=PEER, groups=["design"]), peer=PEER)
        raw = settings_store.config_to_raw(self.window.controller.cfg)
        raw["crossing"]["glow_colour"] = "ocean"
        self.window.controller.apply_settings(self.store.save(raw))
        self.window.revertSame_(None)
        self.assertEqual(self.window.controller.cfg.same_groups, ["crossing"])
        self.assertEqual(self.window.controller.cfg.crossing["glow_colour"], "ocean")

    def test_a_peers_same_state_is_forwarded_with_this_macs_own_design_state(self):
        self.pair_sources(PEER)
        data = pc_message(True, 100, by=PEER)
        self.window.apply_same(data, peer=PEER)
        self.assertEqual(len(self.sent), 1)
        self.assertEqual({key: value for key, value in self.sent[0].items() if key != "design_sync"}, data)
        self.assertNotEqual(self.sent[0]["design_sync"]["by"], PEER)
        self.assertEqual(self.sources, [PEER])

    def test_who_made_the_change_is_kept_with_it(self):
        self.pair_sources(PEER)
        self.window.apply_same(pc_message(True, 100, by=PEER), peer=PEER)
        self.assertEqual(self.window.controller.cfg.same_by, PEER)
        self.assertEqual(self.store.load().same_by, PEER)

    def test_a_change_made_here_says_it_was_made_by_this_mac(self):
        self.window._set_same(True)
        own = self.store.current()["machine_id"]
        self.assertEqual(self.sent[0]["by"], own)
        self.assertEqual(self.window.controller.cfg.same_by, own)

    def test_the_same_second_from_a_larger_id_is_taken_and_from_a_smaller_is_not(self):
        self.pair_sources(PEER, LOW, HIGH)
        self.window.apply_same(pc_message(True, 100, by=PEER, glow_style="beam"), peer=PEER)
        self.window.apply_same(pc_message(True, 100, by=LOW, glow_style="glow"), peer=LOW)
        self.assertEqual(self.window.controller.cfg.crossing["glow_style"], "beam")
        self.window.apply_same(pc_message(True, 100, by=HIGH, glow_style="glow"), peer=HIGH)
        self.assertEqual(self.window.controller.cfg.crossing["glow_style"], "glow")
        self.assertEqual(self.window.controller.cfg.same_by, HIGH)

    def test_the_copy_that_arrives_over_the_second_link_changes_nothing_and_is_not_sent_on(self):
        self.pair_sources(PEER)
        data = pc_message(True, 100, by=PEER)
        self.window.apply_same(data, peer=PEER)
        self.window.apply_same(data, peer=PEER)
        self.assertEqual(len(self.sent), 1)

    def test_an_unpaired_source_cannot_change_or_forward_settings(self):
        before = settings_store.config_to_raw(self.window.controller.cfg)
        self.window.apply_same(pc_message(True, 100, by=HIGH), peer=HIGH)
        self.assertEqual(settings_store.config_to_raw(self.window.controller.cfg), before)
        self.assertEqual(self.sent, [])
        self.assertNotIn(HIGH, self.window._design_states)


if __name__ == "__main__":
    unittest.main()
