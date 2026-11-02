"""Status refreshes retain current controls, not every control they replace."""
import gc
import unittest
import weakref
import tracemalloc
from types import SimpleNamespace
from unittest import mock

import objc

import widgets
from mac_app.tests.test_crossing_page_machines import Page, BEE, SEA, token


class MenuLifetime(unittest.TestCase):
    def test_discarded_menu_releases_its_python_owner(self):
        with objc.autorelease_pool():
            menu = widgets.MenuChoice([("", "None")])
            reference = weakref.ref(menu)
            del menu
        gc.collect()
        self.assertIsNone(reference())

    def test_a_live_menu_still_dispatches_its_selection(self):
        selected = []
        menu = widgets.MenuChoice([("", "None"), ("peer", "Peer")], on_change=selected.append)
        menu.value = "peer"
        menu.target.fire_(menu.popup)
        self.assertEqual(selected, ["peer"])


class IdleControls(unittest.TestCase):
    def test_unchanged_enabled_state_does_not_repaint_choices(self):
        controls = [widgets.Segmented([("a", "A"), ("b", "B")]),
                    widgets.ChoiceTiles([("a", "A", "", widgets.box()), ("b", "B", "", widgets.box())]),
                    widgets.Swatches([(name, name) for name in list(widgets.theme.PALETTES)[:2]])]
        for control in controls:
            control.value = control.values[0] if isinstance(control, widgets.Swatches) else "a"
            control.set_enabled(False)
            control.set_enabled(True)
            with mock.patch.object(widgets, "paint", wraps=widgets.paint) as paint, \
                    mock.patch.object(widgets.theme, "tint", wraps=widgets.theme.tint) as tint:
                for _ in range(20):
                    control.set_enabled(True)
                self.assertEqual(paint.call_count + tint.call_count, 0, type(control).__name__)
            control.set_enabled(False)
            self.assertEqual(control.view.alphaValue(), 0.45)


class FollowRefresh(Page):
    def test_unchanged_ledger_row_does_not_retain_title_attributes(self):
        window = self.build([(BEE, token(100), "Bee")])
        row = window.crossing_ledger.rows[BEE]
        settings = self.store.current()
        entry = settings["peers"][0]
        row.refresh(settings, entry, True)
        tracemalloc.start()
        try:
            gc.collect()
            before = tracemalloc.take_snapshot()
            for _ in range(200):
                with objc.autorelease_pool():
                    row.refresh(settings, entry, True)
            gc.collect()
            after = tracemalloc.take_snapshot()
            retained = sum(stat.size_diff for stat in after.compare_to(before, "filename")
                           if stat.traceback[0].filename == widgets.__file__)
            self.assertLess(retained, 2048)
            row.refresh(settings, {**entry, "left_out": True}, True)
            self.assertEqual(row.leave.title(), "Put back")
        finally:
            tracemalloc.stop()

    def test_unchanged_sidebar_status_does_not_retain_a_string_per_tick(self):
        window = self.build([(BEE, token(100), "Bee")])
        state = SimpleNamespace(led="signal", blink=False, word="Connected", tone="signal")
        window.sidebar.set_link(state, "Bee")
        tracemalloc.start()
        try:
            gc.collect()
            before = tracemalloc.take_snapshot()
            for _ in range(200):
                with objc.autorelease_pool():
                    window.sidebar.set_link(state, "Bee")
            gc.collect()
            after = tracemalloc.take_snapshot()
            retained = sum(stat.size_diff for stat in after.compare_to(before, "filename")
                           if stat.traceback[0].filename == widgets.__file__)
            self.assertLess(retained, 2048)
            self.assertEqual(window.sidebar.header.accessibilityLabel(), "Connected, Bee")
        finally:
            tracemalloc.stop()

    def test_unchanged_ticks_keep_the_same_menu(self):
        window = self.build([(BEE, token(100), "Bee"), (SEA, token(150), "Sea")])
        window._refresh_design_follow()
        menu = window.follow_select
        for _ in range(100):
            with objc.autorelease_pool():
                window._refresh_design_follow()
        self.assertIs(window.follow_select, menu)

    def test_peer_and_selection_changes_reach_the_menu(self):
        window = self.build([(BEE, token(100), "Bee")])
        settings = self.store.current()
        self.store.save_settings({**settings, "peers": [{**settings["peers"][0], "label": "Renamed"}]})
        window.controller.cfg.design_follow_peer = BEE
        window._refresh_design_follow()
        self.assertEqual(window.follow_select.value, BEE)
        self.assertIn((BEE, "Renamed"), window.follow_select.choices)
        self.store.remove_peer(token(100))
        window._refresh_design_follow()
        self.assertEqual(window.follow_select.choices, [("", "None")])

    def test_changed_capabilities_and_arriving_design_update_availability(self):
        window = self.build([(BEE, token(100), "Bee")])
        known = {BEE: frozenset()}
        window._design_peer_caps = lambda: known
        window._refresh_design_follow()
        old = weakref.ref(window.follow_select)
        self.assertFalse(window.follow_select.items[1][1].isEnabled())
        known[BEE] = frozenset({"design_sync"})
        window._design_states[BEE] = {}
        window._refresh_design_follow()
        with objc.autorelease_pool():
            gc.collect()
        self.assertTrue(window.follow_select.items[1][1].isEnabled())
        self.assertIsNone(old())


if __name__ == "__main__":
    unittest.main()
