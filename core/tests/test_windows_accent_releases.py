"""Exercise the real Windows hook and sender without native hooks or GUI imports."""

import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "win_app"))

import capture_win
from core.tests import test_linux_accents as accents


class HookData(ctypes.Structure):
    _fields_ = [("vkCode", ctypes.c_ulong), ("scanCode", ctypes.c_ulong),
                ("flags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.c_size_t)]


class HookApi:
    def CallNextHookEx(self, *args):
        return 99


class WindowsReleaseTests(unittest.TestCase):
    def setUp(self):
        self.sender = accents.AccentTests.link_sender()
        def route(name, down, vk, us):
            if isinstance(name, capture_win.TextInput):
                return self.sender.on_text(name.text, vk, down)
            if isinstance(name, capture_win.DeadKey):
                return self.sender.on_dead_key(vk, down)
            return self.sender.on_key(name, down, vk, us)
        self.hooks = capture_win.Hooks(route, lambda *args: False, lambda *args: None)
        self.translate = lambda vk, scan, state: "€" if state.get(0x12) else "e"
        for replacement in (patch.object(capture_win, "KBDLLHOOKSTRUCT", HookData, create=True),
                            patch.object(capture_win, "user32", HookApi()),
                            patch.object(capture_win, "_to_unicode", lambda *args: self.translate(*args))):
            replacement.start()
            self.addCleanup(replacement.stop)

    def feed(self, vk, down, scan=0, flags=0):
        data = HookData(vk, scan, flags, 0, 0)
        return self.hooks._keyboard_proc(capture_win.HC_ACTION,
                                        capture_win.WM_KEYDOWN if down else capture_win.WM_KEYUP,
                                        ctypes.byref(data))

    def messages(self):
        return accents.AccentTests.messages(self.sender)

    def test_ordinary_repeat_cannot_turn_into_altgr_text_and_lose_its_release(self):
        self.feed(0x45, True, 0x12)
        self.feed(0xa5, True)
        self.feed(0x45, True, 0x12)
        self.feed(0x45, False, 0x12)
        self.feed(0xa5, False)
        messages = self.messages()
        self.assertFalse(any(m["type"] == "text" for m in messages))
        self.assertEqual([(m["type"], m.get("key")) for m in messages if m.get("key") == "e"],
                         [("keydown", "e"), ("keydown", "e"), ("keyup", "e")])
        self.assertEqual(self.sender._keys_down, {})

    def test_release_uses_original_press_even_when_new_layout_is_dead_or_unavailable(self):
        for changed in (capture_win.DeadKey("´"), None, "", "x"):
            with self.subTest(changed=changed):
                self.translate = lambda *args: "'"
                self.feed(0xde, True, 0x28)
                self.translate = lambda *args: changed
                self.feed(0xde, False, 0x28)
                self.assertEqual([(m["type"], m.get("key")) for m in self.messages()],
                                 [("keydown", "'"), ("keyup", "'")])
                self.assertEqual(self.sender._keys_down, {})

    def test_swallowed_dead_release_does_not_depend_on_new_layout(self):
        self.translate = lambda *args: capture_win.DeadKey("´")
        self.assertEqual(self.feed(0xde, True), 1)
        self.translate = lambda *args: None
        self.assertEqual(self.feed(0xde, False), 1)
        self.assertEqual(self.hooks._dead_keys_swallowed, set())

    def test_explicit_ctrl_right_alt_shortcut_is_never_text(self):
        for ctrl in (0xa2, 0xa3):
            with self.subTest(ctrl=ctrl):
                self.feed(ctrl, True, 0x1d)
                self.feed(0xa5, True)
                self.feed(0x45, True, 0x12)
                self.feed(0x45, False, 0x12)
                self.feed(0xa5, False)
                self.feed(ctrl, False, 0x1d)
                messages = self.messages()
                self.assertFalse(any(m["type"] == "text" for m in messages))
                self.assertEqual([(m["type"], m.get("key")) for m in messages if m.get("key") == "e"],
                                 [("keydown", "e"), ("keyup", "e")])
                self.assertEqual(self.sender._keys_down, {})

    def test_synthetic_altgr_ctrl_does_not_disable_altgr_text(self):
        self.feed(0xa2, True, 0x21d)
        self.feed(0xa5, True)
        self.feed(0x45, True, 0x12)
        self.feed(0x45, False, 0x12)
        self.feed(0xa5, False)
        self.feed(0xa2, False, 0x21d)
        self.assertEqual([m["text"] for m in self.messages() if m["type"] == "text"], ["€"])
        self.assertEqual(self.sender._keys_down, {})

    def test_altgr_synthetic_ctrl_release_cannot_release_a_physical_ctrl(self):
        self.feed(0xa2, True, 0x1d)
        self.feed(0xa2, True, 0x21d)
        self.feed(0xa5, True)
        self.feed(0xa5, False)
        self.feed(0xa2, False, 0x21d)
        self.assertIn(0xa2, self.sender._keys_down)
        self.feed(0xa2, False, 0x1d)
        messages = self.messages()
        self.assertEqual([(m["type"], m["key"]) for m in messages if m.get("key") == "ctrl"],
                         [("keydown", "ctrl"), ("keyup", "ctrl")])

    def test_ctrl_during_altgr_text_repeat_stays_a_balanced_shortcut(self):
        self.feed(0xa5, True)
        self.feed(0x45, True, 0x12)
        self.feed(0xa3, True, 0x1d)
        self.feed(0x45, True, 0x12)
        self.feed(0x45, False, 0x12)
        self.feed(0xa3, False, 0x1d)
        self.feed(0xa5, False)
        messages = self.messages()
        self.assertEqual([m["text"] for m in messages if m["type"] == "text"], ["€"])
        self.assertEqual([(m["type"], m.get("key")) for m in messages if m.get("key") == "e"],
                         [("keydown", "e"), ("keyup", "e")])
        self.assertEqual(self.sender._keys_down, {})

    def test_pending_dead_accent_cannot_turn_a_ctrl_shortcut_into_text(self):
        self.translate = lambda vk, *args: capture_win.DeadKey("^") if vk == 0xde else "x"
        self.feed(0xde, True, 0x28)
        self.feed(0xde, False, 0x28)
        self.feed(0xa2, True, 0x1d)
        self.feed(0x58, True, 0x2d)
        self.feed(0x58, False, 0x2d)
        self.feed(0xa2, False, 0x1d)
        messages = self.messages()
        self.assertFalse(any(m["type"] == "text" for m in messages))
        self.assertEqual([(m["type"], m.get("key")) for m in messages if m.get("key") == "x"],
                         [("keydown", "x"), ("keyup", "x")])
        self.assertEqual(self.sender._keys_down, {})

    def test_text_release_allows_new_ignore_rule_on_the_next_press(self):
        self.feed(0xa5, True)
        self.feed(0x45, True, 0x12)
        self.feed(0x45, False, 0x12)
        self.feed(0xa5, False)
        self.messages()
        self.sender._ignore_gate.configure(["key:69"])
        self.assertEqual(self.feed(0x45, True, 0x12), 99)
        self.assertEqual(self.feed(0x45, False, 0x12), 99)
        self.assertEqual(self.messages(), [])

    def test_dead_release_allows_new_ignore_rule_on_the_next_press(self):
        self.translate = lambda *args: capture_win.DeadKey("´")
        self.feed(0xde, True, 0x28)
        self.feed(0xde, False, 0x28)
        self.sender._ignore_gate.configure(["key:222"])
        self.translate = lambda *args: "'"
        self.assertEqual(self.feed(0xde, True, 0x28), 99)
        self.assertEqual(self.feed(0xde, False, 0x28), 99)
        self.assertEqual(self.messages(), [])

    def test_local_altgr_press_keeps_its_release_after_crossing_to_peer(self):
        self.sender._owner.on = None
        self.assertEqual(self.feed(0xa5, True), 99)
        self.assertEqual(self.feed(0x45, True, 0x12), 99)
        self.sender._owner.on = bytes(range(16, 32))
        self.assertEqual(self.feed(0x45, True, 0x12), 99)
        self.assertEqual(self.feed(0x45, False, 0x12), 99)
        self.feed(0xa5, False)
        self.assertFalse(any(m["type"] == "text" for m in self.messages()))

    def test_local_dead_press_keeps_its_release_after_layout_and_owner_change(self):
        self.sender._owner.on = None
        self.translate = lambda *args: capture_win.DeadKey("´")
        self.assertEqual(self.feed(0xde, True, 0x28), 99)
        self.sender._owner.on = bytes(range(16, 32))
        self.translate = lambda *args: "'"
        self.assertEqual(self.feed(0xde, True, 0x28), 99)
        self.assertEqual(self.feed(0xde, False, 0x28), 99)
        self.assertEqual(self.messages(), [])


if __name__ == "__main__":
    unittest.main()
