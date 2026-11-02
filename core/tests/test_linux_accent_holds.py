"""Accent key presses retain shortcut and held-key semantics without native injection."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "win_app"))

import inject_portal
import inject_x11


class AccentKeymap:
    min_keycode, max_keycode = 8, 255
    symbols = {26: (ord("e"), ord("E")), 32: (ord("o"), ord("O")),
               48: (0xfe51, 0xfe52), 50: (0xffe1, 0xffe1),
               108: (0xfe03, 0xfe03)}

    def keysym(self, code, mods, group):
        if code == 26 and mods & 32:
            return ord("é")
        return self.symbols.get(code, (0, 0))[bool(mods & 1)]

    def modifier_mask(self, code):
        return {50: 1, 108: 32}.get(code, 0)

    def text(self, symbol):
        return chr(symbol) if 0 < symbol < 0xfe00 else None

    def empty(self, code):
        return code not in self.symbols


class AccentHoldTests(unittest.TestCase):
    def test_x11_holds_remapped_altgr_and_composed_characters_until_release(self):
        for character, us in (("é", "e"), ("ô", "o")):
            with self.subTest(character=character):
                layout = inject_x11.Layout(AccentKeymap())
                state = inject_x11.KeyState()
                steps = inject_x11.plan_key(character, True, us, state, layout, 0, 0)
                self.assertEqual(steps, [(inject_x11.MAP, 255, ord(character), False),
                                         (inject_x11.PRESS, 255)])
                self.assertEqual(state.chars_down, {character: (255, None)})
                self.assertEqual(state.taps_down, set())
                self.assertEqual(inject_x11.plan_key(character, True, us, state, layout, 0, 0), [])
                self.assertEqual(inject_x11.plan_key(character, False, us, state, layout, 0, 0),
                                 [(inject_x11.RELEASE, 255)])
                self.assertEqual(state.chars_down, {})

    def test_wayland_composition_repeat_becomes_shortcut_without_releasing_modifier(self):
        for modifier in ("ctrl", "ctrl_r", "alt", "alt_r", "cmd", "cmd_r"):
            with self.subTest(modifier=modifier):
                layout = inject_portal.WaylandLayout(AccentKeymap())
                state = inject_x11.KeyState()
                initial = inject_x11.plan_key("ô", True, "o", state, layout, 0, 0)
                self.assertIn((inject_x11.PRESS, 48), initial)
                self.assertIn((inject_x11.RELEASE, 32), initial)
                self.assertEqual(state.taps_down, {"ô"})
                modifier_steps = inject_x11.plan_key(modifier, True, None, state, layout, 0, 0)
                modifier_code = modifier_steps[0][1]
                self.assertEqual(inject_x11.plan_key("ô", True, "o", state, layout, 0, 0),
                                 [(inject_x11.PRESS, 32)])
                self.assertEqual(state.mods_down, {modifier})
                self.assertEqual(state.taps_down, set())
                self.assertEqual(state.chars_down, {"ô": (32, None)})
                self.assertEqual(inject_x11.plan_key("ô", True, "o", state, layout, 0, 0), [])
                self.assertEqual(inject_x11.plan_key("ô", False, "o", state, layout, 0, 0),
                                 [(inject_x11.RELEASE, 32)])
                self.assertEqual(inject_x11.plan_key(modifier, False, None, state, layout, 0, 0),
                                 [(inject_x11.RELEASE, modifier_code)])
                self.assertEqual((state.mods_down, state.named_down, state.chars_down, state.taps_down),
                                 (set(), set(), {}, set()))

    def test_x11_without_spares_never_replaces_held_input_with_compose_taps(self):
        layout = inject_x11.Layout(AccentKeymap())
        layout.spare = lambda: []
        state = inject_x11.KeyState()
        self.assertEqual(inject_x11.plan_key("ô", True, "o", state, layout, 0, 0), [])
        self.assertEqual((state.chars_down, state.taps_down), ({}, set()))


if __name__ == "__main__":
    unittest.main()
