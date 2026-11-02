"""The Keyboard page's mouse-only switch, without showing the window."""

import unittest
from unittest import mock

from mac_app.tests.test_crossing_page_machines import Page, kvm_bridge_app
from mac_app.settings_store import config_to_raw


class MouseOnlyPageTests(Page):
    def test_load_and_apply_round_trip_through_the_existing_settings_path(self):
        window = self.build([])
        self.addCleanup(window.window.close)
        self.assertFalse(window.mouse_only_box.value)
        raw = config_to_raw(window.controller.cfg)
        raw["mouse_only"] = True
        window._load(raw)
        self.assertTrue(window.mouse_only_box.value)
        with mock.patch.object(window.controller, "apply_settings"):
            kvm_bridge_app.ControlWindow._apply_settings(window)
        self.assertTrue(window.settings_store.load().mouse_only)
        self.assertFalse(window.window.isVisible())


if __name__ == "__main__":
    unittest.main()
