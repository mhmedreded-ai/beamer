"""Packaging must allow an ordinary Explorer launch without requesting elevation."""

import runpy
import unittest
from pathlib import Path
from types import SimpleNamespace


class WindowsLaunchManifestTests(unittest.TestCase):
    def test_main_executable_does_not_request_administrator_or_uiaccess(self):
        source = Path(__file__).resolve().parents[2] / "win_app"
        options = {}

        def executable(*args, **kwargs):
            options.update(kwargs)

        runpy.run_path(str(source / "build.spec"), init_globals={
            "SPECPATH": str(source),
            "Analysis": lambda *args, **kwargs: SimpleNamespace(
                binaries=[], pure=[], scripts=[], datas=[]),
            "PYZ": lambda value: value,
            "EXE": executable,
        })
        self.assertFalse(options.get("uac_admin", False))
        self.assertFalse(options.get("uac_uiaccess", False))


if __name__ == "__main__":
    unittest.main()
