"""Crossing profiles in the native page, built offscreen with synthetic desks.

Run this module in its own process on macOS. It imports AppKit but starts no
links, event taps or input capture, and never orders a window onto the screen.
"""

import copy
import json
import unittest
from unittest import mock

from test_crossing_page_machines import BEE, SEA, Page, kvm_bridge_app, press, rumps_module, token
from core import crossing_state, profiles, protocol, settings_sync, ways


def click(button):
    view = getattr(button, "view", button)
    if hasattr(view, "_ledger_target"):
        press(view)
    else:
        view.performClick_(None)


class ProfilePage(Page):
    def desk(self, *, enabled=False, older=False):
        window = self.build([
            (BEE, token(100), "Bee", {"side": "right"}),
            (SEA, token(150), "Sea", {"side": "left"}),
        ])
        self.own = self.store.current()["machine_id"]
        settings = copy.deepcopy(self.store.current())
        settings["name"] = "Studio"
        settings["layout"] = {
            self.own: dict(x=0, y=0, w=160, h=100),
            BEE: dict(x=160, y=0, w=160, h=100),
            SEA: dict(x=-160, y=0, w=160, h=100),
        }
        for peer in settings["peers"]:
            peer["ways"] = dict(edge=True, thirds=list(ways.PARTS), edges=[],
                                corner=False, notch=False)
        settings["zones"] = [{"peer": BEE, "kind": "edge"}, {"peer": SEA, "kind": "edge"}]
        for key in ("crossing_own", "crossing_desk"):
            settings.pop(key, None)
        settings, _ = profiles.before_load(settings, now=1_700_000_000)
        settings, _ = profiles.after_load(settings, platform="mac", capable={BEE, SEA},
                                         now=1_700_000_001)
        if enabled:
            settings = crossing_state.set_same_ways(
                settings, True, platform="mac", capable={BEE, SEA}, now=1_700_000_002)["settings"]
        desk = settings["crossing_desk"]
        pid = desk["active"]["value"]
        for machine, target in ((BEE, self.own), (SEA, BEE)):
            row = profiles.canonical_row({target: {"ways": {
                "edge": True, "thirds": list(ways.PARTS), "edges": [],
                "corner": False, "notch": False,
            }, "notch_return": False}})
            desk["profiles"][pid]["rows"][machine] = dict(
                value=row, set_at=1_700_000_003, by=machine)
            desk["status"][machine] = dict(set_at=1_700_000_004, by=machine, value={
                "platform": "windows", "app": "1.5.5", "in_force": "desk",
                "profile": pid, "row": [1_700_000_003, machine], "waiting": [],
            })
        self.store.save_settings(settings)
        self.controller.cfg = self.store.load()
        self.controller.crossing_capable = mock.Mock(return_value={self.own, BEE, SEA})
        window.windows_input.server.caps_of.side_effect = lambda ident: (
            {"layout"} if older and ident == protocol.read_id(BEE)
            else {"layout", "profiles"})
        window._show_arrangement()
        return window

    def row_registers(self):
        desk = self.store.current()["crossing_desk"]
        return copy.deepcopy(desk["profiles"][desk["active"]["value"]]["rows"])

    def texts(self, view):
        result = []
        if hasattr(view, "stringValue"):
            result.append(view.stringValue())
        for child in view.subviews():
            result.extend(self.texts(child))
        return result

    def menu_tray(self):
        tray = mock.Mock()
        tray.controller = self.controller
        tray.settings_store = self.store
        tray.control_window = self.window
        tray.menu = rumps_module.Menu()
        tray.toggle_item = kvm_bridge_app.rumps.MenuItem(kvm_bridge_app.TOGGLE_ITEM)
        tray.menu.add(tray.toggle_item)
        tray.menu.add(kvm_bridge_app.rumps.MenuItem("Pause crossing"))
        tray._send_keys = []
        tray._profile_menu_key = None
        tray.profile_item = None
        return tray

    def test_same_ways_off_keeps_local_ledger_without_profile_tabs(self):
        window = self.desk()
        self.assertFalse(window.ways_switch.value)
        self.assertTrue(window.profile_tabs.isHidden())
        self.assertEqual(set(window.crossing_ledger.rows), {BEE, SEA})
        self.assertEqual(window.atlas_canvas.own_id, self.own)
        self.assertFalse(window.crossing_ledger.rows[BEE].cells["keys"].isHidden())
        before = copy.deepcopy(self.store.current())
        window._atlas_selected(BEE)
        self.assertEqual(window.viewed_machine, self.own)
        self.assertEqual(window.chosen_peer, BEE)
        self.assertEqual(self.store.current(), before)

    def test_switch_enables_profile_tabs_and_desk_words_without_moving_tiles(self):
        window = self.desk()
        tiles = copy.deepcopy(window.atlas_canvas.tiles)
        with mock.patch.object(self.controller, "crossing_event", wraps=self.controller.crossing_event) as event:
            window.ways_switch._toggle()
        self.assertIs(event.call_args.args[0], crossing_state.set_same_ways)
        self.assertTrue(profiles.same_ways_on(self.store.current()))
        self.assertFalse(window.profile_tabs.isHidden())
        self.assertEqual(window.atlas_canvas.tiles, tiles)
        self.assertIn(profiles.DESK_ID, window.profile_buttons)
        for machine in (self.own, BEE, SEA):
            self.assertEqual(window.atlas_canvas.tile_words[machine], "Desk")

    def test_picking_another_machine_uses_its_row_and_hides_local_controls(self):
        window = self.desk(enabled=True)
        before = copy.deepcopy(self.store.current())
        with mock.patch.object(profiles, "view_settings", wraps=profiles.view_settings) as view:
            window._atlas_selected(BEE)
        self.assertTrue(any(call.args[1] == BEE for call in view.call_args_list))
        self.assertEqual(window.viewed_machine, BEE)
        self.assertEqual(window.atlas_canvas.own_id, BEE)
        self.assertEqual(set(window.crossing_ledger.rows), {self.own, SEA})
        self.assertEqual(window.ledger_heading.text, "Bee's ways")
        self.assertIn("Editing Bee's ways", window.edit_notice.text)
        self.assertIn("Jump keys and permissions are set on Bee", window.remote_permissions_note.text)
        self.assertEqual(window.remote_changes_note.text, "Changes go to Bee at once.")
        for row in window.crossing_ledger.rows.values():
            self.assertTrue(row.cells["keys"].isHidden())
            self.assertTrue(row.rename.isHidden())
            self.assertTrue(row.leave.isHidden())
        for module in (window.pointer_module, window.resistance_module, window.shortcut_module):
            self.assertTrue(module.view.isHidden())
        self.assertEqual(self.store.current(), before)

    def test_same_ways_shelves_left_out_machine_in_picker_and_remote_layout(self):
        window = self.desk()
        own = self.own
        viewed, shelved = BEE, SEA
        settings = self.store.current()
        next(peer for peer in settings["peers"] if peer["id"] == shelved).update(
            left_out=True)
        settings["layout"][shelved] = copy.deepcopy(settings["layout"][viewed])
        self.store.save_settings(settings)
        window.controller.cfg = self.store.load()
        window.ways_switch._toggle()
        self.assertTrue(profiles.same_ways_on(self.store.current()))

        window._atlas_selected(shelved)
        self.assertEqual(window.viewed_machine, own)
        window._atlas_selected(viewed)
        viewed_settings = window._view_settings()
        self.assertEqual(viewed_settings["machine_id"], viewed)
        states = ways.compile_layout(viewed_settings, viewed_settings["layout"], platform="pc")["tiles"]
        self.assertEqual(states[shelved]["state"], "dormant")
        self.assertNotEqual(states.get(own, {}).get("state"), "overlap")
        self.assertNotEqual(states.get(viewed, {}).get("state"), "overlap")
        self.assertNotIn(shelved, window.atlas_canvas.tiles)

    def test_remote_ledger_edit_calls_edit_other_and_changes_only_that_desk_row(self):
        window = self.desk(enabled=True)
        window._atlas_selected(BEE)
        before = self.row_registers()
        own_row = copy.deepcopy(self.store.current()["crossing_own"])
        with mock.patch.object(self.controller, "crossing_event", wraps=self.controller.crossing_event) as event:
            press(window.crossing_ledger.rows[self.own].contact())
        self.assertIs(event.call_args.args[0], crossing_state.edit_other)
        self.assertEqual(event.call_args.args[1:3], (BEE, self.own))
        after = self.row_registers()
        self.assertNotEqual(before[BEE], after[BEE])
        self.assertEqual(before[self.own], after[self.own])
        self.assertEqual(before[SEA], after[SEA])
        self.assertEqual(self.store.current()["crossing_own"]["row"], own_row["row"])
        self.assertFalse(after[BEE]["value"][self.own]["ways"]["edge"])

    def test_back_button_restores_own_machine_and_local_controls(self):
        window = self.desk(enabled=True)
        window._atlas_selected(BEE)
        click(window.back_ways_button)
        self.assertEqual(window.viewed_machine, self.own)
        self.assertEqual(window.atlas_canvas.own_id, self.own)
        self.assertFalse(window.crossing_ledger.rows[BEE].cells["keys"].isHidden())
        for module in (window.pointer_module, window.resistance_module, window.shortcut_module):
            self.assertFalse(module.view.isHidden())

    def test_switch_off_from_remote_view_restores_todays_local_ledger(self):
        window = self.desk(enabled=True)
        window._atlas_selected(BEE)
        window.ways_switch._toggle()
        self.assertFalse(profiles.same_ways_on(self.store.current()))
        self.assertEqual(window.viewed_machine, self.own)
        self.assertTrue(window.profile_tabs.isHidden())
        self.assertEqual(set(window.crossing_ledger.rows), {BEE, SEA})
        self.assertFalse(window.crossing_ledger.rows[BEE].cells["keys"].isHidden())

    def test_map_return_and_space_pick_a_machine_without_changing_its_layout(self):
        window = self.desk(enabled=True)
        tiles = copy.deepcopy(window.atlas_canvas.tiles)
        for code, machine in ((36, BEE), (49, SEA)):
            with self.subTest(key=code):
                window.atlas_canvas.selected = machine
                event = mock.Mock()
                event.keyCode.return_value = code
                window.atlas_canvas.keyDown_(event)
                self.assertEqual(window.viewed_machine, machine)
                self.assertEqual(window.atlas_canvas.tiles, tiles)

    def test_picker_tab_does_not_change_the_viewed_machine_or_settings(self):
        window = self.desk(enabled=True)
        before = copy.deepcopy(self.store.current())
        event = mock.Mock()
        event.keyCode.return_value = 48
        event.modifierFlags.return_value = 0
        window.atlas_canvas.keyDown_(event)
        self.assertEqual(window.viewed_machine, self.own)
        self.assertEqual(self.store.current(), before)

    def test_remote_header_order_and_inline_back_link(self):
        window = self.desk(enabled=True)
        window._atlas_selected(BEE)
        self.assertEqual(window.edit_notice.text, "Editing Bee's ways ·")
        self.assertEqual(window.back_ways_button.title(), "Back to This Mac")
        self.assertEqual(list(window.edit_header.arrangedSubviews()),
                         [window.edit_notice.view, window.back_ways_button])
        body = list(window.ways_ledger_module.body.arrangedSubviews())
        ordered = [window.edit_header, window.profile_status.view,
                   window.remote_changes_note.view, window.remote_permissions_note.view]
        self.assertEqual([body.index(view) for view in ordered],
                         sorted(body.index(view) for view in ordered))

    def test_home_shortcut_opens_overviews_same_shortcut_row(self):
        window = self.desk(enabled=True)
        window._atlas_selected(BEE)
        with mock.patch.object(window.window, "makeFirstResponder_") as focus:
            click(window.home_shortcut_button)
        self.assertEqual(window.page, "overview")
        focus.assert_called_with(window.same_checks["shortcut"])

    def test_home_shortcut_formats_only_the_key_in_mono_in_translated_order(self):
        window = self.desk(enabled=True)
        translate = kvm_bridge_app.tr
        def key_first(message):
            if message == "Shortcut: {style} {key} ·":
                return "{key} : {style} ·"
            return translate(message)
        with mock.patch.object(kvm_bridge_app, "tr", side_effect=key_first), \
                mock.patch.object(kvm_bridge_app.widgets, "attributed",
                                  wraps=kvm_bridge_app.widgets.attributed) as attributed:
            window._show_profiles()
        key = kvm_bridge_app.widgets.key_title(self.controller.cfg.trigger_key)
        mono = [call for call in attributed.call_args_list if call.kwargs.get("mono")]
        self.assertEqual(len(mono), 1)
        self.assertEqual(mono[0].args[0], key)
        self.assertTrue(window.home_shortcut_note.text.startswith(key + " : "))
        self.assertEqual(window.home_shortcut_note.view.attributedStringValue().string(),
                         window.home_shortcut_note.text)

    def test_profiles_actions_are_visible_and_protect_active_and_last_profile(self):
        window = self.desk(enabled=True)
        self.assertFalse(window.profile_actions.isHidden())
        self.assertFalse(window.delete_profile_button.isEnabled())
        with mock.patch.object(window, "_profile_name") as prompt:
            click(window.new_profile_button)
            prompt.assert_called_once_with()
        with mock.patch.object(window, "_profile_name") as prompt:
            click(window.rename_profile_button)
            prompt.assert_called_once_with(profiles.DESK_ID)
        window._profile_event(crossing_state.new_profile, "Sofa")
        self.assertFalse(window.profile_switch_note.view.isHidden())
        # Delete… offers every profile but the active one; the menu is built, not popped up.
        self.assertTrue(window.delete_profile_button.isEnabled())
        offered = [item.title() for item in window._delete_profile_menu().itemArray()]
        self.assertEqual(offered, ["Sofa"])
        window.ways_switch._toggle()
        self.assertTrue(window.profile_actions.isHidden())
        self.assertTrue(window.profile_switch_note.view.isHidden())

    def test_off_intro_and_on_hint_use_their_own_instructions(self):
        window = self.desk()
        self.assertTrue(window.crossing_intro.text.startswith("Drag a machine on the map"))
        self.assertEqual(window.ways_off_note.text,
                         "Set every machine's ways from this screen. Turning it on changes nothing until you do; each machine's own ways are kept.")
        window.ways_switch._toggle()
        self.assertTrue(window.crossing_intro.text.startswith("Click a machine on the map"))
        self.assertFalse(window.atlas_canvas_hint.view.isHidden())
        self.assertEqual(window.atlas_canvas_hint.text,
                         "Click a machine to set its ways · Tab and Return pick one from the keyboard")

    def test_empty_archive_hides_toggle_and_own_band_matches_ledger_width(self):
        window = self.desk(enabled=True)
        settings = copy.deepcopy(self.store.current())
        settings["crossing_own"]["saved"] = []
        self.store.save_settings(settings)
        window._show_profiles()
        self.assertTrue(window.saved_toggle.isHidden())
        window._select_page("crossing")
        window.window.contentView().layoutSubtreeIfNeeded()
        self.assertGreater(window.ways_ledger_module.view.frame().size.width, 0)
        self.assertAlmostEqual(window.own_ways_module.view.frame().size.width,
                               window.ways_ledger_module.view.frame().size.width, delta=1)

    def test_open_archive_fills_own_band_and_actions_align_at_the_right(self):
        window = self.desk(enabled=True)
        click(window.own_ways_button)
        self.assertTrue(profiles.saved_list(self.store.current()))
        click(window.saved_toggle)
        window._select_page("crossing")
        window.window.contentView().layoutSubtreeIfNeeded()
        for pair in window._earlier_pairs:
            pair.update_orientation(700)
        window.window.contentView().layoutSubtreeIfNeeded()
        ledger_width = window.ways_ledger_module.view.frame().size.width
        self.assertGreater(ledger_width, 0)
        self.assertAlmostEqual(window.own_ways_module.view.frame().size.width, ledger_width, delta=1)
        for pair in window._earlier_pairs:
            frame = pair.second.frame()
            self.assertAlmostEqual(frame.origin.x + frame.size.width, pair.view.bounds().size.width, delta=1)
        for restore, delete in window.earlier_buttons.values():
            self.assertFalse(restore.isBordered())
            self.assertFalse(delete.isBordered())

    def test_profile_switch_hugs_and_tabs_mark_active_without_a_filled_add_button(self):
        window = self.desk(enabled=True)
        window._profile_event(crossing_state.new_profile, "Sofa")
        window._select_page("crossing")
        window.profile_top.update_orientation(700)
        window.window.contentView().layoutSubtreeIfNeeded()
        switch = window.ways_switch
        words, track = switch.words.view.frame(), switch.track.frame()
        # Label and toggle sit together (the Switch's own spacing), not split across the header.
        self.assertLessEqual(track.origin.x - words.origin.x - words.size.width, 16)
        self.assertFalse(window.add_profile_button.isBordered())
        self.assertLessEqual(window.add_profile_button.frame().size.width, 24)
        for button in window.profile_buttons.values():
            self.assertEqual(button.view.layer().borderWidth(), 1)

    def test_own_override_band_routes_keep_use_and_share_to_their_events(self):
        window = self.desk(enabled=True)
        self.assertFalse(window.own_ways_button.view.isHidden())
        click(window.own_ways_button)
        self.assertTrue(self.store.current()["crossing_own"]["use_own"])
        self.assertFalse(window.use_desk_button.view.isHidden())
        window._write_ways(BEE, edge=False)
        self.assertFalse(window.share_ways_button.view.isHidden())
        own_row = copy.deepcopy(self.store.current()["crossing_own"]["row"])
        click(window.share_ways_button)
        self.assertEqual(self.row_registers()[self.own]["value"], own_row)
        click(window.use_desk_button)
        self.assertFalse(self.store.current()["crossing_own"]["use_own"])

    def test_earlier_ways_restore_preserves_saved_choices_and_delete_removes_one(self):
        window = self.desk(enabled=True)
        settings = copy.deepcopy(self.store.current())
        earlier = copy.deepcopy(settings["crossing_own"]["row"])
        earlier[BEE]["ways"]["edge"] = not earlier[BEE]["ways"]["edge"]
        profiles.archive(settings, earlier, "older_version", 1_700_000_010)
        saved_id = profiles.saved_list(settings)[0]["id"]
        self.store.save_settings(settings)
        window._show_profiles()
        click(window.saved_toggle)
        self.assertFalse(window.earlier_host.isHidden())
        self.assertEqual(window.saved_toggle.title(), "Earlier ways ▴")
        self.assertIn("From an older Beamer", " ".join(self.texts(window.earlier_host)))
        restore, _delete = window.earlier_buttons[saved_id]
        click(restore)
        self.assertTrue(self.store.current()["crossing_own"]["use_own"])
        self.assertEqual(self.store.current()["crossing_own"]["row"], profiles.canonical_row(earlier))
        self.assertIn(saved_id, [item["id"] for item in profiles.saved_list(self.store.current())])
        _restore, delete = window.earlier_buttons[saved_id]
        click(delete)
        self.assertNotIn(saved_id, [item["id"] for item in profiles.saved_list(self.store.current())])

    def test_way_home_switch_calls_set_shake_for_the_current_profile(self):
        window = self.desk(enabled=True)
        self.assertTrue(window.shake_switch.value)
        with mock.patch.object(self.controller, "crossing_event", wraps=self.controller.crossing_event) as event:
            window.shake_switch._toggle()
        self.assertIs(event.call_args.args[0], crossing_state.set_shake)
        self.assertFalse(profiles.shake_on(self.store.current()))
        self.assertFalse(window.shake_switch.value)

    def test_way_home_sensitivity_calls_the_profile_event_and_shows_its_value(self):
        window = self.desk(enabled=True)
        with mock.patch.object(self.controller, "crossing_event", wraps=self.controller.crossing_event) as event:
            window._shake_sensitivity_changed(150)
        self.assertIs(event.call_args.args[0], crossing_state.set_shake_sensitivity)
        self.assertEqual(profiles.shake_sensitivity(self.store.current()), 150)
        self.assertEqual(window.shake_sensitivity_figure.text, "150")

    def test_way_home_sensitivity_arrows_save_ten_percent_or_one_with_option(self):
        window = self.desk(enabled=True)
        window.shake_sensitivity.nudge(1)
        self.assertEqual(profiles.shake_sensitivity(self.store.current()), 110)
        window.shake_sensitivity.nudge(1, fine=True)
        self.assertEqual(profiles.shake_sensitivity(self.store.current()), 111)
        self.assertEqual(window.shake_sensitivity_figure.text, "111")

    def test_no_way_back_warning_names_every_machine_without_an_edge_home(self):
        window = self.desk(enabled=True)
        settings = copy.deepcopy(self.store.current())
        desk = settings["crossing_desk"]
        pid = desk["active"]["value"]
        for machine in (BEE, SEA):
            desk["profiles"][pid]["rows"][machine]["value"] = {}
        self.store.save_settings(settings)
        missing = profiles.no_way_back(self.store.current(), self.own)
        self.assertTrue(missing, "the synthetic desk must exercise a missing way back")
        window._show_profiles()
        for name in missing:
            self.assertIn(f"{name} has no way back to This Mac except the shortcut and a shake.",
                          window.home_warning.text)

    def test_older_machine_is_labelled_read_only_and_ledger_cannot_write(self):
        window = self.desk(enabled=True, older=True)
        self.assertEqual(window.atlas_canvas.tile_words[BEE], "Older")
        window._atlas_selected(BEE)
        self.assertIn("Read only", window.profile_status.text)
        before = copy.deepcopy(self.store.current())
        with mock.patch.object(self.controller, "crossing_event", wraps=self.controller.crossing_event) as event:
            window._write_ways(self.own, edge=False)
        event.assert_not_called()
        self.assertEqual(self.store.current(), before)
        for row in window.crossing_ledger.rows.values():
            self.assertTrue(all(not chip.enabled for chip in row.chips.values()))
            self.assertTrue(all(not button.enabled for button in row.corner_buttons.values()))

    def test_offline_capable_machine_keeps_editable_ways_for_its_next_connection(self):
        window = self.desk(enabled=True)
        window.windows_input.server.links.return_value = [protocol.read_id(SEA)]
        window._show_arrangement()
        self.assertEqual(window.atlas_canvas.tile_words[BEE], "Offline")
        window._atlas_selected(BEE)
        self.assertIn("Offline. It takes these when it next connects", window.profile_status.text)
        before = self.row_registers()[BEE]
        window._write_ways(self.own, edge=False)
        self.assertNotEqual(self.row_registers()[BEE], before)

    def test_disconnected_older_machine_remains_read_only(self):
        window = self.desk(enabled=True, older=True)
        window.windows_input.server.links.return_value = [protocol.read_id(SEA)]
        window._show_arrangement()
        window._atlas_selected(BEE)
        self.assertEqual(window.atlas_canvas.tile_words[BEE], "Older")
        self.assertIn("Read only", window.profile_status.text)
        before = copy.deepcopy(self.store.current())
        window._write_ways(self.own, edge=False)
        self.assertEqual(self.store.current(), before)

    def test_named_profile_tabs_switch_and_removal_preserves_the_active_profile(self):
        window = self.desk(enabled=True)
        window._profile_event(crossing_state.new_profile, "Sofa")
        sofa = next(item["id"] for item in profiles.profile_list(self.store.current()) if item["name"] == "Sofa")
        self.assertIn(sofa, window.profile_buttons)
        self.assertFalse(window.profile_buttons[profiles.DESK_ID].view.menu().itemAtIndex_(1).isEnabled())
        self.assertTrue(window.profile_buttons[sofa].view.menu().itemAtIndex_(1).isEnabled())
        click(window.profile_buttons[sofa])
        self.assertEqual(self.store.current()["crossing_desk"]["active"]["value"], sofa)
        self.assertFalse(window.profile_buttons[sofa].view.menu().itemAtIndex_(1).isEnabled())
        window._profile_event(crossing_state.remove_profile, sofa)
        self.assertIn(sofa, window.profile_buttons)
        click(window.profile_buttons[profiles.DESK_ID])
        window._profile_event(crossing_state.rename_profile, sofa, "Reading")
        self.assertEqual(next(item["name"] for item in profiles.profile_list(self.store.current())
                              if item["id"] == sofa), "Reading")
        window._profile_event(crossing_state.remove_profile, sofa)
        self.assertNotIn(sofa, window.profile_buttons)
        self.assertEqual([item["id"] for item in profiles.profile_list(self.store.current())], [profiles.DESK_ID])

    def test_older_version_load_notice_is_shown_and_consumed_once(self):
        window = self.desk(enabled=True)
        raw = json.loads(self.store.path.read_text())
        peer = next(entry for entry in raw["peers"] if entry["id"] == BEE)
        peer["ways"]["edge"] = not peer["ways"]["edge"]
        self.store.path.write_text(json.dumps(raw))
        self.store.load_settings()
        self.assertIn("older_version", self.store.profile_load_notes)
        window._ways_module()
        self.assertEqual(window.older_ways_notice.text,
                         "This machine kept the ways set while it ran an older Beamer.")
        self.assertFalse(window.older_ways_notice.view.isHidden())
        self.assertFalse(self.store.take_profile_load_note("older_version"))
        self.store.load_settings()
        self.assertFalse(self.store.take_profile_load_note("older_version"))

    def test_menu_profile_checks_active_and_click_switches_the_profile(self):
        window = self.desk(enabled=True)
        window._profile_event(crossing_state.new_profile, "Sofa")
        sofa = next(item["id"] for item in profiles.profile_list(self.store.current()) if item["name"] == "Sofa")
        tray = self.menu_tray()
        kvm_bridge_app.TrayApp._profile_items(tray)
        self.assertIn("Profile", tray.menu)
        children = tray.profile_item
        # Keyed by profile id, so two profiles with one name never share an item; titled by name.
        self.assertEqual({child.title for child in children.values()}, {"Desk", "Sofa"})
        self.assertEqual(children[profiles.DESK_ID].state, 1)
        self.assertEqual(children[sofa].state, 0)
        with mock.patch.object(self.controller, "crossing_event", wraps=self.controller.crossing_event) as event:
            children[sofa].callback(children[sofa])
        self.assertIs(event.call_args.args[0], crossing_state.switch_profile)
        self.assertEqual(event.call_args.args[1], sofa)
        self.assertEqual(self.store.current()["crossing_desk"]["active"]["value"], sofa)
        kvm_bridge_app.TrayApp._profile_items(tray)
        self.assertEqual(tray.profile_item[profiles.DESK_ID].state, 0)
        self.assertEqual(tray.profile_item[sofa].state, 1)

    def test_menu_profile_submenu_is_absent_with_one_profile_or_same_ways_off(self):
        window = self.desk(enabled=True)
        tray = self.menu_tray()
        kvm_bridge_app.TrayApp._profile_items(tray)
        self.assertIsNone(tray.profile_item)
        self.assertNotIn("Profile", tray.menu)
        window._profile_event(crossing_state.new_profile, "Sofa")
        kvm_bridge_app.TrayApp._profile_items(tray)
        self.assertIn("Profile", tray.menu)
        window.ways_switch._toggle()
        kvm_bridge_app.TrayApp._profile_items(tray)
        self.assertIsNone(tray.profile_item)
        self.assertNotIn("Profile", tray.menu)

    def test_desk_frame_dispatches_once_without_echoing_same_settings_or_layout(self):
        window = self.desk(enabled=True)
        frame = profiles.frames(profiles.desk_message(self.store.current()))[0]
        data = settings_sync.desk_message_data({"on": True, "set_at": 1_700_000_020,
                                               "by": BEE}, frame)
        outcome = {"changed": False, "settings": self.store.current()}
        with mock.patch.object(self.controller, "receive_desk", return_value=outcome) as receive, \
                mock.patch.object(window, "_send_same") as send, \
                mock.patch.object(window, "_receive_layout") as layout, \
                mock.patch.object(window, "refresh"):
            window.apply_same(data, BEE)
        receive.assert_called_once()
        self.assertEqual(receive.call_args.args, (data, BEE))
        self.assertTrue(receive.call_args.kwargs["linked"])
        send.assert_not_called()
        layout.assert_not_called()

    def test_desk_full_outcome_is_explained_in_the_existing_page_note(self):
        window = self.desk(enabled=True)
        outcome = {"changed": False, "accepted": False, "reason": "desk_full",
                   "settings": self.store.current()}
        with mock.patch.object(self.controller, "crossing_event", return_value=outcome):
            result = window._profile_event(crossing_state.new_profile, "Sofa")
        self.assertEqual(result["reason"], "desk_full")
        self.assertEqual(window.message_label.text,
                         "The desk is full. Remove an unused profile before adding more ways.")


if __name__ == "__main__":
    unittest.main()
