"""Native update notice, built offscreen with synthetic settings; Claude pass only."""

from types import SimpleNamespace
from unittest import mock

import kvm_bridge_app as kvm
import settings_store
import test_crossing_page_machines as fixture
from core import updates


FOUND = updates.Release("1.5.11", "https://github.com/kalkman-code/beamer/releases/tag/v1.5.11")


class UpdateNoticeTest(fixture.Page):
    def test_hidden_notice_does_not_keep_a_width_link_to_the_window(self):
        window = self.build([])
        notice = window.update_notice.view

        def window_width_links():
            pending = [window.window.contentView()]
            links = []
            while pending:
                view = pending.pop()
                pending.extend(view.subviews())
                links.extend(constraint for constraint in view.constraints()
                             if constraint.isActive() and constraint.firstItem() == notice
                             and constraint.firstAttribute() == kvm.AppKit.NSLayoutAttributeWidth
                             and constraint.secondAttribute() == kvm.AppKit.NSLayoutAttributeWidth)
            return links

        self.assertTrue(notice.isHidden())
        self.assertEqual(window_width_links(), [])
        window.show_update(FOUND)
        self.assertFalse(notice.isHidden())
        self.assertEqual(len(window_width_links()), 1)
        window.dismissUpdate_(None)
        self.assertTrue(notice.isHidden())
        self.assertEqual(window_width_links(), [])
        window.show_update_check("available", FOUND)
        self.assertFalse(notice.isHidden())
        self.assertEqual(len(window_width_links()), 1)

    def test_quiet_patch_stays_on_updates_page_even_when_explicitly_requested(self):
        window = self.build([])
        quiet = updates.newer_release("1.5.10", {"tag_name": "v1.5.12"})
        window.show_update(quiet)
        self.assertTrue(window.update_notice.view.isHidden())
        self.assertFalse(window.update_button.view.isHidden())
        self.assertFalse(window.update_page_notes.view.isHidden())
        self.assertIn("1.5.12", window.update_status.view.stringValue())
        window._set_check_updates(False)
        window.show_update_check("available", quiet)
        self.assertTrue(window.update_notice.view.isHidden())
        self.assertIn("1.5.12", window.update_status.view.stringValue())

    def test_quiet_patch_does_not_change_menu_header_but_important_release_does_once(self):
        window = self.build([])
        header = SimpleNamespace(title="", set_callback=lambda cb: setattr(header, "callback", cb))
        tray = SimpleNamespace(controller=self.controller, control_window=window, header_item=header,
                               open_update=lambda _sender: None, update_url=None)
        window.update_handler = lambda found, **kw: kvm.TrayApp._update_found(tray, found, **kw)
        window.update_handler(updates.newer_release("1.5.10", {"tag_name": "v1.5.11"}))
        self.assertIsNone(header.callback)
        self.assertNotIn("available", header.title)
        important = updates.newer_release("1.5.10", {
            "tag_name": "v1.5.12", "body": "<!-- beamer-notify: important -->"})
        window.update_handler(important)
        self.assertIsNotNone(header.callback)
        window.dismissUpdate_(None)
        window.update_handler(important)
        self.assertTrue(window.update_notice.view.isHidden())
        self.assertIsNone(header.callback)

    def test_explicit_check_resurfaces_a_dismissed_action_until_dismissed_again(self):
        window = self.build([])
        window.show_update(FOUND)
        window.dismissUpdate_(None)
        window._set_check_updates(False)
        window.show_update_check("available", FOUND)
        self.assertFalse(window.update_notice.view.isHidden())
        window.dismissUpdate_(None)
        self.assertTrue(window.update_notice.view.isHidden())

    def test_manual_check_is_visible_and_works_when_automatic_checks_are_off(self):
        window = self.build([])
        window._set_check_updates(False)
        window.update_checker = mock.Mock()
        self.assertFalse(window.check_updates_button.view.isHidden())
        self.assertEqual(window.check_updates_button.view.title(), "Check for updates")
        window.check_updates_button.view.performClick_(None)
        window.update_checker.check_now.assert_called_once_with(manual=True)
        self.assertFalse(self.controller.cfg.check_updates)
        self.assertFalse(window.check_updates_button.view.isEnabled())
        window.show_update_check("available", FOUND)
        self.assertFalse(window.update_notice.view.isHidden())
        self.assertFalse(window.update_button.view.isHidden())
        self.assertIn("1.5.11", window.update_status.view.stringValue())
        self.assertTrue(window.check_updates_button.view.isEnabled())

    def test_manual_check_reports_no_update_and_retryable_failure(self):
        window = self.build([])
        window.show_update_check("current", None)
        self.assertIn("up to date", window.update_status.view.stringValue())
        window.show_update_check("checking", None)
        self.assertFalse(window.check_updates_button.view.isEnabled())
        window.show_update_check("failed", None)
        self.assertIn("try again", window.update_status.view.stringValue())
        self.assertTrue(window.check_updates_button.view.isEnabled())

    def test_update_notice_has_an_explicit_update_action(self):
        window = self.build([])
        workspace = mock.Mock()
        with mock.patch.object(kvm.AppKit, "NSWorkspace", SimpleNamespace(sharedWorkspace=lambda: workspace)):
            window.show_update(FOUND)
            window.update_action.view.performClick_(None)
        self.assertEqual(workspace.openURL_.call_args.args[0].absoluteString(), FOUND[1])

    def test_dismissal_survives_rebuilding_and_other_settings_changes(self):
        window = self.build([])
        window.show_update(FOUND)
        self.assertFalse(window.update_notice.view.isHidden())
        self.assertFalse(window.window.isVisible())
        window.dismissUpdate_(None)
        self.assertTrue(window.update_notice.view.isHidden())
        self.assertEqual(self.store.current()["dismissed_update_version"], "1.5.11")
        cfg = self.store.save(settings_store.config_to_raw(self.controller.cfg))
        self.controller.update_config(cfg)
        self.store._cache = None
        window = self.build([])
        window.show_update(FOUND)
        self.assertTrue(window.update_notice.view.isHidden())
        window.show_update(updates.Release("1.5.12", FOUND[1]))
        self.assertFalse(window.update_notice.view.isHidden())

    def test_switching_off_hides_the_notice_and_late_answers(self):
        window = self.build([])
        window.show_update(FOUND)
        window._set_check_updates(False)
        self.assertFalse(self.controller.cfg.check_updates)
        self.assertTrue(window.update_notice.view.isHidden())
        window.show_update(FOUND)
        self.assertTrue(window.update_notice.view.isHidden())
        self.assertIsNone(window.update_url)

    def test_switching_off_clears_the_tray_download_action(self):
        window = self.build([])
        header = SimpleNamespace(title="", set_callback=lambda callback: setattr(header, "callback", callback))
        tray = SimpleNamespace(controller=self.controller, control_window=window, header_item=header,
                               open_update=lambda _sender: None, update_url=None)
        window.update_handler = lambda found: kvm.TrayApp._update_found(tray, found)
        window.update_handler(FOUND)
        self.assertIsNotNone(header.callback)
        window._set_check_updates(False)
        self.assertIsNone(tray.update_url)
        self.assertIsNone(header.callback)
        self.assertNotIn("available", header.title)
        window.update_handler(FOUND)
        self.assertIsNone(header.callback)

    def test_whats_new_opens_the_release_only_on_a_click(self):
        window = self.build([])
        workspace = mock.Mock()
        with mock.patch.object(kvm.AppKit, "NSWorkspace", SimpleNamespace(sharedWorkspace=lambda: workspace)):
            window.show_update(FOUND)
            self.assertEqual(workspace.openURL_.call_count, 0)
            window.update_notes.view.performClick_(None)
        self.assertEqual(workspace.openURL_.call_args.args[0].absoluteString(), FOUND[1])

    def test_failed_save_keeps_the_notice_available(self):
        window = self.build([])
        window.show_update(FOUND)
        with mock.patch.object(self.store, "save", side_effect=settings_store.SettingsError("read only")):
            window.dismissUpdate_(None)
        self.assertFalse(window.update_notice.view.isHidden())
