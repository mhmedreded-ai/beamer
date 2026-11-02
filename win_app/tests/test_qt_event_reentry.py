"""Keep cold-process event conversion safe when earlier recorder fixtures are collected."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


@unittest.skipUnless(sys.platform == "win32", "Windows native heap-corruption regression")
class QtEventReentryTests(unittest.TestCase):
    def test_recording_then_constructing_a_widget_survives_collection(self):
        root = Path(__file__).resolve().parents[2]
        source = root / "win_app"
        environment = dict(
            os.environ,
            QT_QPA_PLATFORM="offscreen",
            PYSIDE6_OPTION_LAZY="1",
            PYTHONFAULTHANDLER="1",
            PYTHONPATH=os.pathsep.join((str(source / "tests"), str(source), str(root))),
        )
        # A fresh process preserves lazy type loading; earlier Qt tests can hide the fault.
        for attempt in range(3):
            with self.subTest(attempt=attempt):
                result = subprocess.run(
                    [sys.executable, str(root / "scripts/ui_audit/guarded_tests.py"), "-v",
                     "test_ignored_page"],
                    cwd=source, env=environment, capture_output=True,
                    text=True, errors="replace", timeout=30,
                )
                output = result.stdout + result.stderr
                self.assertEqual(result.returncode, 0, output)
                self.assertIn("Ran 7 tests", output)
                self.assertNotIn("skipped", output.lower())


if __name__ == "__main__":
    unittest.main()
