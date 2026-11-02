"""A settings.json this build cannot read is never replaced; the file here is synthetic, in a
temporary folder, with a newer schema number and made-up ids."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import settings_store
from settings_store import SettingsError, SettingsStore

NEWER = {
    "schema": settings_store.SCHEMA + 1,
    "machine_id": "ab" * 16,
    "name": "Preview Mac",
    "peers": [{"id": "cd" * 16, "name": "Somewhere", "token": "not-a-real-token"}],
    "zones": [],
    "preview_only_section": {"anything": [1, 2, 3]},
}


class UnreadableSettingsTests(unittest.TestCase):
    def setUp(self):
        tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(tempdir.cleanup)
        self.path = Path(tempdir.name) / "settings.json"
        self.path.write_text(json.dumps(NEWER, indent=2) + "\n", encoding="utf-8")
        self.before = self.path.read_bytes()
        self.store = SettingsStore(self.path)

    def assert_untouched(self):
        self.assertEqual(self.path.read_bytes(), self.before)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["settings.json"])

    def test_load_reports_it_and_leaves_the_file(self):
        with self.assertRaises(SettingsError):
            self.store.load_settings()
        self.assert_untouched()

    def test_a_save_does_not_replace_it(self):
        with self.assertRaises(SettingsError):
            self.store.save_settings(self.store._fresh())
        self.assert_untouched()


if __name__ == "__main__":
    unittest.main()
