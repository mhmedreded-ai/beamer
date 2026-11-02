"""Input regressions found by the independent 1.5.10 candidate review."""

import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from core.tests import test_linux_accents as accents
from core.tests import test_windows_accent_releases as windows

capture_win = windows.capture_win


class BackendPolicyTests(unittest.TestCase):
    def test_saved_mouse_only_applies_only_to_a_supported_backend(self):
        for supported in (False, True):
            with self.subTest(supported=supported):
                config = SimpleNamespace(mouse_only=True)
                link = accents.sender.LinkSender(None, lambda: {},
                                                mouse_only_supported=supported,
                                                links=lambda _: SimpleNamespace(live=lambda peer: True))
                link.refresh = lambda: None
                link.update_config(config)
                link._owner = accents.owner.Owner(bytes(range(16)), lambda: 0)
                link._owner.on = bytes(range(16, 32))
                self.assertTrue(config.mouse_only, "The saved X11 preference must survive a Wayland session")
                self.assertEqual(link._ignore_gate.mouse_only, supported)
                self.assertEqual(link.on_key("enter", True, 28), not supported)
                self.assertEqual(link.on_key("enter", False, 28), not supported)
                messages = accents.AccentTests.messages(link)
                self.assertEqual([(m["type"], m.get("key")) for m in messages],
                                 [] if supported else [("keydown", "enter"), ("keyup", "enter")])


class RetainedModifierTests(unittest.TestCase):
    def test_linux_windows_chords_preserve_remote_modifiers_in_either_order(self):
        for name, vk in (("alt", 0xa4), ("cmd", 0xa2), ("shift", 0xa0)):
            for modifier_first in (False, True):
                with self.subTest(modifier=name, modifier_first=modifier_first), patch.object(sys, "platform", "win32"):
                    link = accents.AccentTests.link_sender()
                    link._peers[link._owner.on] = {"platform": "linux"}
                    keys = [(name, vk), ("ctrl", 0x5b)]
                    if not modifier_first:
                        keys.reverse()
                    for key, code in keys:
                        link.on_key(key, True, code)
                    link.on_key("s", True, 0x53)
                    link.on_key("s", False, 0x53)
                    for key, code in reversed(keys):
                        link.on_key(key, False, code)
                    messages = accents.AccentTests.messages(link)
                    self.assertFalse(any(m.get("key") == "spotlight" for m in messages))
                    self.assertEqual([(m["type"], m.get("key")) for m in messages if m.get("key") == "s"],
                                     [("keydown", "s"), ("keyup", "s")])
                    self.assertEqual(link._owner.pressed_keys, frozenset())
                    self.assertEqual(link._intent_windows_up, set())
                    self.assertEqual(link._intent_keys_up, set())

    def test_buffered_windows_key_is_cancelled_on_return_home(self):
        with patch.object(sys, "platform", "win32"):
            link = accents.AccentTests.link_sender()
            peer = link._owner.on
            link._peers[peer] = {"platform": "macos"}
            self.assertTrue(link.on_key("cmd", True, 0x5b))
            self.assertTrue(link.set_redirecting(False))
            link.on_key("cmd", False, 0x5b)
            accents.AccentTests.messages(link)
            link._owner.on = peer
            link.on_key("s", True, 0x53)
            link.on_key("s", False, 0x53)
            self.assertEqual([(m["type"], m.get("key")) for m in accents.AccentTests.messages(link)],
                             [("keydown", "s"), ("keyup", "s")])
            self.assertIsNone(link._pending_windows_key)
            self.assertEqual(link._intent_windows_up, set())
            link.on_key("cmd", True, 0x5b)
            link.on_key("cmd", False, 0x5b)
            self.assertEqual([m["type"] for m in accents.AccentTests.messages(link)],
                             ["keydown", "keyup"])

    def test_cancelled_windows_release_cleans_intent_even_with_mouse_only_on(self):
        with patch.object(sys, "platform", "win32"):
            link = accents.AccentTests.link_sender()
            peer = link._owner.on
            link._peers[peer] = {"platform": "macos"}
            link.on_key("cmd", True, 0x5b)
            link.set_redirecting(False)
            accents.AccentTests.messages(link)
            link._owner.on = peer
            link._ignore_gate.configure([], mouse_only=True)
            link.on_key("cmd", False, 0x5b)
            self.assertIsNone(link._pending_windows_key)
            self.assertEqual(link._intent_windows_up, set())
            link._ignore_gate.configure([], mouse_only=False)
            link.on_key("s", True, 0x53)
            link.on_key("s", False, 0x53)
            self.assertEqual([(m["type"], m.get("key")) for m in accents.AccentTests.messages(link)],
                             [("keydown", "s"), ("keyup", "s")])

    def test_home_windows_hold_never_activates_remote_intent_after_crossing(self):
        with patch.object(sys, "platform", "win32"):
            link = accents.AccentTests.link_sender()
            peer = link._owner.on
            link._peers[peer] = {"platform": "macos"}
            link._owner.on = None
            link._ignore_gate.configure([], mouse_only=True)
            self.assertFalse(link.on_key("cmd", True, 0x5b))
            link._owner.on = peer
            link._ignore_gate.configure([], mouse_only=False)
            link.on_key("s", True, 0x53)
            link.on_key("s", False, 0x53)
            self.assertFalse(link.on_key("cmd", False, 0x5b))
            self.assertEqual([(m["type"], m.get("key")) for m in accents.AccentTests.messages(link)],
                             [("keydown", "s"), ("keyup", "s")])
            self.assertEqual(link._intent_windows_up, set())
            self.assertEqual(link._intent_keys_up, set())

    def test_home_ctrl_hold_does_not_cancel_remote_intent_after_crossing(self):
        with patch.object(sys, "platform", "win32"):
            link = accents.AccentTests.link_sender()
            peer = link._owner.on
            link._peers[peer] = {"platform": "macos"}
            link._owner.on = None
            self.assertFalse(link.on_key("ctrl", True, 0xa2))
            link._owner.on = peer
            link.on_key("cmd", True, 0x5b)
            link.on_key("s", True, 0x53)
            link.on_key("s", False, 0x53)
            link.on_key("cmd", False, 0x5b)
            self.assertFalse(link.on_key("ctrl", False, 0xa2))
            self.assertEqual([(m["type"], m.get("key")) for m in accents.AccentTests.messages(link)],
                             [("keydown", "spotlight"), ("keyup", "spotlight")])

    def test_retained_windows_key_never_activates_remote_intent(self):
        for retained in (0x5b, 0x5c):
            with self.subTest(retained=retained), patch.object(sys, "platform", "win32"):
                link = accents.AccentTests.link_sender()
                link._peers[link._owner.on] = {"platform": "macos"}
                link._ignore_gate.configure([], mouse_only=True)
                self.assertFalse(link.on_key("cmd", True, retained))
                link._ignore_gate.configure([], mouse_only=False)
                self.assertTrue(link.on_key("s", True, 0x53))
                self.assertTrue(link.on_key("s", False, 0x53))
                self.assertFalse(link.on_key("cmd", False, retained))
                self.assertEqual([(m["type"], m.get("key")) for m in accents.AccentTests.messages(link)],
                                 [("keydown", "s"), ("keyup", "s")])
                self.assertEqual(link._intent_windows_up, set())
                self.assertEqual(link._intent_keys_up, set())
                link.on_key("cmd", True, retained)
                link.on_key("cmd", False, retained)
                self.assertEqual([m["type"] for m in accents.AccentTests.messages(link)],
                                 ["keydown", "keyup"])

    def test_retained_ctrl_does_not_cancel_a_remote_windows_shortcut(self):
        with patch.object(sys, "platform", "win32"):
            link = accents.AccentTests.link_sender()
            link._peers[link._owner.on] = {"platform": "macos"}
            link._ignore_gate.configure([], mouse_only=True)
            self.assertFalse(link.on_key("ctrl", True, 0xa2))
            link._ignore_gate.configure([], mouse_only=False)
            link.on_key("cmd", True, 0x5b)
            link.on_key("s", True, 0x53)
            link.on_key("s", False, 0x53)
            link.on_key("cmd", False, 0x5b)
            self.assertFalse(link.on_key("ctrl", False, 0xa2))
            self.assertEqual([(m["type"], m.get("key")) for m in accents.AccentTests.messages(link)],
                             [("keydown", "spotlight"), ("keyup", "spotlight")])
            self.assertEqual(link._intent_windows_up, set())
            self.assertEqual(link._intent_keys_up, set())


class AccentSpaceTests(unittest.TestCase):
    def test_altgr_dead_accent_space_is_text_with_balanced_modifiers(self):
        hook = windows.WindowsReleaseTests()
        hook.setUp()
        self.addCleanup(hook.doCleanups)
        hook.translate = lambda vk, scan, modifiers: capture_win.DeadKey("~")
        hook.feed(0xa5, True)
        hook.feed(0xde, True)
        hook.feed(0xde, False)
        hook.feed(0x20, True)
        hook.feed(0x20, False)
        hook.feed(0xa5, False)
        messages = hook.messages()
        self.assertEqual([(m["type"], m.get("key"), m.get("text")) for m in messages],
                         [("keydown", "alt_r", None), ("keyup", "alt_r", None),
                          ("text", None, "~"), ("keydown", "alt_r", None),
                          ("keyup", "alt_r", None)])
        self.assertEqual(hook.sender._keys_down, {})
        self.assertEqual(hook.hooks._text_keyups_swallowed, set())


if __name__ == "__main__":
    unittest.main()
