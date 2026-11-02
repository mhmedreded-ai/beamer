import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dock_visibility


class DockVisibilityTests(unittest.TestCase):
    def test_existing_settings_default_to_showing_beamer_in_the_dock(self):
        settings = dock_visibility.complete_settings({"name": "Mac"})
        self.assertTrue(settings["show_in_dock"])

    def test_hidden_preference_is_preserved(self):
        settings = dock_visibility.complete_settings({"show_in_dock": False})
        self.assertFalse(settings["show_in_dock"])

    def test_setting_rejects_non_boolean_values(self):
        with self.assertRaises(ValueError):
            dock_visibility.validate_settings({"show_in_dock": "yes"})

    def test_accessory_policy_applies_only_when_hidden_and_window_closed(self):
        self.assertEqual(dock_visibility.activation_policy(True, False), "regular")
        self.assertEqual(dock_visibility.activation_policy(False, True), "regular")
        self.assertEqual(dock_visibility.activation_policy(False, False), "accessory")

    def test_the_menu_bar_defaults_on_and_rejects_non_boolean_values(self):
        self.assertTrue(dock_visibility.complete_settings({})["show_in_tray"])
        self.assertFalse(dock_visibility.complete_settings({"show_in_tray": False})["show_in_tray"])
        with self.assertRaises(ValueError):
            dock_visibility.validate_settings({"show_in_dock": True, "show_in_tray": 1})

    def test_the_menu_bar_stays_whenever_the_dock_is_hidden(self):
        self.assertTrue(dock_visibility.menu_bar_visible(True, True))
        self.assertTrue(dock_visibility.menu_bar_visible(False, True))
        self.assertFalse(dock_visibility.menu_bar_visible(True, False))
        self.assertTrue(dock_visibility.menu_bar_visible(False, False))


if __name__ == "__main__":
    unittest.main()
