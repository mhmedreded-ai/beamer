import subprocess
import sys
import unittest

from core import windows_tools


class WindowsToolOutputTests(unittest.TestCase):
    def test_oem_and_ansi_are_decoded_with_the_callers_encoding(self):
        self.assertEqual(windows_tools.decode_output(b"\x90lodie", "cp850"), "Élodie")
        self.assertEqual(windows_tools.decode_output(b"Jos\xe9", "cp1252"), "José")

    def test_bom_overrides_the_legacy_encoding(self):
        for encoding in ("utf-8-sig", "utf-16", "utf-16-be"):
            text = "Élodie: C:\\Users\\José"
            data = text.encode(encoding)
            if encoding == "utf-16-be":
                data = b"\xfe\xff" + data
            with self.subTest(encoding=encoding):
                self.assertEqual(windows_tools.decode_output(data, "cp850"), text)

    def test_xml_declaration_does_not_override_the_actual_pipe_encoding(self):
        self.assertEqual(
            windows_tools.decode_output(b'<?xml version="1.0" encoding="UTF-16"?><User>\x90lodie</User>', "cp850"),
            '<?xml version="1.0" encoding="UTF-16"?><User>Élodie</User>',
        )

    def test_invalid_bytes_are_replaced_and_line_endings_are_normalised(self):
        self.assertEqual(windows_tools.decode_output(b"invalid \x90\r\nnext\rlast", "cp1252"), "invalid \ufffd\nnext\nlast")

    def test_real_pipe_captures_both_streams_without_losing_exit_status(self):
        result = windows_tools.run([
            sys.executable, "-c",
            "import os; os.write(1, b'\\x90lodie\\r\\n'); os.write(2, b'acc\\x8as refus\\x82'); raise SystemExit(7)",
        ], encoding="cp850", timeout=5)
        self.assertEqual(result.stdout, "Élodie\n")
        self.assertEqual(result.stderr, "accès refusé")
        self.assertEqual(result.returncode, 7)

    def test_timeout_still_reaches_the_caller(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            windows_tools.run([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)
