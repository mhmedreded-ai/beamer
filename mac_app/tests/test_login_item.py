"""The macOS approval path for start at login."""

import unittest
from unittest.mock import Mock, patch
from pathlib import Path

import login_item


class LoginItemTests(unittest.TestCase):
    def test_requires_approval_has_a_clear_status(self):
        self.assertEqual(login_item.status_text(login_item.REQUIRES_APPROVAL), "Requires approval")

    def test_only_an_applications_bundle_is_eligible_for_login(self):
        self.assertTrue(login_item.installed_in_applications("/Applications/Beamer.app"))
        self.assertTrue(login_item.installed_in_applications("/Applications/Utilities/Beamer.app"))
        self.assertTrue(login_item.installed_in_applications(str(Path.home() / "Applications" / "Beamer.app")))
        self.assertFalse(login_item.installed_in_applications("/Downloads/Beamer.app"))
        self.assertFalse(login_item.installed_in_applications("/AppTranslocation/Beamer.app"))

    def test_a_moved_app_can_remove_but_not_add_its_login_item(self):
        path = "/Downloads/Beamer.app"
        self.assertFalse(login_item.can_toggle(login_item.NOT_REGISTERED, path))
        self.assertTrue(login_item.can_toggle(login_item.ENABLED, path))

    def test_non_default_launch_stays_in_the_menu_bar(self):
        self.assertTrue(login_item.is_background_launch(False))
        self.assertFalse(login_item.is_background_launch(True))
        self.assertFalse(login_item.is_background_launch(None))

    def test_opens_login_items_settings(self):
        service = Mock()
        with patch.object(login_item, "SMAppService", service):
            login_item.open_system_settings()
        service.openSystemSettingsLoginItems.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
