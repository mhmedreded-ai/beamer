"""GH19: exercise the real pipe reader without changing accounts or sign-in tasks."""

import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import autostart_win
import firewall_win


@unittest.skipUnless(sys.platform == "win32", "Windows pipe encodings")
class WindowsToolEncodingTests(unittest.TestCase):
    def test_oem_task_xml_preserves_accented_owner_and_profile(self):
        exe = r"C:\Users\Élodie\AppData\Local\Beamer\Beamer.exe"
        xml = autostart_win.task_xml(exe, r"STUDIO-PC\Élodie")
        data = xml.encode("oem")
        command = [sys.executable, "-c", f"import os; os.write(1, {data!r})"]
        result = autostart_win._run(command)
        self.assertEqual(result.stdout, xml)
        with patch.object(autostart_win, "installed_exe", return_value=None):
            self.assertTrue(autostart_win.is_enabled(lambda args: result, r"STUDIO-PC\Élodie"))

    def test_oem_stderr_preserves_the_refusal_and_exit_code(self):
        message = "Échec : accès refusé."
        data = message.encode("oem")
        result = autostart_win._run([
            sys.executable, "-c", f"import os; os.write(2, {data!r}); raise SystemExit(7)",
        ])
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stderr, message)

    def test_bom_marked_utf16_task_xml_is_read_as_unicode(self):
        xml = autostart_win.task_xml(r"C:\Users\José\Beamer.exe", r"STUDIO-PC\José")
        data = xml.encode("utf-16")
        result = autostart_win._run([
            sys.executable, "-c", f"import os; os.write(1, {data!r})",
        ])
        self.assertEqual(result.stdout, xml)

    def test_firewall_powershell_utf8_preserves_non_ascii_program_path(self):
        exe = r"C:\Users\José\AppData\Local\Beamer\Beamer.exe"
        script = firewall_win._PREAMBLE + "[pscustomobject]@{program=" + firewall_win._quote(exe) + "} | ConvertTo-Json -Compress"
        self.assertEqual(json.loads(firewall_win._powershell(script))["program"], exe)

    def test_profile_overrides_keep_settings_and_logs_in_the_unicode_directory(self):
        import app_config
        import kvm_bridge_win

        with tempfile.TemporaryDirectory() as directory:
            profile = Path(directory) / "José"
            local = profile / "AppData" / "Local"
            roaming = profile / "AppData" / "Roaming"
            with patch.dict(os.environ, {"USERPROFILE": str(profile), "APPDATA": str(roaming), "LOCALAPPDATA": str(local)}):
                path = app_config.default_config_path()
                self.assertEqual(path, local / "Beamer" / "settings.json")
                config = app_config.default_config()
                config.edge_glow = False
                app_config.save_config(path, config)
                self.assertFalse(app_config.load_config(path, unpaired_ok=True).edge_glow)
                root = logging.getLogger()
                previous_handlers, previous_level = list(root.handlers), root.level
                try:
                    with patch.object(kvm_bridge_win, "LOG_PATH", None):
                        self.assertEqual(kvm_bridge_win.configure_logging(), path.with_name("Beamer.log"))
                        root.info("Élodie: %s", path)
                finally:
                    for handler in list(root.handlers):
                        if handler not in previous_handlers:
                            handler.flush()
                            root.removeHandler(handler)
                            handler.close()
                    root.setLevel(previous_level)
                self.assertIn("Élodie", path.with_name("Beamer.log").read_text(encoding="utf-8"))

    def test_firewall_powershell_refusal_preserves_localised_text(self):
        with self.assertRaisesRegex(RuntimeError, "Échec"):
            firewall_win._powershell(firewall_win._PREAMBLE + "throw 'Échec : accès refusé.'")
