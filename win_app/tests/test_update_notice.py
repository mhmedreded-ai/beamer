"""Qt update notice with synthetic settings; run offscreen from a Claude session."""

import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]

from PySide6.QtWidgets import QApplication
import app_config
import kvm_bridge_win as kvm
import theme
from core import updates


FOUND = updates.Release("1.5.11", "https://github.com/kalkman-code/beamer/releases/tag/v1.5.11")


class UpdateNoticeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.init_fonts()

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "settings.json"
        app_config.write_settings(self.path, app_config.load_settings(self.path))
        for blocker in (
            patch.object(threading.Thread, "start", side_effect=AssertionError("service started")),
            patch.object(kvm.WindowsApplication, "_make_pairing", return_value=None),
            patch.object(kvm.autostart_win, "installed_exe", return_value=None),
            patch.object(kvm.portal, "sandboxed", return_value=False),
            patch("core.locale.is_us_region", return_value=False),
        ):
            blocker.start()
            self.addCleanup(blocker.stop)
        self.window = self.build()

    def build(self):
        window = kvm.WindowsApplication(self.path)
        window.refresh_timer.stop()
        window.full_screen_timer.stop()
        self.addCleanup(window.deleteLater)
        return window

    def test_quiet_patch_is_available_on_updates_page_without_a_banner_or_tray_alert(self):
        quiet = updates.newer_release("1.5.10", {"tag_name": "v1.5.12"})
        with patch.object(kvm.portal, "sandboxed", return_value=False):
            self.window._on_update(quiet)
        self.assertTrue(self.window.update_notice.isHidden())
        self.assertFalse(self.window.header_action.isEnabled())
        self.assertNotIn("available", self.window.header_action.text())
        self.assertFalse(self.window.update_button.isHidden())
        self.assertFalse(self.window.update_page_notes.isHidden())
        self.assertIn("1.5.12", self.window.update_status.text())
        self.window._set_check_updates(False)
        self.window._on_update_check("available", quiet)
        self.assertTrue(self.window.update_notice.isHidden())
        self.assertFalse(self.window.header_action.isEnabled())
        self.assertIn("1.5.12", self.window.update_status.text())

    def test_quiet_flatpak_keeps_release_notes_on_the_updates_page(self):
        quiet = updates.newer_release("1.5.10", {"tag_name": "v1.5.12"})
        with patch.object(kvm.portal, "sandboxed", return_value=True), \
                patch.object(kvm.QDesktopServices, "openUrl") as opened:
            self.window._on_update(quiet)
            self.assertTrue(self.window.update_notice.isHidden())
            self.assertFalse(self.window.update_page_notes.isHidden())
            self.window.update_page_notes.click()
        self.assertEqual(opened.call_args.args[0].toString(), quiet.url)
        self.assertIn("software store", self.window.update_status.text())

    def test_quiet_patch_without_a_portable_zip_explains_the_missing_download_on_updates_page(self):
        quiet = updates.newer_release("1.5.10", {"tag_name": "v1.5.12"})
        settings = app_config.load_settings(self.path)
        with patch.object(kvm.portable_runtime, "enabled", return_value=True), \
                patch.object(app_config, "load_settings", return_value=settings):
            self.window._on_update_check("available", quiet)
        self.assertTrue(self.window.update_notice.isHidden())
        self.assertTrue(self.window.update_button.isHidden())
        self.assertFalse(self.window.update_page_notes.isHidden())
        self.assertIn("No portable download", self.window.update_status.text())

    def test_important_release_after_quiet_patch_alerts_until_dismissed(self):
        self.window._on_update(updates.newer_release("1.5.10", {"tag_name": "v1.5.11"}))
        important = updates.newer_release("1.5.10", {
            "tag_name": "v1.5.12", "body": "<!-- beamer-notify: important -->"})
        self.window._on_update(important)
        self.assertFalse(self.window.update_notice.isHidden())
        self.assertTrue(self.window.header_action.isEnabled())
        self.window.update_dismiss.click()
        self.window._on_update(important)
        self.assertTrue(self.window.update_notice.isHidden())
        self.assertFalse(self.window.header_action.isEnabled())

    def test_manual_check_is_visible_and_works_when_automatic_checks_are_off(self):
        self.window._set_check_updates(False)
        self.assertFalse(self.window.check_updates_button.isHidden())
        self.assertEqual(self.window.check_updates_button.text(), "Check for updates")
        with patch.object(self.window.update_checker, "check_now") as check:
            self.window.check_updates_button.click()
        check.assert_called_once_with(manual=True)
        self.assertFalse(self.window._config.check_updates)
        self.assertFalse(self.window.check_updates_button.isEnabled())
        self.window._on_update_check("available", FOUND)
        self.assertFalse(self.window.update_notice.isHidden())
        self.assertFalse(self.window.update_button.isHidden())
        self.assertIn("1.5.11", self.window.update_status.text())
        self.assertTrue(self.window.check_updates_button.isEnabled())

    def test_manual_check_reports_no_update_and_retryable_failure(self):
        self.window._on_update_check("current", None)
        self.assertIn("up to date", self.window.update_status.text())
        self.window._on_update_check("checking", None)
        self.assertFalse(self.window.check_updates_button.isEnabled())
        self.window._on_update_check("failed", None)
        self.assertIn("try again", self.window.update_status.text())
        self.assertTrue(self.window.check_updates_button.isEnabled())

    def test_update_notice_has_an_explicit_update_action(self):
        with patch.object(kvm.QDesktopServices, "openUrl") as opened:
            self.window._on_update(FOUND)
            self.window.update_action.click()
        self.assertEqual(opened.call_args.args[0].toString(), FOUND[1])

    def test_flatpak_update_action_opens_the_release_with_store_guidance(self):
        with patch.object(kvm.portal, "sandboxed", return_value=True), \
                patch.object(kvm.QDesktopServices, "openUrl") as opened:
            self.window._on_update_check("available", FOUND)
            self.assertFalse(self.window.update_action.isHidden())
            self.window.update_action.click()
        self.assertEqual(opened.call_args.args[0].toString(), FOUND[1])
        self.assertIn("Flatpak", self.window.update_notice_text.text())

    def test_explicit_flatpak_check_resurfaces_a_dismissed_action_until_dismissed_again(self):
        with patch.object(kvm.portal, "sandboxed", return_value=True):
            self.window._on_update(FOUND)
            self.window.update_dismiss.click()
            self.window._set_check_updates(False)
            self.window._on_update_check("available", FOUND)
            self.assertFalse(self.window.update_notice.isHidden())
            self.assertFalse(self.window.update_action.isHidden())
            self.window.update_dismiss.click()
            self.assertTrue(self.window.update_notice.isHidden())


    def test_missing_portable_download_is_explained_without_offering_installer(self):
        settings = app_config.load_settings(self.path)
        with patch.object(kvm.portable_runtime, "enabled", return_value=True), \
                patch.object(app_config, "load_settings", return_value=settings):
            self.window._on_update(FOUND)
        self.assertTrue(self.window.update_action.isHidden())
        self.assertIn("No portable download", self.window.update_notice_text.text())

    def test_dismissal_survives_rebuilding_and_other_settings_changes(self):
        self.window._on_update(FOUND)
        self.assertFalse(self.window.update_notice.isHidden())
        self.assertFalse(self.window.isVisible())
        self.window.update_dismiss.click()
        self.assertTrue(self.window.update_notice.isHidden())
        self.assertTrue(self.window._persist())
        self.assertEqual(app_config.load_settings(self.path)["dismissed_update_version"], "1.5.11")
        rebuilt = self.build()
        rebuilt._on_update(FOUND)
        self.assertTrue(rebuilt.update_notice.isHidden())
        rebuilt._on_update(updates.Release("1.5.12", FOUND[1]))
        self.assertFalse(rebuilt.update_notice.isHidden())

    def test_switching_off_hides_the_notice_and_late_answers(self):
        self.window._on_update(FOUND)
        self.window._set_check_updates(False)
        self.assertFalse(self.window._config.check_updates)
        self.assertTrue(self.window.update_notice.isHidden())
        self.window._on_update(FOUND)
        self.assertTrue(self.window.update_notice.isHidden())
        self.assertIsNone(self.window._update)

    def test_flatpak_has_store_guidance_and_no_download_action(self):
        with patch.object(kvm.portal, "sandboxed", return_value=True):
            self.window._on_update(FOUND)
        self.assertIn("software store", self.window.update_notice_text.text())
        self.assertTrue(self.window.update_button.isHidden())
        self.assertFalse(self.window.update_notes.isHidden())
        self.assertFalse(self.window.header_action.isEnabled())

    def test_desktop_update_header_remains_a_download_action(self):
        with patch.object(kvm.portal, "sandboxed", return_value=False):
            self.window._on_update(FOUND)
        self.assertTrue(self.window.header_action.isEnabled())

    def test_linux_start_checks_for_updates_without_waiting_for_a_toggle(self):
        events = []
        owner = SimpleNamespace(update_checker=SimpleNamespace(start=lambda: events.append("check")),
                                _listen=lambda: events.append("listen"))
        with patch.object(kvm.sys, "platform", "linux"), patch.object(kvm.sys, "frozen", False, create=True):
            kvm.WindowsApplication.start(owner)
        self.assertEqual(events, ["check", "listen"])

    def test_unreadable_settings_do_not_block_opening_the_panel(self):
        self.window._on_update(FOUND)
        with patch.object(app_config, "load_settings", side_effect=OSError("locked")):
            self.window._refresh_update_notice()
        self.assertTrue(self.window.update_notice.isHidden())

    def test_whats_new_opens_the_release_only_on_a_click(self):
        with patch.object(kvm.QDesktopServices, "openUrl") as opened:
            self.window._on_update(FOUND)
            self.assertEqual(opened.call_count, 0)
            self.window.update_notes.click()
        self.assertEqual(opened.call_args.args[0].toString(), FOUND[1])

    def test_failed_save_keeps_the_notice_available(self):
        self.window._on_update(FOUND)
        with patch.object(app_config, "write_settings", side_effect=OSError("read only")):
            self.window.update_dismiss.click()
        self.assertFalse(self.window.update_notice.isHidden())
