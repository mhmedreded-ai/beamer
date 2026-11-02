"""Show in Dock and Show in menu bar through the real window, tray and launch code: which activation policy each moment
asks for. NSApp's calls are recorded rather than made, so no Dock icon or window appears."""

import json
import logging
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import kvm_bridge_app  # noqa: E402
import settings_store  # noqa: E402
from bridge_fakes import PAIRED_TOKEN  # noqa: E402
from fake_link import FakeLink  # noqa: E402
from test_same_on_both import PEER, pc_message  # noqa: E402
from wake import WakingController  # noqa: E402

AppKit = kvm_bridge_app.AppKit
REGULAR, ACCESSORY = AppKit.NSApplicationActivationPolicyRegular, AppKit.NSApplicationActivationPolicyAccessory


class DockWindowTests(unittest.TestCase):
    def test_menu_bar_toggle_changes_the_saved_dock_choice_and_reflects_the_tick(self):
        self.tray.control_window = self.window
        self.tray.dock_item = types.SimpleNamespace(state=True)
        self.tray.apply_activation_policy(window_open=False)
        kvm_bridge_app.TrayApp.toggle_dock(self.tray, None)
        self.assertFalse(self.store.current()["show_in_dock"])
        self.assertFalse(self.tray.dock_item.state)
        kvm_bridge_app.TrayApp.toggle_dock(self.tray, None)
        self.assertTrue(self.store.current()["show_in_dock"])
        self.assertTrue(self.tray.dock_item.state)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = Path(self.directory.name) / "config.json"
        # A settings file from before the switch existed: no show_in_dock in it.
        raw = settings_store.config_to_raw(settings_store.editable_default_config())
        raw.update(host="192.0.2.20", auth_token=PAIRED_TOKEN)
        path.write_text(json.dumps(raw))
        self.store = settings_store.SettingsStore(path)
        logger = logging.getLogger("test-dock-window")
        with mock.patch.object(kvm_bridge_app, "accessibility_granted", return_value=True), \
                mock.patch.object(kvm_bridge_app, "input_monitoring_granted", return_value=True):
            self.window = kvm_bridge_app.ControlWindow.alloc().initWithController_settingsStore_logger_(
                WakingController(self.store.load(), logger=logger, link_factory=FakeLink, book=self.store.book()),
                self.store, logger)
        self.addCleanup(self.window.appearance_watch.stop)
        self.sent = []
        self.window.controller.send_settings = lambda data, source=None: self.sent.append(data) or True
        self.policies = []
        self.events = []
        app = mock.Mock()
        app.setActivationPolicy_.side_effect = lambda policy: self.policies.append(policy) or self.events.append(policy)
        app.activateIgnoringOtherApps_.side_effect = lambda _flag: self.events.append("activate")
        patch = mock.patch.object(AppKit, "NSApp", app)
        patch.start()
        self.addCleanup(patch.stop)
        self.visible = False
        self.status_item = mock.Mock()
        self.tray = types.SimpleNamespace(
            settings_store=self.store, hidden=False, notices=mock.Mock(), status_item_view=self.status_item,
            control_window=types.SimpleNamespace(window=types.SimpleNamespace(isVisible=lambda: self.visible)))
        self.tray.apply_activation_policy = lambda window_open=None: kvm_bridge_app.TrayApp.apply_activation_policy(
            self.tray, window_open)
        self.window.activation_policy_handler = self.tray.apply_activation_policy

    def show(self):
        """ControlWindow.show with the window itself stood in for, so nothing is put on screen."""
        with mock.patch.object(self.window, "window", mock.Mock()), \
                mock.patch.object(self.window, "_select_page"), mock.patch.object(self.window, "refresh"), \
                mock.patch.object(self.window, "_run_previews"):
            self.window.show()
        self.visible = True

    def close(self):
        with mock.patch.object(self.window, "_remember_normal_frame"):
            self.window.windowWillClose_(None)
        self.visible = False

    def test_an_existing_settings_file_shows_beamer_in_the_dock(self):
        self.assertTrue(self.store.current()["show_in_dock"])
        self.assertTrue(self.window.dock_switch.value)
        self.tray.apply_activation_policy(window_open=False)
        self.assertEqual(self.policies, [REGULAR])

    def test_turned_off_it_stays_while_the_window_is_open_and_goes_when_it_closes(self):
        self.show()
        self.window._set_show_in_dock(False)
        self.assertFalse(type(self.store)(self.store.path).current()["show_in_dock"])
        self.assertEqual(self.policies[-1], REGULAR)
        self.close()
        self.assertEqual(self.policies[-1], ACCESSORY)

    def test_reopened_from_the_menu_bar_it_comes_back_before_taking_focus_and_goes_again(self):
        self.window._set_show_in_dock(False)
        self.assertEqual(self.policies[-1], ACCESSORY)
        self.events.clear()
        self.show()
        # Regular first: an Accessory app cannot become the active app the window needs.
        self.assertEqual(self.events, [REGULAR, "activate"])
        self.close()
        self.assertEqual(self.policies[-1], ACCESSORY)

    def test_a_background_login_launch_with_it_off_stays_out_of_the_dock(self):
        self.window._set_show_in_dock(False)
        self.policies.clear()
        watch = kvm_bridge_app._LaunchWatch.alloc().initWithTray_(self.tray)
        self.addCleanup(AppKit.NSNotificationCenter.defaultCenter().removeObserver_, watch)
        watch.launched_(types.SimpleNamespace(userInfo=lambda: {"NSApplicationLaunchIsDefaultLaunchKey": False}))
        self.assertTrue(self.tray.hidden)
        self.assertEqual(self.policies, [ACCESSORY])

    def test_same_on_all_machines_never_changes_this_macs_choice(self):
        self.window._set_show_in_dock(False)
        # message_data never carries it, so it is put in by hand where a peer could put it.
        data = pc_message(True, 100, by=PEER)
        for section in (data, data["crossing"], data["design"]):
            section["show_in_dock"] = True
        self.window.apply_same(data, peer=PEER)
        self.assertFalse(type(self.store)(self.store.path).current()["show_in_dock"])
        self.window._set_same(True)
        self.assertTrue(self.sent)
        self.assertNotIn("show_in_dock", json.dumps(self.sent))


    def saved(self):
        return type(self.store)(self.store.path).current()

    def test_an_existing_settings_file_shows_beamer_in_the_menu_bar(self):
        self.assertTrue(self.saved()["show_in_tray"])
        self.assertTrue(self.window.tray_switch.value)
        self.tray.apply_activation_policy(window_open=False)
        self.status_item.setVisible_.assert_called_with(True)

    def test_turned_off_the_menu_bar_item_goes_and_the_choice_is_kept(self):
        self.window._set_show_in_tray(False)
        self.assertFalse(self.saved()["show_in_tray"])
        self.assertTrue(self.saved()["show_in_dock"])
        self.status_item.setVisible_.assert_called_with(False)
        self.assertEqual(self.policies[-1], REGULAR)

    def test_the_menu_bar_and_the_dock_are_never_both_off(self):
        self.window._set_show_in_dock(False)
        self.window._set_show_in_tray(False)
        self.assertTrue(self.saved()["show_in_tray"])
        self.assertTrue(self.window.tray_switch.value)
        self.window._set_show_in_dock(True)
        self.window._set_show_in_tray(False)
        self.window._set_show_in_dock(False)
        self.assertTrue(self.saved()["show_in_dock"])
        self.assertTrue(self.window.dock_switch.value)

    def test_the_switch_that_would_hide_the_last_one_is_dimmed(self):
        self.assertTrue(self.window.dock_switch.enabled and self.window.tray_switch.enabled)
        self.window._set_show_in_dock(False)
        self.assertFalse(self.window.tray_switch.enabled)
        self.assertTrue(self.window.tray_switch.value)
        self.window._set_show_in_dock(True)
        self.window._set_show_in_tray(False)
        self.assertFalse(self.window.dock_switch.enabled)
        self.assertTrue(self.window.dock_switch.value)

    def test_a_settings_file_with_both_off_still_shows_the_menu_bar(self):
        with self.store.lock:
            settings = self.store.current()
            settings.update(show_in_dock=False, show_in_tray=False)
            self.store.save_settings(settings)
        self.window._reflect_visibility()
        self.assertTrue(self.window.tray_switch.value)
        self.tray.apply_activation_policy(window_open=False)
        self.status_item.setVisible_.assert_called_with(True)
        self.assertEqual(self.policies[-1], ACCESSORY)

    def test_turning_the_dock_on_from_a_both_off_file_keeps_the_menu_bar_it_showed(self):
        with self.store.lock:
            settings = self.store.current()
            settings.update(show_in_dock=False, show_in_tray=False)
            self.store.save_settings(settings)
        self.window._set_show_in_dock(True)
        self.assertTrue(self.saved()["show_in_tray"])
        self.assertTrue(self.saved()["show_in_dock"])

    def test_with_the_menu_bar_off_a_second_launch_or_a_dock_click_raises_the_window(self):
        self.window._set_show_in_tray(False)
        self.assertEqual(self.policies[-1], REGULAR)
        # TrayApp.open_window is what the second-launch socket calls; the Dock click is the delegate.
        self.tray.control_window.show = self.show
        kvm_bridge_app.TrayApp.open_window(self.tray, None)
        self.assertTrue(self.visible)
        self.close()
        self.assertEqual(self.policies[-1], REGULAR)
        with mock.patch.object(kvm_bridge_app, "_reopen_window", self.show):
            kvm_bridge_app.applicationShouldHandleReopen_hasVisibleWindows_(None, None, False)
        self.assertTrue(self.visible)

    def test_same_on_all_machines_never_changes_this_macs_menu_bar_choice(self):
        self.window._set_show_in_tray(False)
        data = pc_message(True, 100, by=PEER)
        for section in (data, data["crossing"], data["design"]):
            section["show_in_tray"] = True
        self.window.apply_same(data, peer=PEER)
        self.assertFalse(self.saved()["show_in_tray"])
        self.window._set_same(True)
        self.assertNotIn("show_in_tray", json.dumps(self.sent))


if __name__ == "__main__":
    unittest.main()
