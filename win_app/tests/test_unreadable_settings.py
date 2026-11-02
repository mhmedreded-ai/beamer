"""A settings.json this build cannot read must not crash the window or be touched; run offscreen,
one module per process. The file here is synthetic: a newer schema number and made-up ids, no
real pairing token."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import test_crossing_page_parity as fixture
import app_config
import kvm_bridge_win as kvm
from kvm_bridge_win import ServerState
from PySide6.QtWidgets import QApplication


NEWER = {
    "schema": app_config.SCHEMA + 1,
    "machine_id": "ab" * 16,
    "name": "Preview PC",
    "peers": [{"id": "cd" * 16, "name": "Somewhere", "token": "not-a-real-token"}],
    "zones": [],
    "preview_only_section": {"anything": [1, 2, 3]},
}


class UnreadableSettingsTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        patches = [patch.object(kvm.platform_parts, "WAYLAND", False)]
        for name, value in (("installed_exe", lambda: Path("Beamer.exe")),
                            ("is_enabled", lambda: False),
                            ("can_run_for_current_user", lambda: False)):
            patches.append(patch.object(kvm.autostart_win, name, value))
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        root = Path(__file__).resolve().parents[1] / ".test-tmp"
        root.mkdir(parents=True, exist_ok=True)
        tempdir = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(tempdir.cleanup)
        self.path = Path(tempdir.name) / "settings.json"
        self.path.write_text(json.dumps(NEWER, indent=2) + "\n", encoding="utf-8")
        self.before = self.path.read_bytes()
        self.window = kvm.WindowsApplication(self.path)
        self.window.full_screen_timer.stop()
        self.window.refresh_timer.stop()
        self.addCleanup(self.window.close)

    def test_window_opens_and_the_crossing_page_builds(self):
        fixture.settle(self.window)
        self.window._reflect_ways()
        self.assertEqual(self.window._layout_capable(), set())

    def test_the_message_says_what_happened_and_how_to_recover(self):
        self.assertEqual(self.window._status, ServerState.ERROR)
        detail = self.window._status_detail
        self.assertIn("newer", detail)
        self.assertIn("left as it is", detail)

    def test_the_file_is_untouched_by_launch_and_by_every_writer(self):
        fixture.settle(self.window)
        with self.assertRaises(app_config.SettingsFileError):
            app_config.write_settings(self.path, dict(NEWER))
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["settings.json"])

    def test_the_setting_toggles_do_not_write_over_it(self):
        fixture.settle(self.window)
        self.window._set_show_in_tray(False)
        self.window._set_check_updates(False)
        self.assertEqual(self.path.read_bytes(), self.before)


if __name__ == "__main__":
    unittest.main()
