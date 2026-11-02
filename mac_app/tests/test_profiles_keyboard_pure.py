"""Exercise the canvas key handler without loading AppKit or starting NSApplication.

The native module cannot be imported in a sandbox without a window-server connection;
extract its unchanged method bodies so this covers the real dispatch logic there too.
"""

import ast
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1].joinpath("diagram.py").read_text()


class PickerKeyboard(unittest.TestCase):
    def canvas(self, picker=True):
        tree = ast.parse(SOURCE)
        atlas = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "AtlasCanvas")
        methods = [node for node in atlas.body if isinstance(node, ast.FunctionDef)
                   and node.name in ("keyDown_", "select_tile", "reading_order")]
        for node in methods:
            node.decorator_list = []
        atlas.bases, atlas.decorator_list, atlas.body = [], [], methods
        namespace = {"AppKit": SimpleNamespace(NSEventModifierFlagShift=1),
                     "objc": SimpleNamespace(super=lambda *_: SimpleNamespace(keyDown_=mock.Mock()))}
        exec(compile(ast.Module(body=[atlas], type_ignores=[]), "diagram.py", "exec"), namespace)
        canvas = namespace["AtlasCanvas"]()
        canvas.machine_picker, canvas.read_only, canvas.cycle_enabled = picker, False, False
        canvas.selected = canvas.own_id = canvas.fixed_machine_id = "home"
        canvas.tiles = {"home": dict(x=0, y=0, w=160, h=90),
                        "other": dict(x=160, y=0, w=160, h=90)}
        canvas.on_select = mock.Mock()
        canvas.on_rename = mock.Mock()
        canvas._draw = mock.Mock()
        canvas._show_recentre = lambda: False
        return canvas

    def event(self, code, shift=False):
        return SimpleNamespace(keyCode=lambda: code, modifierFlags=lambda: int(shift))

    def test_tab_and_shift_tab_focus_without_committing(self):
        canvas = self.canvas()
        canvas.keyDown_(self.event(48))
        self.assertEqual(canvas.selected, "other")
        self.assertTrue(canvas.keyboard_focus)
        canvas.on_select.assert_not_called()
        canvas.keyDown_(self.event(48, shift=True))
        self.assertEqual(canvas.selected, "home")
        canvas.on_select.assert_not_called()
        canvas._draw.assert_called_with(False)

    def test_return_and_space_commit_the_focused_machine(self):
        canvas = self.canvas()
        canvas.keyDown_(self.event(48))
        canvas.keyDown_(self.event(36))
        canvas.keyDown_(self.event(49))
        self.assertEqual(canvas.on_select.call_args_list, [mock.call("other"), mock.call("other")])
        canvas.on_rename.assert_not_called()

    def test_tab_at_the_end_yields_focus_without_committing(self):
        canvas = self.canvas()
        canvas.selected = "other"
        canvas.keyDown_(self.event(48))
        self.assertEqual(canvas.selected, "other")
        self.assertFalse(canvas.keyboard_focus)
        canvas.on_select.assert_not_called()


if __name__ == "__main__":
    unittest.main()
