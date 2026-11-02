"""Synthetic AppKit coverage for the profiles picker and remote ledger; run offscreen."""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import AppKit

AppKit.NSApplication.sharedApplication()

import diagram
import ledger
from core import crossing_state
from test_crossing_page_machines import BEE, SEA, Page


class ProfilesMap(unittest.TestCase):
    def canvas(self, **options):
        canvas = diagram.AtlasCanvas.alloc().initWithFrame_(((0, 0), (600, 260))).setup()
        tiles = {"home": {"x": 0, "y": 0, "w": 160, "h": 90},
                 "other": {"x": 160, "y": 0, "w": 160, "h": 90}}
        states = {"home": {"state": "this"},
                  "other": {"state": "edge", "linked": True, "crosses": True}}
        canvas.show_layout(tiles, states, {"home": "This Mac", "other": "Other"},
                           "home", "other", **options)
        return canvas

    def test_profile_word_is_secondary_status_and_preserves_crossing_outcome(self):
        canvas = self.canvas(tile_words={"home": "Desk", "other": "Own"}, machine_picker=True)
        self.assertEqual(canvas.state_for_tile["other"].string(), "Own")
        self.assertEqual(canvas.outcome_for_tile["other"].string(), "Crosses")
        self.assertIn("Own", canvas._description())

    def test_picker_draws_thirds_and_unlit_tracks_for_the_viewed_machine(self):
        canvas = self.canvas(machine_picker=True)
        canvas.selected = "home"
        canvas.states["other"].update(picked_thirds=["middle"], lit_thirds=["middle"], edge_off=False)
        canvas._draw(False)
        segments = canvas._third_segments(canvas._map()[0])
        self.assertEqual({part for _peer, part, *_ in segments}, {"start", "middle", "end"})
        self.assertEqual(len([segment for segment in canvas.seam_segments if segment["kind"] == "track"]), 2)

    def test_picker_includes_home_and_return_space_pick_instead_of_rename_or_cycle(self):
        canvas = self.canvas(machine_picker=True)
        selected, renamed = [], []
        canvas.on_select = selected.append
        canvas.on_rename = renamed.append
        self.assertIn("home", canvas.reading_order())
        canvas.select_tile("home")
        for code in (36, 49):
            event = mock.Mock()
            event.keyCode.return_value = code
            canvas.keyDown_(event)
        self.assertEqual(selected, ["home", "home", "home"])
        self.assertEqual(renamed, [])

    def test_read_only_picker_selects_but_arrow_cannot_move_a_tile(self):
        canvas = self.canvas(machine_picker=True, read_only=True)
        before = dict(canvas.tiles["other"])
        event = mock.Mock()
        event.keyCode.return_value = 124
        canvas.keyDown_(event)
        self.assertEqual(canvas.tiles["other"], before)
        canvas.select_tile("home")
        self.assertEqual(canvas.selected, "home")

    def test_picker_tab_moves_focus_without_committing_until_return_or_space(self):
        canvas = self.canvas(machine_picker=True)
        selected = []
        canvas.on_select = selected.append
        canvas.selected = "home"
        event = mock.Mock()
        event.keyCode.return_value = 48
        event.modifierFlags.return_value = 0
        canvas.keyDown_(event)
        self.assertEqual(canvas.selected, "other")
        self.assertTrue(canvas.keyboard_focus)
        self.assertEqual(selected, [])
        event.modifierFlags.return_value = AppKit.NSEventModifierFlagShift
        canvas.keyDown_(event)
        self.assertEqual(canvas.selected, "home")
        self.assertEqual(selected, [])
        for code in (36, 49):
            event.keyCode.return_value = code
            canvas.keyDown_(event)
        self.assertEqual(selected, ["home", "home"])

    def test_remote_unconnected_tile_says_set_elsewhere_without_amber(self):
        canvas = self.canvas(machine_picker=True, fixed_machine_id="home")
        canvas.own_id = "other"
        canvas.states["home"].update(state="gap", linked=True, crosses=False)
        canvas.selected = "other"
        with mock.patch.object(diagram.theme, "tint", wraps=diagram.theme.tint) as tint:
            canvas._draw(False)
        self.assertEqual(canvas.outcome_for_tile["home"].string(), "Set elsewhere")
        tile_calls = [call for call in tint.call_args_list if call.args[0] == canvas.layer_for_tile["home"]]
        self.assertEqual(tile_calls[-1].kwargs["border"], "edge")

    def test_tile_word_dots_use_signal_for_desk_and_own_only(self):
        canvas = self.canvas(machine_picker=True)
        for word, colour in (("Desk", "signal"), ("Own", "signal"),
                             ("Older", "ink_3"), ("Offline", "ink_3")):
            with self.subTest(word=word):
                canvas.tile_words["other"] = word
                with mock.patch.object(diagram.theme, "tint", wraps=diagram.theme.tint) as tint:
                    canvas._draw(False)
                dots = [call for call in tint.call_args_list if call.args[0] == canvas.dot_for_tile["other"]]
                self.assertEqual(dots[-1].kwargs["background"], colour)

    def test_picker_tiles_offer_pointing_hand_even_when_editable(self):
        canvas = self.canvas(machine_picker=True)
        event = mock.Mock()
        event.locationInWindow.return_value = AppKit.NSMakePoint(300, 80)
        with mock.patch.object(canvas, "tile_at", return_value="other"), \
                mock.patch.object(canvas, "_resize_hit", return_value=None), \
                mock.patch.object(canvas, "_third_hit", return_value=None), \
                mock.patch.object(diagram.AppKit, "NSCursor") as cursor:
            canvas.mouseMoved_(event)
        cursor.pointingHandCursor.return_value.set.assert_called_once_with()
        cursor.openHandCursor.assert_not_called()

    def test_read_only_cannot_recentre_a_machine(self):
        canvas = self.canvas(machine_picker=True, read_only=True)
        canvas.tiles["other"]["y"] = 80
        before = dict(canvas.tiles["other"])
        self.assertFalse(canvas._show_recentre())
        self.assertFalse(canvas.recentre_selected())
        self.assertEqual(canvas.tiles["other"], before)

    def test_viewed_machine_can_move_while_the_physical_home_tile_stays_fixed(self):
        canvas = self.canvas(machine_picker=True, fixed_machine_id="home")
        canvas.own_id = "other"
        moved = []
        canvas.on_layout_change = lambda tiles, key, **_kwargs: moved.append(key)
        event = mock.Mock()
        event.keyCode.return_value = 124
        event.modifierFlags.return_value = 0
        canvas.select_tile("other")
        canvas.keyDown_(event)
        self.assertEqual(moved, ["other"])
        canvas.select_tile("home")
        before = dict(canvas.tiles["home"])
        # The fixed tile hands the key to NSView, which a mock event cannot reach.
        with mock.patch.object(diagram.objc, "super"):
            canvas.keyDown_(event)
        self.assertEqual(canvas.tiles["home"], before)
        self.assertEqual(moved, ["other"])

    def test_peer_tile_selection_precedes_a_third_hit_in_picker_mode(self):
        canvas = self.canvas(machine_picker=True, read_only=False)
        selected = []
        canvas.on_select = selected.append
        event = mock.Mock()
        event.locationInWindow.return_value = AppKit.NSMakePoint(300, 80)
        event.clickCount.return_value = 1
        event.modifierFlags.return_value = 0
        with mock.patch.object(canvas, "window"), \
                mock.patch.object(canvas, "tile_at", return_value="other"), \
                mock.patch.object(canvas, "_resize_hit", return_value=None), \
                mock.patch.object(canvas, "display_at", return_value=("other", None)), \
                mock.patch.object(canvas, "_third_hit", return_value=("other", "start")), \
                mock.patch.object(diagram.AppKit, "NSCursor"):
            canvas.mouseDown_(event)
        self.assertIsNone(canvas.third_pressed)
        self.assertEqual(selected, ["other"])


class ProfilesLedger(Page):
    def test_disabled_edge_chips_draw_solid_neutral_borders_and_text(self):
        chip = ledger.widgets.box(None, None, 5, cls=ledger.Chip).setup(lambda: None)
        chip.setFrame_(((0, 0), (140, 26)))
        for kind in ("free", "contact_on", "on", "kept"):
            with self.subTest(kind=kind):
                chip.apply(kind, "Right", ["middle"], False)
                with mock.patch.object(ledger.AppKit, "NSGraphicsContext"), \
                        mock.patch.object(chip, "paint_box") as paint, \
                        mock.patch.object(ledger, "_icon") as icon, \
                        mock.patch.object(ledger, "_stroke") as stroke, \
                        mock.patch.object(ledger.widgets, "attributed") as attributed:
                    attributed.return_value.size.return_value.height = 12
                    chip.drawRect_(chip.bounds())
                self.assertEqual(paint.call_args.args[:4], (None, "rule", 1.0, False))
                self.assertEqual(attributed.call_args.kwargs["ink"], "ink_3")
                self.assertEqual(icon.call_args.args[1], "ink_3")
                self.assertTrue(all(call.args[1] == "ink_3" for call in stroke.call_args_list))

    def test_disabled_corner_chips_draw_solid_neutral_borders(self):
        button = ledger.widgets.box(None, None, 5, cls=ledger.CornerButton).setup("top_left", lambda: None)
        button.setFrame_(((0, 0), (28, 28)))
        for kind in ("on", "free"):
            with self.subTest(kind=kind):
                button.apply(kind, False)
                with mock.patch.object(ledger.AppKit, "NSGraphicsContext"), \
                        mock.patch.object(button, "paint_box") as paint, \
                        mock.patch.object(ledger, "_stroke") as stroke:
                    button.drawRect_(button.bounds())
                self.assertEqual(paint.call_args.args[:4], (None, "rule", 1.0, False))
                self.assertEqual(stroke.call_args.args[1], "ink_3")

    def test_remote_row_click_selects_destination_without_switching_viewed_machine(self):
        window = self.two()
        window._profile_event(crossing_state.set_same_ways, True)
        window._atlas_selected(BEE)
        window.crossing_ledger.rows[SEA].mouseDown_(None)
        self.assertEqual(window.viewed_machine, BEE)
        self.assertEqual(window.chosen_peer, SEA)
        self.assertTrue(window.crossing_ledger.rows[SEA].selected)

    def test_remote_hides_local_controls_and_reallocates_the_keys_column(self):
        window = self.two()
        table = window.crossing_ledger
        table.setFrame_(((0, 0), (800, 300)))
        table.refresh(self.store.current(), BEE, remote=True, has_notch=False)
        row = table.rows[BEE]
        self.assertTrue(row.cells["keys"].isHidden())
        self.assertTrue(row.rename.isHidden())
        self.assertTrue(row.leave.isHidden())
        self.assertTrue(table.headers[-1].view.isHidden())
        self.assertTrue(table.headers[-2].view.isHidden())
        self.assertTrue(row.cells["notch"].isHidden())
        self.assertEqual(len(table.accessibilityColumns()), 3)
        self.assertEqual(row.cells["corner"].frame().origin.x + row.cells["corner"].frame().size.width, 800)
        self.assertEqual(row.cells["notch"].frame().size.width, 0)
        self.assertEqual(row.cells["notch"].frame().size.height, 0)
        self.assertFalse(row.notch.isEnabled())
        self.assertTrue(row.no_notch.view.isHidden())
        table.setFrame_(((0, 0), (400, 300)))
        self.assertEqual(row.cells["notch"].frame().size.width, 0)
        self.assertEqual(row.cells["notch"].frame().size.height, 0)
        table.refresh(self.store.current(), BEE)
        self.assertFalse(row.cells["notch"].isHidden())
        self.assertEqual(len(table.accessibilityColumns()), 5)

    def test_read_only_disables_edges_corners_and_notch_then_local_restores_controls(self):
        window = self.two()
        table = window.crossing_ledger
        table.refresh(self.store.current(), BEE, remote=True, read_only=True, has_notch=True)
        row = table.rows[BEE]
        self.assertFalse(any(chip.enabled for chip in row.chips.values()))
        self.assertFalse(any(button.enabled for button in row.corner_buttons.values()))
        self.assertFalse(row.notch.isEnabled())
        table.refresh(self.store.current(), BEE)
        self.assertFalse(row.cells["keys"].isHidden())
        self.assertFalse(row.rename.isHidden())
        self.assertFalse(row.leave.isHidden())


if __name__ == "__main__":
    unittest.main()
