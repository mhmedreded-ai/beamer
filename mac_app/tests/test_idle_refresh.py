import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from types import SimpleNamespace

import kvm_bridge_app as app
import settings_store
from mac_app.tests.test_window_geometry import offscreen_control_window


class IdleRefreshTests(unittest.TestCase):
    def setUp(self):
        self.window = offscreen_control_window()
        self.addCleanup(self.window.appearance_watch.stop)
        for name in ('accessibility_granted', 'input_monitoring_granted'):
            patch = mock.patch.object(app, name, return_value=True)
            patch.start()
            self.addCleanup(patch.stop)

    def test_unchanged_tick_reads_no_settings_and_rebuilds_no_window(self):
        self.window.refresh()
        with mock.patch.object(self.window.settings_store, 'load_settings') as read, \
                mock.patch.object(self.window, '_show_same') as same, \
                mock.patch.object(self.window, '_show_arrangement') as arrangement, \
                mock.patch.object(self.window.panel, '_build_rows') as rows:
            self.window.refresh()
        read.assert_not_called()
        same.assert_not_called()
        arrangement.assert_not_called()
        rows.assert_not_called()

    def test_mutated_settings_and_live_status_are_seen_next_tick(self):
        self.window.refresh()
        with mock.patch.object(self.window, '_show_arrangement', wraps=self.window._show_arrangement) as arrangement:
            self.window.settings_store.current()['name'] = 'Changed remotely'
            self.window.refresh()
            arrangement.assert_called_once()
        self.window.controller.input_error = 'Synthetic capture error'
        self.window.refresh()
        self.assertIn('Synthetic capture error', self.window.capture_status.text)

    def test_unchanged_map_inputs_do_not_redraw_even_when_status_changes(self):
        self.window.refresh()
        with mock.patch.object(self.window.atlas_canvas, '_draw') as draw:
            self.window.controller.input_error = 'Capture state changed'
            self.window.refresh()
        draw.assert_not_called()

    def test_hidden_tray_tick_skips_window_and_keeps_capture_retry(self):
        tray = SimpleNamespace(controller=self.window.controller, control_window=self.window,
                               windows_input=mock.Mock(), gesture_overlay=mock.Mock(),
                               measure_notch=mock.Mock(), set_menu_bar_symbol=mock.Mock(),
                               toggle_item=app._tray_item('toggle'), pause_item=app._tray_item('pause'),
                               _machine_items=mock.Mock(), _profile_items=mock.Mock(), _direction_items=mock.Mock(),
                               _refresh_tray_state=mock.Mock())
        with mock.patch.object(self.window.window, 'isVisible', return_value=False), \
                mock.patch.object(self.window, 'refresh') as refresh, \
                mock.patch.object(self.window, 'retry_capture') as retry, \
                mock.patch.object(self.window.settings_store, 'load_settings') as read:
            app.TrayApp.refresh_status(tray, None)
        refresh.assert_not_called()
        read.assert_not_called()
        retry.assert_called_once_with()

    def test_unchanged_menu_glyph_is_not_rebuilt(self):
        tray = SimpleNamespace(_symbol='local', _nsapp=None)
        with mock.patch.object(app, 'menu_bar_glyph') as glyph:
            app.TrayApp.set_menu_bar_symbol(tray, 'local')
            glyph.assert_not_called()
            app.TrayApp.set_menu_bar_symbol(tray, 'held')
            app.TrayApp.set_menu_bar_symbol(tray, 'held')
            glyph.assert_called_once_with('held')


class CachedSettingsTests(unittest.TestCase):
    def test_unchanged_load_does_not_read_but_atomic_replacement_does(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'idle-data.json'
            store = settings_store.SettingsStore(path)
            store.save_settings(store._complete(store._fresh()))
            store.load()
            with mock.patch.object(store, '_read', wraps=store._read) as read:
                store.load()
                read.assert_not_called()
                changed = copy.deepcopy(store.current())
                changed['name'] = 'New remote name'
                replacement = path.with_suffix('.replacement')
                replacement.write_text(json.dumps(changed))
                replacement.replace(path)
                self.assertEqual(store.load_settings()['name'], 'New remote name')
                read.assert_called_once()
