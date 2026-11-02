"""Windows capture to Linux input planning, without a GUI or native input injection."""

from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "win_app"))

import capture_win
import inject_portal
import inject_x11
import sender
from core import owner, protocol


class AccentKeymap:
    # Spanish-like dead accents, plus an AltGr vowel. AltGr is deliberately Mod3.
    KEYS = {26: ("e", "E"), 32: ("o", "O"), 30: ("u", "U"), 31: ("i", "I"),
            48: (0xfe51, 0xfe52), 49: (0xfe57, 0xfe57), 50: (0xffe1, 0xffe1),
            108: (0xfe03, 0xfe03)}
    min_keycode, max_keycode = 8, 255

    def keysym(self, code, mods, group):
        if group != 0:
            return 0
        if code == 31 and mods & 32:
            return 0x20ac  # EuroSign is a legacy XKB keysym, not a Unicode-prefixed one.
        if code == 26 and mods & 32:
            return ord("É" if mods & 1 else "é")
        pair = self.KEYS.get(code)
        if pair is None:
            return 0
        upper = bool(mods & 1) != bool(mods & 2 and isinstance(pair[0], str))
        value = pair[int(upper)]
        return ord(value) if isinstance(value, str) else value

    def modifier_mask(self, code):
        return {50: 1, 108: 32}.get(code, 0)

    def text(self, sym):
        return chr(sym) if 0 < sym < 0xfe00 else None

    def empty(self, code):
        return code not in self.KEYS


class CaptureApi:
    def GetForegroundWindow(self):
        return 99

    def GetWindowThreadProcessId(self, window, process):
        return 17

    def GetKeyboardLayout(self, thread):
        return "Spanish" if thread == 17 else "US"

    def ToUnicodeEx(self, vk, scan, state, buffer, size, flags, layout):
        if vk == 0xde:
            buffer[0] = "´" if layout == "Spanish" else "'"
            return -1 if layout == "Spanish" else 1
        buffer[0] = "e"
        return 1


class AccentTests(unittest.TestCase):
    def test_capture_reads_foreground_layout_instead_of_hook_thread_layout(self):
        state = capture_win.KeyboardState()
        with patch.object(capture_win, "user32", CaptureApi()):
            dead = capture_win.key_name(0xde, 0, state, capture_win._to_unicode)
            self.assertIsInstance(dead, capture_win.DeadKey)
            self.assertEqual(capture_win.key_name(0x45, 0, state, capture_win._to_unicode), "é")

    def test_no_foreground_window_uses_hook_layout(self):
        api = CaptureApi()
        api.GetForegroundWindow = lambda: 0
        with patch.object(capture_win, "user32", api):
            self.assertEqual(capture_win._to_unicode(0xde, 0, {}), "'")

    def test_altgr_printable_uses_text_but_unchanged_shortcut_stays_a_key(self):
        state = capture_win.KeyboardState()
        state.altgr_down = True

        def translate(vk, scan, modifiers):
            return "é" if modifiers.get(0x12, 0) & 0x80 else "e"

        translated = capture_win.key_name(0x45, 0, state, translate)
        self.assertIsInstance(translated, capture_win.TextInput)
        self.assertEqual(translated.text, "é")
        self.assertEqual(capture_win.key_name(0x45, 0, state, lambda *args: "e"), "e")

    def test_windows_dead_keys_survive_sender_wire_and_both_linux_planners(self):
        for accent, base, want, strokes in (("´", "e", "é", [26]),
                                           ("^", "o", "ô", [48, 32]),
                                           ("¨", "u", "ü", [49, 30])):
            for layout_class in (inject_x11.Layout, inject_portal.WaylandLayout):
                with self.subTest(accent=accent, backend=layout_class.__name__):
                    keyboard = capture_win.KeyboardState()
                    translate = lambda vk, scan, mods: capture_win.DeadKey(accent) if vk == 0xde else base
                    link_sender = self.link_sender()
                    dead = capture_win.key_name(0xde, 0, keyboard, translate)
                    self.assertTrue(link_sender.on_dead_key(0xde))
                    self.assertIsInstance(dead, capture_win.DeadKey)
                    capture_win.key_name(0xde, 0, keyboard, translate, False)
                    name = capture_win.key_name(ord(base.upper()), 0, keyboard, translate)
                    self.assertEqual(name, want)
                    self.assertTrue(link_sender.on_key(name, True, ord(base.upper()), base))
                    self.assertTrue(link_sender.on_key(base, False, ord(base.upper()), base))
                    messages = self.messages(link_sender)
                    self.assertEqual([m["key"] for m in messages], [want, want])
                    state = inject_x11.KeyState()
                    layout = layout_class(AccentKeymap())
                    steps = []
                    for read in messages:
                        steps += inject_x11.plan_key(read["key"], read["type"] == "keydown", read["us"], state, layout, 0, 0)
                    presses = [s[1] for s in steps if s[0] == inject_x11.PRESS and s[1] not in (50, 108)]
                    if layout.can_remap:
                        self.assertEqual(steps, [(inject_x11.MAP, 255, ord(want), False),
                                                 (inject_x11.PRESS, 255), (inject_x11.RELEASE, 255)])
                    else:
                        self.assertFalse(any(step[0] == inject_x11.MAP for step in steps))
                        self.assertEqual(presses, strokes)
                    self.assertEqual(state.chars_down, {})

    def test_text_and_repeated_composed_keys_restore_modifiers_and_release_strokes(self):
        for locks in (0, 2):
            with self.subTest(locks=locks):
                state = inject_x11.KeyState()
                state.mods_down = {"shift_r"}
                layout = inject_portal.WaylandLayout(AccentKeymap())
                steps = inject_x11.plan_key("ô", True, "o", state, layout, 0, locks)
                self.assertTrue(steps)
                self.assertEqual(steps, inject_x11.plan_key("ô", True, "o", state, layout, 0, locks))
                self.assertEqual(inject_x11.plan_key("ô", False, "o", state, layout, 0, locks), [])
                self.assertEqual(steps[0], (inject_x11.RELEASE, 62))
                self.assertEqual(steps[-1], (inject_x11.PRESS, 62))
                self.assertEqual(state.mods_down, {"shift_r"})
                self.assertEqual(state.chars_down, {})
                self.assertTrue(inject_x11.plan_text("éôü", state, layout, 0, locks))

    def test_altgr_text_from_sender_brackets_ctrl_alt_and_reaches_wayland(self):
        link_sender = self.link_sender()
        link_sender.on_key("cmd", True, 0xa2)
        link_sender.on_key("alt_r", True, 0xa5)
        link_sender.on_text("é", 0x45)
        messages = self.messages(link_sender)
        self.assertTrue(any(m.get("text") == "é" for m in messages))
        state = inject_x11.KeyState()
        layout = inject_portal.WaylandLayout(AccentKeymap())
        for read in messages:
            if read["type"] == "text":
                steps = inject_x11.plan_text(read["text"], state, layout, 0, 0)
                self.assertIn((inject_x11.PRESS, 26), steps)
                self.assertEqual(state.mods_down, set())
            else:
                inject_x11.plan_key(read["key"], read["type"] == "keydown", read["us"], state, layout, 0, 0)
        self.assertEqual(state.mods_down, {"ctrl", "alt_r"})

    def test_unavailable_layout_character_and_chord_are_not_replaced_with_compose(self):
        state = inject_x11.KeyState()
        layout = inject_portal.WaylandLayout(AccentKeymap())
        self.assertEqual(inject_x11.plan_key("ô", True, None, state, layout, 1, 0), [])
        state.mods_down = {"ctrl"}
        self.assertEqual(inject_x11.plan_key("ô", True, "o", state, layout, 0, 0), [(inject_x11.PRESS, 32)])

    def test_legacy_altgr_keysym_is_looked_up_by_its_unicode_text(self):
        layout = inject_portal.WaylandLayout(AccentKeymap())
        steps = inject_x11.plan_text("€", inject_x11.KeyState(), layout, 0, 0)
        self.assertIn((inject_x11.PRESS, 31), steps)

    def test_repeated_composition_follows_lock_changes(self):
        layout = inject_portal.WaylandLayout(AccentKeymap())
        state = inject_x11.KeyState()
        first = inject_x11.plan_key("ô", True, "o", state, layout, 0, 0)
        repeat = inject_x11.plan_key("ô", True, "o", state, layout, 0, 2)
        self.assertNotEqual(repeat, first)
        self.assertIn((inject_x11.PRESS, 50), repeat[-5:])
        self.assertEqual(inject_x11.plan_key("ô", False, "o", state, layout, 0, 2), [])

    def test_composition_does_not_release_another_held_character(self):
        layout = inject_portal.WaylandLayout(AccentKeymap())
        state = inject_x11.KeyState()
        inject_x11.plan_key("o", True, "o", state, layout, 0, 0)
        self.assertEqual(inject_x11.plan_key("ô", True, None, state, layout, 0, 0), [])
        self.assertEqual(state.chars_down, {"o": (32, None)})

    def test_altgr_dead_key_keeps_composition_until_the_next_character(self):
        state = capture_win.KeyboardState()
        state.altgr_down = True

        def translate(vk, scan, modifiers):
            return capture_win.DeadKey("´") if vk == 0xde and modifiers.get(0x12) else "e"

        self.assertIsInstance(capture_win.key_name(0xde, 0, state, translate), capture_win.DeadKey)
        result = capture_win.key_name(0x45, 0, state, translate)
        self.assertIsInstance(result, capture_win.TextInput)
        self.assertEqual(result.text, "é")

    @staticmethod
    def link_sender():
        result = sender.LinkSender(None, lambda: {}, links=lambda _: SimpleNamespace(live=lambda peer: True))
        result._owner = owner.Owner(bytes(range(16)), lambda: 0)
        result._owner.on = bytes(range(16, 32))
        return result

    @staticmethod
    def messages(link_sender):
        messages = []
        while not link_sender._outbound.empty():
            action = link_sender._outbound.get_nowait()
            message = dict(action.message)
            message["data"] = dict(message["data"], seq=len(messages) + 1)
            messages.append(protocol.read_input(protocol.parse_message(protocol.dumps_message(message))))
        return messages


if __name__ == "__main__":
    unittest.main()
