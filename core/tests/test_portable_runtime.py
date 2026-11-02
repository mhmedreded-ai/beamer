import os
from pathlib import Path
import socket
import tempfile
import unittest
from unittest.mock import patch

from core import portable_runtime as runtime


class PortableRuntimeTests(unittest.TestCase):
    def tearDown(self):
        runtime.close()

    def test_marker_uses_executable_not_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / runtime.MARKER).touch()
            exe = root / "Beamer.exe"
            self.assertEqual(runtime.portable_root(exe, True), root.resolve())
            self.assertIsNone(runtime.portable_root(exe, False))
            self.assertIsNone(runtime.portable_root(root / "other" / "Beamer.exe", True))

    def test_installed_and_two_portable_copies_have_separate_names(self):
        with patch.object(runtime, "root", return_value=None):
            installed = runtime.instance_suffix()
        with patch.object(runtime, "root", return_value=Path("/first")):
            first = runtime.instance_suffix()
        with patch.object(runtime, "root", return_value=Path("/second")):
            second = runtime.instance_suffix()
        self.assertEqual(installed, "")
        self.assertNotEqual(installed, first)
        self.assertNotEqual(first, second)

    def test_reserved_port_is_held_and_distinct_from_installed(self):
        with socket.socket() as installed:
            installed.bind(("127.0.0.1", 0))
            port = runtime.reserve_listener()
            self.assertNotEqual(port, installed.getsockname()[1])
            with socket.socket() as other:
                with self.assertRaises(OSError):
                    other.bind(("127.0.0.1", port))

    def test_writable_setup_keeps_temp_inside_folder(self):
        previous_temp = tempfile.tempdir
        try:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ):
                root = Path(directory)
                with patch.object(runtime, "portable_root", return_value=root):
                    with patch.object(runtime, "_root", None):
                        runtime.initialise(True)
                        self.assertEqual(runtime.settings_path(), root.resolve() / "data" / "settings.beamer")
                        self.assertEqual(Path(tempfile.gettempdir()), root.resolve() / "data" / "tmp")
                        self.assertEqual(Path(os.environ["LOCALAPPDATA"]), root.resolve() / "data" / "cache" / "local")
                        self.assertEqual(Path(os.environ["APPDATA"]), root.resolve() / "data" / "cache" / "roaming")
                        self.assertFalse(any((root / "data" / "tmp").iterdir()))
        finally:
            tempfile.tempdir = previous_temp

    def test_missing_marker_and_unwritable_storage_never_fall_back(self):
        with patch.object(runtime, "portable_root", return_value=None):
            with self.assertRaises(OSError):
                runtime.initialise(True)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").write_text("not a directory")
            with patch.object(runtime, "portable_root", return_value=root):
                with patch.object(runtime, "_root", None):
                    with self.assertRaises(OSError):
                        runtime.initialise(True)
