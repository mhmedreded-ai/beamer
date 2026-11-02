"""The Mac's two halves with a real second end on loopback: input, keys and the clipboard going to a
version 6 PC, and a PC's input arriving on the Mac. test_mac_links.py has the controller against
fake links; core/tests/test_link.py has the link against the responder; this is the two together,
through the controller's event tap and the Mac's responder."""

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bridge_fakes import FakeQuartz
from core import protocol, return_edge
from two_machines import PC_TEXT, Duo, wait_for

A_KEYCODE, COMMAND_KEYCODE = 0x00, 0x37


def setUpModule():
    # These push home the moment input lands; the arrival grace is test_arrival_grace's subject,
    # not theirs, as in test_way_home and core's loopback tests.
    grace = mock.patch.object(return_edge, "ARRIVAL_GRACE_SECONDS", 0.0)
    grace.start()
    unittest.addModuleCleanup(grace.stop)


def key_event(down, keycode, character=None):
    event = {FakeQuartz.kCGKeyboardEventKeycode: keycode}
    if character is not None:
        event["unicode"] = character
    return (FakeQuartz.kCGEventKeyDown if down else FakeQuartz.kCGEventKeyUp), event


def command_event(down):
    return FakeQuartz.kCGEventFlagsChanged, {
        FakeQuartz.kCGKeyboardEventKeycode: COMMAND_KEYCODE,
        "flags": FakeQuartz.kCGEventFlagMaskCommand if down else 0,
    }


class MacDrivesPcTests(unittest.TestCase):
    def setUp(self):
        self.duo = Duo(crossing={"edge": "right", "resistance_px": 40}).listen()
        self.addCleanup(self.duo.close)
        self.duo.link_mac_to_pc()
        self.pc = self.duo.pc_responder

    def tap(self, event):
        event_type, fields = event
        return self.duo.mac._event_tap_callback(None, event_type, fields, None)

    def cross(self):
        self.assertTrue(self.duo.mac_push(1727, 558, 30), "the push never crossed")
        self.assertTrue(wait_for(lambda: self.pc.responder.owner == self.duo.mac_id))

    def test_a_key_goes_to_the_pc_swallowed_here_with_the_command_key_as_control(self):
        self.cross()
        self.assertIsNone(self.tap(command_event(True)), "the Mac let the command key through")
        self.assertIsNone(self.tap(key_event(True, A_KEYCODE, "a")))
        self.assertIsNone(self.tap(key_event(False, A_KEYCODE, "a")))
        self.assertIsNone(self.tap(command_event(False)))
        keys = [("key", "ctrl", True), ("key", "a", True), ("key", "a", False), ("key", "ctrl", False)]
        self.assertTrue(wait_for(lambda: [call for call in self.pc.injected() if call[0] == "key"] == keys), self.pc.injected())

    def test_caps_lock_from_the_redirecting_mac_reaches_the_windows_responder(self):
        self.cross()
        event = {
            FakeQuartz.kCGKeyboardEventKeycode: 0x39,
            "flags": 0,
        }
        self.assertIsNone(self.tap((FakeQuartz.kCGEventFlagsChanged, event)))
        caps = [("key", "caps_lock", True), ("key", "caps_lock", False)]
        self.assertTrue(wait_for(lambda: [call for call in self.pc.injected() if call[0] == "key"] == caps), self.pc.injected())

    def test_a_key_held_when_the_input_comes_home_is_released_on_the_pc_first(self):
        self.cross()
        self.tap(key_event(True, A_KEYCODE, "a"))
        self.assertTrue(wait_for(lambda: ("key", "a", True) in self.pc.injected()))
        self.pc.desktop.cursor = (0, 540)
        self.assertTrue(self.duo.mac_lean(-30))
        self.assertTrue(wait_for(lambda: ("key", "a", False) in self.pc.injected()), "the key stayed down on the PC")
        self.assertTrue(wait_for(lambda: self.pc.responder.owner is None))

    def test_the_macs_clipboard_goes_with_the_take_and_the_pcs_comes_back_once_after_the_let_go(self):
        self.cross()
        self.assertTrue(wait_for(lambda: ("on the Mac", None) in self.pc.clipboard.set_calls), "the Mac's clipboard never reached the PC")
        self.pc.clipboard.copy("copied on the PC")
        self.assertTrue(self.duo.mac.set_redirecting(False))
        self.assertTrue(wait_for(lambda: self.duo.clipboard.set_calls == [("copied on the PC", None)]), self.duo.clipboard.set_calls)
        self.assertEqual(self.duo.mac.owner.on, None)

    def test_losing_the_pcs_link_brings_the_pointer_home_and_says_so(self):
        self.cross()
        self.pc.stop()
        self.assertTrue(wait_for(lambda: not self.duo.mac.redirecting), "input stayed on a PC that is gone")
        self.assertTrue(wait_for(lambda: any("Lost the link" in text for text in self.duo.alerts)), self.duo.alerts)
        self.assertEqual(FakeQuartz.associate_calls[-1], True)

    def test_a_pc_that_stops_allowing_this_mac_to_drive_it_leaves_the_keyboard_here(self):
        self.pc.settings.peer(self.duo.mac_text)["allow_drive"] = False
        self.pc.responder.peers_changed()
        self.assertTrue(wait_for(lambda: self.duo.mac._accepts.get(PC_TEXT) is False), "the Mac was never told")
        self.assertFalse(self.duo.mac.set_redirecting(True))
        self.assertFalse(self.duo.mac.redirecting)
        event_type, fields = key_event(True, A_KEYCODE, "a")
        self.assertIs(self.duo.mac._event_tap_callback(None, event_type, fields, None), fields)


class PcDrivesMacTests(unittest.TestCase):
    def setUp(self):
        self.duo = Duo(crossing={"edge": "right", "resistance_px": 40}).listen()
        self.addCleanup(self.duo.close)
        self.duo.link_pc_to_mac()
        self.duo.pc_drives("right", 0.5)

    def test_the_pcs_keys_and_pointer_are_injected_on_the_mac_in_order(self):
        pc = self.duo.pc
        pc.acked(pc.key(protocol.MSG_KEYDOWN, "a"))
        pc.acked(pc.move(-4, 5))
        pc.acked(pc.key(protocol.MSG_KEYUP, "a"))
        calls = [call for call in self.duo.mac_injector.seen() if call[0] in ("key", "move")]
        self.assertEqual(calls, [("key", "a", True), ("move", -4, 5), ("key", "a", False)])

    def test_caps_lock_from_the_windows_peer_reaches_the_mac_injector(self):
        pc = self.duo.pc
        for kind in (protocol.MSG_KEYDOWN, protocol.MSG_KEYUP):
            self.assertIsNotNone(pc.acked(pc.key(kind, "caps_lock")))
        caps = [("key", "caps_lock", True), ("key", "caps_lock", False)]
        self.assertTrue(wait_for(lambda: [call for call in self.duo.mac_injector.seen() if call[0] == "key"] == caps))

    def test_caps_lock_shortcut_hands_the_pcs_input_home_while_the_mac_is_driven(self):
        self.duo.link_mac_to_pc()
        self.duo.mac.cfg.trigger_key = "caps_lock"
        self.duo.mac.trigger_code = 0x39
        self.duo.mac.cfg.crossing["methods"] = ("edge", "shortcut")
        event = {FakeQuartz.kCGKeyboardEventKeycode: 0x39, "flags": 0}
        tap = self.duo.mac._event_tap_callback

        self.assertIs(tap(None, FakeQuartz.kCGEventFlagsChanged, event, None), event)
        self.assertIsNone(tap(None, FakeQuartz.kCGEventFlagsChanged, event, None))
        self.assertTrue(wait_for(lambda: not self.duo.mac.receiving), "the PC kept driving after the Caps Lock shortcut")

    def test_the_macs_own_events_pass_through_untouched_while_the_pc_drives(self):
        event = {"location": (400.0, 300.0), FakeQuartz.kCGMouseEventDeltaX: 5, FakeQuartz.kCGMouseEventDeltaY: 0}
        self.assertIs(self.duo.mac._event_tap_callback(None, FakeQuartz.kCGEventMouseMoved, event, None), event)
        key_down = {FakeQuartz.kCGKeyboardEventKeycode: A_KEYCODE, "unicode": "a"}
        self.assertIs(self.duo.mac._event_tap_callback(None, FakeQuartz.kCGEventKeyDown, key_down, None), key_down)
        self.assertFalse(self.duo.mac.redirecting)

    def test_a_key_the_pc_holds_when_it_lets_go_is_released_on_the_mac(self):
        pc = self.duo.pc
        pc.acked(pc.key(protocol.MSG_KEYDOWN, "a"))
        pc.let_go()
        self.assertTrue(wait_for(lambda: not self.duo.mac.receiving))
        self.assertIn(("key", "a", False), self.duo.mac_injector.seen())

    def test_losing_the_pcs_link_hands_the_mac_back_and_releases_what_it_held(self):
        pc = self.duo.pc
        pc.acked(pc.key(protocol.MSG_KEYDOWN, "a"))
        pc.sock.shutdown(2)
        self.assertTrue(wait_for(lambda: not self.duo.mac.receiving), "the Mac stayed driven by a PC that is gone")
        self.assertIn(("key", "a", False), self.duo.mac_injector.seen())
        self.assertIsNone(self.duo.mac_responder.owner)


if __name__ == "__main__":
    unittest.main()
