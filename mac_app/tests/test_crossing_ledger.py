"""Direction A section 9: native ledger controls, round trips and accessibility."""

import copy
import itertools
from unittest import mock

import AppKit

from core import ways
import test_part_edge_thirds as thirds
from test_crossing_page_machines import BEE, SEA, Page, press, token
import ledger


class LedgerTests(Page):
    layout = thirds.PartEdgeThirds.layout
    row = thirds.PartEdgeThirds.row
    picked = thirds.PartEdgeThirds.picked
    pick_thirds = thirds.PartEdgeThirds.pick_thirds
    whole_edge = thirds.PartEdgeThirds.whole_edge

    def rebuilt(self):
        window = self.build([(peer["id"], peer["token"], peer["name"],
                              {key: value for key, value in peer.items() if key not in ("id", "token", "name")})
                             for peer in self.store.current()["peers"]], self.store.current()["zones"])
        window.atlas_canvas.setFrame_(((0, 0), (480, window.atlas_canvas.HEIGHT)))
        window.atlas_canvas._draw(False)
        return window

    def test_seven_third_combinations_rebuild_to_same_cells_and_map(self):
        self.layout()
        for count in (1, 2, 3):
            for combination in itertools.combinations(ways.PARTS, count):
                with self.subTest(thirds=combination):
                    self.pick_thirds(combination)
                    self.assertEqual(self.store.current()["peers"][0]["ways"]["thirds"], list(combination))
                    self.rebuilt()
                    self.assertEqual(self.picked(), list(combination))
                    self.assertEqual(self.window.atlas_canvas.states[BEE]["picked_thirds"], list(combination))

    def chip(self, side, peer=BEE):
        return self.window.crossing_ledger.rows[peer].chips[side]

    def corner(self, corner, peer=BEE):
        return self.window.crossing_ledger.rows[peer].corner_buttons[corner]

    def test_reach_is_compiled_once_per_settings_not_once_per_status_tick(self):
        self.layout()
        self.window._reach_stamp = None
        with mock.patch.object(ways, "reach", wraps=ways.reach) as reach:
            for _ in range(3):
                self.window._show_arrangement()
                self.window.crossing_ledger.refresh(self.store.current(), self.window.chosen_peer)
            self.assertEqual(reach.call_count, 1)
            press(self.chip("top"))
            self.assertGreater(reach.call_count, 1, "a change to the settings asks again")

    def test_a_corner_left_on_after_its_machine_moved_away_stays_removable(self):
        self.layout()
        press(self.corner("top_left"))
        settings = self.store.current()
        settings["layout"][BEE] = {"x": 400, "y": 400, "w": 160, "h": 100}
        self.store.save_settings(settings)
        self.window._show_arrangement()
        self.assertTrue(self.corner("top_left").enabled)
        press(self.corner("top_left"))
        self.assertEqual(self.store.current()["peers"][0]["ways"]["corners"], [])

    def test_last_ledger_segment_off_then_off_click_lights_only_that_segment(self):
        self.layout()
        self.pick_thirds(["start"])
        press(self.row().contact().segments["start"])
        self.assertEqual(self.row().contact().kind, "contact_off")
        self.assertEqual(self.picked(), [])
        self.assertFalse(self.store.current()["peers"][0]["ways"]["edge"])
        press(self.row().contact().segments["middle"])
        self.assertEqual(self.picked(), ["middle"])
        self.assertEqual(self.row().contact().kind, "contact_on")
        press(self.row().contact())
        self.assertEqual(self.picked(), [])
        press(self.row().contact())
        self.assertEqual(self.picked(), list(ways.PARTS))

    def test_every_chip_state_round_trips_through_the_saved_ways_the_ledger_and_the_map(self):
        self.layout()
        self.assertEqual([side for side, chip in self.row().chips.items() if not chip.isHidden()],
                         list(ways.EDGES))
        self.assertEqual(self.chip("right").kind, "contact_on")
        self.assertEqual(self.chip("left").kind, "free")
        press(self.chip("left"))
        self.assertEqual(self.ways_of(BEE)["edges"], ["left"])
        self.rebuilt()
        self.assertEqual(self.chip("left").kind, "on")
        self.assertEqual(self.chip("left").label, "Left, wraps")
        self.assertEqual(self.window.atlas_canvas.states[BEE]["wrap_sides"], ["left"])
        self.assertIn((BEE, "left"), self.window.atlas_canvas.wrap_layers)
        press(self.chip("left"))
        self.assertEqual(self.ways_of(BEE)["edges"], [])
        self.rebuilt()
        self.assertEqual(self.chip("left").kind, "free")
        self.assertNotIn((BEE, "left"), self.window.atlas_canvas.wrap_layers)
        press(self.chip("top"))
        press(self.chip("left"))
        self.assertEqual(self.ways_of(BEE)["edges"], ["left", "top"])
        press(self.chip("right"))
        self.assertEqual(self.chip("right").kind, "contact_off")
        self.rebuilt()
        self.assertEqual(self.chip("left").kind, "kept")
        self.assertEqual(self.window.atlas_canvas.states[BEE]["kept_sides"], ["left", "top"])
        self.assertEqual(self.window.atlas_canvas.states[BEE]["wrap_sides"], [])
        press(self.chip("left"))
        self.assertTrue(self.ways_of(BEE)["methods"])
        self.assertEqual(self.ways_of(BEE)["edges"], ["left", "top"])
        self.rebuilt()
        self.assertEqual(self.chip("right").kind, "contact_on")
        self.assertEqual(self.chip("left").kind, "on")
        press(self.chip("top"))
        self.assertEqual(self.ways_of(BEE)["edges"], ["left"])

    def test_chip_added_while_edge_is_off_turns_edge_on_with_the_remembered_thirds(self):
        self.layout()
        self.pick_thirds(["start", "end"])
        press(self.row().contact())
        self.assertEqual(self.picked(), [])
        press(self.chip("bottom"))
        self.assertEqual(self.picked(), ["start", "end"])
        self.assertEqual(self.ways_of(BEE)["edges"], ["bottom"])

    def test_contact_chip_leads_and_a_machine_cannot_wrap_to_its_own_contact(self):
        self.layout("top")
        row = self.row()
        self.assertEqual(row.chip_order[0], "top")
        self.assertEqual(self.chip("top").kind, "contact_on")
        self.assertEqual(self.chip("top").label, "Top")
        self.assertEqual(self.chip("top").toolTip(), "Top edge: touches Bee; click to turn it off")
        self.assertEqual(self.chip("left").toolTip(), "Left edge: click to wrap round to Bee")

    def test_every_corner_on_and_off_survives_page_rebuild(self):
        self.layout()
        for corner in ways.CORNERS:
            with self.subTest(corner=corner):
                press(self.corner(corner))
                self.assertIn("corner", self.ways_of(BEE)["methods"])
                self.assertEqual(self.ways_of(BEE)["corners"], [corner])
                self.rebuilt()
                self.assertEqual(self.corner(corner).kind, "on")
                self.assertEqual(self.corner(corner).accessibilityLabel(), f"{corner.replace('_', ' ').capitalize()} corner, on")
                self.assertIn((BEE, "corner", corner), self.window.atlas_canvas.mark_layers)
                press(self.corner(corner))
                self.rebuilt()
                self.assertNotIn("corner", self.ways_of(BEE)["methods"])
                self.assertEqual(self.corner(corner).kind, "free")

    def test_two_corners_on_at_once_and_a_pad_for_each(self):
        self.layout()
        press(self.corner("top_left"))
        press(self.corner("bottom_right"))
        self.assertEqual(self.ways_of(BEE)["corners"], ["top_left", "bottom_right"])
        self.rebuilt()
        canvas = self.window.atlas_canvas
        self.assertEqual(canvas.states[BEE]["corner_ways"], ["top_left", "bottom_right"])
        self.assertIn((BEE, "corner", "top_left"), canvas.mark_layers)
        self.assertIn((BEE, "corner", "bottom_right"), canvas.mark_layers)
        press(self.corner("top_left"))
        self.assertEqual(self.ways_of(BEE)["corners"], ["bottom_right"])

    def test_another_machines_corner_and_a_corner_with_no_contact_refuse_without_a_write(self):
        self.layout(second=True)
        press(self.corner("top_left"))
        self.assertEqual(self.ways_of(BEE)["corners"], ["top_left"])
        sea = self.window.crossing_ledger.rows[SEA]
        held = sea.corner_buttons["top_left"]
        self.assertEqual(held.kind, "held")
        self.assertFalse(held.enabled)
        self.assertEqual(held.toolTip(), "Bee's corner")
        before = copy.deepcopy(self.store.current())
        press(held)
        self.assertEqual(self.store.current(), before)
        self.window._ledger_corner(SEA, "top_left")
        self.assertEqual(self.store.current(), before)
        settings = self.store.current()
        settings["layout"][BEE]["x"] += 100
        self.store.save_settings(settings)
        self.window._show_arrangement()
        loose = self.window.crossing_ledger.rows[BEE].corner_buttons["bottom_right"]
        self.assertEqual(loose.kind, "unavailable")
        self.assertEqual(loose.toolTip(), "Place Bee beside this Mac first")
        before = copy.deepcopy(self.store.current())
        press(loose)
        self.window._ledger_corner(BEE, "bottom_right")
        self.assertEqual(self.store.current(), before)

    def test_a_side_held_by_another_machine_is_a_dimmed_chip_that_refuses_the_click(self):
        self.layout(second=True)
        row = self.row()
        held = row.chips["left"]
        self.assertEqual(held.kind, "held")
        self.assertEqual(held.label, "Left, Sea")
        self.assertFalse(held.enabled)
        self.assertEqual(held.toolTip(), "Left edge: Sea's")
        before = copy.deepcopy(self.store.current())
        press(held)
        self.window._ledger_side(BEE, "left")
        self.assertEqual(self.store.current(), before)

    def test_notch_on_and_off_rebuild_and_no_notch_is_disabled(self):
        self.layout()
        self.window.has_notch = True
        for on in (True, False):
            self.row().notch.setState_(int(on))
            press(self.row().notch)
            self.assertEqual("notch" in self.ways_of(BEE)["methods"], on)
            self.rebuilt()
            self.window.has_notch = True
            self.window._show_arrangement()
            self.assertEqual(self.row().notch.state(), int(on))
        self.row().notch.setState_(1)
        press(self.row().notch)
        self.window.has_notch = False
        self.window._show_arrangement()
        self.assertEqual(self.row().no_notch.text, "No notch")
        self.assertFalse(self.row().no_notch.view.isHidden())
        self.assertEqual(self.row().notch.accessibilityLabel(), "Notch, no notch")
        self.assertFalse(self.row().notch.isEnabled())

    def test_the_notch_checkbox_says_notch_and_return_through_it_shows_only_with_the_notch(self):
        # rc4: the checkbox said only On or Off, so where the notch was chosen could not be found.
        self.layout()
        self.window.has_notch = True
        self.window._show_arrangement()
        self.assertEqual(self.row().notch.title(), "Notch")
        self.assertEqual(self.row().notch.accessibilityLabel(), "Notch, off")
        self.assertTrue(self.row().notch_return.isHidden())
        self.row().notch.setState_(1)
        press(self.row().notch)
        self.window._show_arrangement()
        self.assertEqual(self.row().notch.accessibilityLabel(), "Notch, on")
        returns = self.row().notch_return
        self.assertFalse(returns.isHidden())
        self.assertEqual(returns.title(), "Return through the notch")
        returns.setState_(1)
        press(returns)
        zone = next(zone for zone in self.store.current()["zones"] if zone["kind"] == "notch")
        self.assertEqual((zone["peer"], zone.get("return_here")), (BEE, True))
        self.window._show_arrangement()
        self.assertEqual(self.row().notch_return.state(), 1)
        self.row().notch_return.setState_(0)
        press(self.row().notch_return)
        zone = next(zone for zone in self.store.current()["zones"] if zone["kind"] == "notch")
        self.assertNotIn("return_here", zone)

    def test_cycle_tick_and_jump_chord_round_trip_without_changing_ways(self):
        self.layout()
        before = self.ways_of(BEE)
        for on in (False, True):
            self.row().cycle.setState_(int(on))
            press(self.row().cycle)
            self.rebuilt()
            self.assertEqual(self.row().cycle.state(), int(on))
        import widgets
        recorder = self.row().jump
        event = mock.Mock()
        event.type.return_value = AppKit.NSEventTypeKeyDown
        event.modifierFlags.return_value = AppKit.NSEventModifierFlagCommand
        event.keyCode.return_value = next(code for code, name in widgets.SPECIAL_KEY_NAMES.items() if name == "f18")
        event_api = mock.Mock()
        event_api.addLocalMonitorForEventsMatchingMask_handler_.return_value = object()
        with mock.patch.object(widgets.AppKit, "NSEvent", event_api):
            recorder._clicked()
            self.assertIsNotNone(recorder.monitor)
            self.assertIsNone(recorder._recorded(event))
            self.assertIsNone(recorder.monitor)
        self.rebuilt()
        self.assertEqual(self.row().jump.value, "cmd+f18")
        self.assertIn("F18", self.row().jump.view.toolTip())
        self.assertEqual(self.ways_of(BEE), before)
        press(self.row().clear)
        self.assertEqual(self.row().jump.value, "")

    def test_offline_row_is_dimmed_but_ways_remain_live_and_saved(self):
        self.layout()
        self.window.windows_input.server.links.return_value = []
        self.window._show_arrangement()
        row = self.row()
        self.assertTrue(row.dimmed)
        self.assertEqual(row.status_text, "Windows · Offline, ways kept")
        self.assertEqual(row.cells["edge"].alphaValue(), .5)
        self.assertTrue(row.contact().segments["start"].isEnabled())
        self.assertTrue(row.contact().enabled)
        press(row.contact().segments["middle"])
        self.assertEqual(self.picked(), ["start", "end"])

    def test_armed_jump_survives_a_cell_refresh_and_escape_cancels(self):
        import widgets
        self.layout()
        recorder = self.row().jump
        event_api = mock.Mock()
        event_api.addLocalMonitorForEventsMatchingMask_handler_.return_value = object()
        with mock.patch.object(widgets.AppKit, "NSEvent", event_api):
            recorder._clicked()
            prompt = recorder.key.text
            press(self.row().contact().segments["start"])
            self.assertIsNotNone(recorder.monitor)
            self.assertEqual(recorder.key.text, prompt)
            escape = mock.Mock()
            escape.type.return_value = AppKit.NSEventTypeKeyDown
            escape.modifierFlags.return_value = 0
            escape.keyCode.return_value = 53
            self.assertIsNone(recorder._recorded(escape))
            self.assertIsNone(recorder.monitor)
            self.assertEqual(recorder.value, "")

    def test_arming_another_row_cancels_the_first_without_dropping_the_guard(self):
        import widgets
        self.layout(second=True)
        ledger = self.window.crossing_ledger
        first, second = ledger.rows[BEE].jump, ledger.rows[SEA].jump
        event_api = mock.Mock()
        event_api.addLocalMonitorForEventsMatchingMask_handler_.side_effect = [object(), object()]
        with mock.patch.object(widgets.AppKit, "NSEvent", event_api):
            first._clicked()
            with mock.patch.object(self.window, "_jump_arming", wraps=self.window._jump_arming) as guard:
                second._clicked()
                self.assertIsNone(first.monitor)
                self.assertIsNotNone(second.monitor)
                self.assertTrue(self.controller.jump_recording)
                self.assertTrue(all(call.args[0] for call in guard.call_args_list))
                first.cancel()
                self.assertTrue(self.controller.jump_recording)
                second.cancel()
                self.assertFalse(self.controller.jump_recording)

    def test_in_use_false_disables_kept_chips_like_left_out(self):
        self.layout()
        press(self.chip("left"))
        press(self.corner("top_left"))
        settings = self.store.current()
        settings["peers"][0]["in_use"] = False
        self.store.save_settings(settings)
        self.window._show_arrangement()
        row = self.row()
        self.assertIn("Left out", row.status_text)
        self.assertEqual(row.chips["left"].kind, "on", "kept ways drawn as they would cross, inert")
        self.assertEqual(row.corner_buttons["top_left"].kind, "on")
        for control in (*row.chips.values(), *row.corner_buttons.values(), *row.contact().segments.values()):
            if control.isHidden():
                continue
            self.assertFalse(control.enabled if hasattr(control, "enabled") and not isinstance(control, AppKit.NSButton)
                             else control.isEnabled())
            self.assertEqual(control.toolTip(), "Left out: Put back to change its ways")

    def test_chips_wrap_in_lines_and_corner_grid_is_two_by_two_at_both_widths(self):
        __import__("theme").init_fonts()
        self.layout(second=True)
        for width in (687, 399):
            self.window.crossing_ledger.setFrameSize_((width, 700))
            row = self.row()
            cell = row.cells["edge"].bounds().size.width
            shown = [chip for chip in row.chips.values() if not chip.isHidden()]
            for chip in shown:
                frame = chip.frame()
                self.assertEqual(frame.size.height, 26)
                self.assertLessEqual(frame.origin.x + frame.size.width, cell + .01)
                self.assertGreaterEqual(frame.size.width, chip.natural_width() - .01)
            first = next(chip for side in row.chip_order for chip in [row.chips[side]] if not chip.isHidden())
            self.assertEqual(first.frame().origin.x, 0)
            frames = [button.frame() for button in row.corner_buttons.values()]
            self.assertEqual([(f.origin.x, f.origin.y, f.size.width, f.size.height) for f in frames],
                             [(0, 0, 28, 28), (32, 0, 28, 28), (0, 32, 28, 28), (32, 32, 28, 28)])
            self.assertLessEqual(row.cells["corner"].frame().size.width, 70)
            self.assertGreaterEqual(row.cells["corner"].frame().size.height, 60)

    def test_selected_machine_status_dot_is_inset_from_the_rule_at_both_widths(self):
        self.layout()
        for width in (687, 399):
            self.window.crossing_ledger.setFrameSize_((width, 700))
            row = self.row()
            self.assertTrue(row.selected)
            machine = row.cells["machine"].frame()
            self.assertGreaterEqual(machine.origin.x + 1, 12)
            self.assertGreaterEqual(machine.origin.x + row.name.view.frame().origin.x, 12)
            self.assertGreaterEqual(row.status.view.frame().origin.x, 12)

    def test_jump_idle_is_a_text_button_or_fitted_chip_and_only_armed_is_a_field(self):
        import widgets
        self.layout()
        row = self.row()
        self.window.crossing_ledger.setFrameSize_((760, 700))
        self.assertGreaterEqual(row.jump_caption.view.frame().size.width - 4,
                                row.jump_caption.view.attributedStringValue().size().width)
        self.assertEqual(row.jump.key.text, "Set a key")
        self.assertEqual(row.jump.view.layer().borderWidth(), 0)
        row.jump.on_change("cmd+f18")
        chip = row.jump.view
        self.assertEqual(chip.layer().cornerRadius(), 4)
        self.assertEqual(chip.layer().borderWidth(), 1)
        self.assertTrue(row.jump.key.mono)
        self.assertEqual(row.jump.key.size, __import__("theme").TYPE["small"])
        self.assertGreaterEqual(chip.frame().size.width, row.jump.key.view.attributedStringValue().size().width + 12)
        self.assertFalse(row.clear.isHidden())
        self.assertEqual(row.clear.frame().origin.y, chip.frame().origin.y)
        idle_height = chip.frame().size.height
        event_api = mock.Mock()
        event_api.addLocalMonitorForEventsMatchingMask_handler_.return_value = object()
        with mock.patch.object(widgets.AppKit, "NSEvent", event_api):
            row.jump._clicked()
            self.assertGreater(chip.frame().size.height, idle_height)
            self.assertFalse(row.jump.hint.view.isHidden())
            row.jump.cancel()
        self.assertEqual(chip.frame().size.height, idle_height)

    def test_narrow_block_insets_every_cell_from_the_selection_rule(self):
        self.layout(second=True)
        ledger = self.window.crossing_ledger
        ledger.setFrameSize_((399, 700))
        for row in ledger.rows.values():
            for key in ("edge", "corner", "notch", "keys"):
                self.assertGreaterEqual(row.cells[key].frame().origin.x, 10)
            self.assertGreaterEqual(row.cells["machine"].frame().origin.y, 12)
            self.assertGreaterEqual(row.cells["keys"].frame().origin.y,
                                    row.cells["edge"].frame().origin.y + row.cells["edge"].frame().size.height)
            self.assertLess(row.frame().size.height, 250)

    def test_left_out_row_keeps_intent_and_put_back_restores_controls(self):
        self.layout()
        press(self.chip("left"))
        press(self.corner("top_left"))
        before = self.ways_of(BEE)
        press(self.row().leave)
        row = self.row()
        self.assertTrue(row.dimmed)
        self.assertEqual(row.status_text, "Windows · Left out")
        self.assertIn("Put back", row.leave.accessibilityLabel())
        self.assertEqual(row.chips["left"].kind, "on", "kept ways drawn as they would cross, inert")
        self.assertEqual(row.corner_buttons["top_left"].kind, "on")
        self.assertFalse(row.contact().segments["start"].isEnabled())
        self.assertFalse(row.chips["left"].enabled)
        self.assertFalse(row.corner_buttons["top_left"].enabled)
        held = copy.deepcopy(self.store.current())
        press(row.contact().segments["start"])
        press(row.chips["left"])
        press(row.corner_buttons["top_left"])
        self.assertEqual(self.store.current(), held)
        self.assertEqual(self.ways_of(BEE), before)
        press(row.leave)
        self.assertFalse(self.row().dimmed)
        self.assertTrue(self.row().contact().segments["start"].isEnabled())
        self.assertTrue(self.row().chips["left"].enabled)
        self.assertEqual(self.ways_of(BEE), before)

    def test_row_order_follows_map_position_and_left_out_stays_present(self):
        window = self.layout(second=True)
        self.assertEqual(window.crossing_ledger.order, [SEA, BEE])
        press(window.crossing_ledger.rows[SEA].leave)
        self.assertIn(SEA, window.crossing_ledger.order)

    def test_no_contact_has_no_contact_chip_and_corners_are_not_offered(self):
        self.layout()
        settings = self.store.current()
        settings["layout"][BEE]["x"] += 100
        self.store.save_settings(settings)
        self.window._show_arrangement()
        row = self.row()
        self.assertIsNone(row.contact())
        self.assertEqual({chip.kind for chip in row.chips.values()}, {"free"})
        self.assertTrue(row.edge_note.view.isHidden())
        self.assertTrue(all(button.kind == "unavailable" and not button.enabled for button in row.corner_buttons.values()))
        before = copy.deepcopy(self.store.current())
        self.window._ledger_corner(BEE, "top_left")
        self.assertEqual(self.store.current(), before)

    def test_narrow_cells_and_visible_controls_fit_inside_the_ledger(self):
        self.layout()
        self.row().jump.on_change("cmd+f18")
        ledger = self.window.crossing_ledger
        ledger.setFrameSize_((440, 700))
        self.assertTrue(all(header.view.isHidden() for header in ledger.headers))
        row = self.row()
        self.assertEqual(row.cells["machine"].frame().origin.x + row.cells["machine"].frame().size.width, 440)
        self.assertEqual(row.cells["edge"].frame().size.width, 440 - 24)
        self.assertGreaterEqual(row.cells["edge"].frame().origin.y,
                                row.cells["machine"].frame().origin.y + row.cells["machine"].frame().size.height)
        self.assertEqual(row.cells["corner"].frame().origin.y, row.cells["notch"].frame().origin.y)
        self.assertEqual(row.cells["notch"].frame().origin.y, row.cells["keys"].frame().origin.y)
        self.assertGreaterEqual(row.cells["corner"].frame().origin.y,
                                row.cells["edge"].frame().origin.y + row.cells["edge"].frame().size.height)
        for cell in row.cells.values():
            frame = cell.frame()
            self.assertGreaterEqual(frame.origin.x, 0)
            self.assertLessEqual(frame.origin.x + frame.size.width, 440)
            for control in cell.subviews():
                if not control.isHidden():
                    frame = control.frame()
                    self.assertGreaterEqual(cell.frame().origin.x + frame.origin.x, 0)
                    self.assertLessEqual(frame.origin.x + frame.size.width, cell.bounds().size.width)
                    self.assertLessEqual(cell.frame().origin.y + frame.origin.y + frame.size.height,
                                         row.frame().size.height)

    def test_window_width_selects_table_or_stacked_rows_without_sideways_scroll(self):
        from mac_app.tests.design_offscreen import settle
        from mac_app.tests.settings_layout_offscreen import measure
        self.layout(second=True)
        self.window._select_page("crossing")
        for width, narrow in ((960, False), (640, True), (1440, False)):
            with self.subTest(width=width):
                self.window.window.setFrame_display_(((100000, 100000), (width, 900)), False)
                for _ in range(4):
                    settle(self.window.window)
                table = self.window.crossing_ledger
                self.assertEqual(table.bounds().size.width < table.narrow_below(), narrow)
                self.assertEqual(all(header.view.isHidden() for header in table.headers), narrow)
                self.assertFalse(self.window.pages["crossing"].hasHorizontalScroller())
                self.assertEqual(measure(self.window, "crossing")["overflows"], [])
                self.assertFalse(self.window.window.isVisible())

    def test_thirds_bar_segments_are_nine_point_hit_targets_inside_the_contact_chip(self):
        for side in ways.EDGES:
            self.layout(side)
            self.window._select_page("crossing")
            self.window.crossing_ledger.setFrameSize_((700, 700))
            chip = self.row().contact()
            for part, segment in chip.segments.items():
                frame = segment.frame()
                with self.subTest(side=side, part=part):
                    self.assertEqual(frame.size.width, 9)
                    self.assertEqual(frame.size.height, 26)
                    centre = (frame.origin.x + 4.5, 13)
                    self.assertIs(chip.hitTest_(chip.convertPoint_toView_(centre, chip.superview())), segment)
            self.assertEqual(chip.segments["start"].toolTip(), f"{ways.part_names(side)['start']} third: click to leave it out")

    def test_changes_keep_the_same_row_controls_and_do_not_hide_the_ledger(self):
        self.layout()
        row = self.row()
        controls = list(row.chips.values()) + list(row.corner_buttons.values())
        with mock.patch("motion.set_hidden", wraps=__import__("motion").set_hidden) as hidden:
            press(row.contact().segments["middle"])
            press(self.chip("left"))
            press(self.corner("top_left"))
        self.assertIs(self.row(), row)
        self.assertEqual(controls, list(row.chips.values()) + list(row.corner_buttons.values()))
        self.assertFalse(self.window.crossing_ledger.isHidden())
        self.assertFalse(any(call.args[0] in (row, self.window.crossing_ledger) for call in hidden.call_args_list))

    def test_accessibility_table_rows_columns_chips_corners_and_segments_are_named_buttons(self):
        self.layout("top")
        ledger, row = self.window.crossing_ledger, self.row()
        self.assertEqual(ledger.accessibilityRole(), AppKit.NSAccessibilityTableRole)
        self.assertEqual(row.accessibilityRole(), AppKit.NSAccessibilityRowRole)
        self.assertEqual(ledger.accessibilityRows(), [row])
        self.assertEqual(len(ledger.accessibilityColumns()), 5)
        self.assertEqual([header.text for header in ledger.headers], ["Machine", "Edges", "Corners", "Notch", "Keys"])
        self.assertEqual(row.accessibilityLabel(), "Bee")
        segment = row.contact().segments["start"]
        self.assertEqual(segment.accessibilityRole(), AppKit.NSAccessibilityButtonRole)
        self.assertEqual(segment.accessibilityLabel(), "Top edge, left third, on")
        press(segment)
        self.assertEqual(segment.accessibilityLabel(), "Top edge, left third, off")
        chip = self.chip("left")
        self.assertEqual(chip.accessibilityRole(), AppKit.NSAccessibilityButtonRole)
        self.assertTrue(chip.isAccessibilityElement())
        self.assertEqual(chip.accessibilityLabel(), "Left edge, wraps round, off")
        self.assertEqual(chip.accessibilityValue(), "off")
        press(chip)
        self.assertEqual(chip.accessibilityLabel(), "Left edge, wraps round, on")
        self.assertEqual(chip.accessibilityValue(), "on")
        corner = self.corner("top_left")
        self.assertEqual(corner.accessibilityRole(), AppKit.NSAccessibilityButtonRole)
        self.assertEqual(corner.accessibilityLabel(), "Top left corner, off")
        press(corner)
        self.assertEqual(corner.accessibilityLabel(), "Top left corner, on")
        self.assertEqual(corner.accessibilityValue(), "on")
        self.assertEqual(row.contact().accessibilityLabel(), "Top edge, touches Bee, on")
        press(row.contact())
        self.assertEqual(row.contact().accessibilityLabel(), "Top edge, touches Bee, off")

    def test_inline_rename_cancel_and_save_use_the_same_row(self):
        self.layout()
        row = self.row()
        press(row.rename)
        self.assertTrue(row.renaming)
        self.assertEqual(row.rename_hint.text, "Return saves, Escape cancels")
        row.name_field.setStringValue_("Cancelled")
        row.finish_rename(False)
        self.assertEqual(row.name.text, "Bee")
        press(row.rename)
        row.name_field.setStringValue_("Studio PC")
        row.finish_rename(True)
        self.assertIs(self.row(), row)
        self.assertEqual(row.name.text, "Studio PC")

    def test_inline_rename_scope_stays_inside_machine_cell_before_the_next_block(self):
        self.layout()
        self.controller.cfg.same_on_both = False
        row = self.row()
        press(row.rename)
        for width in (399, 687):
            self.window.crossing_ledger.setFrameSize_((width, 700))
            self.assertFalse(row.scope.view.isHidden())
            scope = row.scope.view.frame()
            self.assertLessEqual(scope.origin.y + scope.size.height, row.cells["machine"].frame().size.height)
