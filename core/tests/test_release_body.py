"""Exercise the offline release-body generator; never creates a public release."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from core import updates


class ReleaseBodyTests(unittest.TestCase):
    def generate(self, notes, *args):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "notes.md"
            path.write_text(notes, encoding="utf-8")
            return subprocess.run([sys.executable, str(Path(__file__).resolve().parents[2]
                                   / "tools" / "release_body.py"), "--notes", str(path), *args],
                                  capture_output=True, text=True, encoding="utf-8")

    def test_default_patch_body_is_quiet_and_keeps_notes(self):
        result = self.generate("Fix accented account names.\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "<!-- beamer-notify: quiet -->\n\nFix accented account names.\n")
        self.assertFalse(updates.newer_release("1.5.11", {
            "tag_name": "v1.5.12", "body": result.stdout}).notify)

    def test_important_replaces_template_mark_and_alerts(self):
        result = self.generate("<!-- beamer-notify: quiet -->\n\nFix broken updates.\n",
                               "--notify", "important")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "<!-- beamer-notify: important -->\n\nFix broken updates.\n")
        self.assertTrue(updates.newer_release("1.5.11", {
            "tag_name": "v1.5.12", "body": result.stdout}).notify)

    def test_existing_important_mark_is_preserved_without_an_override(self):
        result = self.generate("<!-- beamer-notify: important -->\n\nFix crash.\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count("beamer-notify:"), 1)
        self.assertIn("<!-- beamer-notify: important -->", result.stdout)

    def test_ambiguous_or_invalid_metadata_is_rejected(self):
        for notes in ("<!-- beamer-notify: typo -->\nFixes.\n",
                      "<!-- beamer-notify: important -->\n<!-- beamer-notify: quiet -->\nFixes."):
            with self.subTest(notes=notes):
                result = self.generate(notes)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
