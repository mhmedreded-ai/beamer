"""Synthetic portable acceptance cases; run offscreen from Claude, one module per process."""

import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]

from PySide6.QtWidgets import QApplication
import app_config
import autostart_win
import input_injector
import kvm_bridge_win as kvm
import theme
from core import portable_runtime, protocol, updates
from core.portable_store import PortableStore
from core.tests import responder_harness as h


PASSWORD = 'Synthetic portable passphrase'
VERSION = '1.5.11'
PAGE = 'https://github.com/kalkman-code/beamer/releases/tag/v1.5.11'
DOWNLOAD = ('https://github.com/kalkman-code/beamer/releases/download/v1.5.11/'
            'Beamer-Portable-1.5.11-windows-x64.zip')


class PortableTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        theme.init_fonts()

    def setUp(self):
        scratch = Path.home() / 'hub' / 'Scratch'
        scratch.mkdir(parents=True, exist_ok=True)
        directory = tempfile.TemporaryDirectory(prefix='port2-native-', dir=scratch)
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name) / 'portable'
        (self.root / 'data').mkdir(parents=True)
        self.path = self.root / 'data' / 'settings.beamer'
        self.vault = PortableStore(self.path)
        self.vault.unlock(PASSWORD)
        for blocker in (
            mock.patch.object(portable_runtime, '_root', self.root),
            mock.patch.object(portable_runtime, 'store', self.vault),
            mock.patch.object(portable_runtime, 'reserve_listener', return_value=51234),
            mock.patch('core.locale.is_us_region', return_value=False),
        ):
            blocker.start()
            self.addCleanup(blocker.stop)
        self.addCleanup(self.vault.lock)
        self.addCleanup(lambda: portable_runtime.store.lock() if portable_runtime.store else None)

    def test_paths_ignore_host_settings_and_remain_beside_executable(self):
        with mock.patch.dict(os.environ, {'LOCALAPPDATA': str(self.root.parent / 'host')}):
            self.assertEqual(app_config.default_config_path(), self.path)

    def test_pairing_round_trip_is_encrypted_and_preserves_settings(self):
        settings = app_config.load_settings(self.path)
        settings['peers'] = [h.entry(h.B, 'Synthetic peer')]
        settings['hide_addresses'] = True
        app_config.write_settings(self.path, settings)
        self.vault.lock()
        self.vault.unlock(PASSWORD)
        loaded = app_config.load_settings(self.path)
        self.assertEqual(loaded['schema'], settings['schema'])
        self.assertEqual(loaded['peers'][0]['token'], h.TOKENS[h.B])
        self.assertTrue(loaded['hide_addresses'])
        self.assertEqual(loaded['port'], 51234)
        for path in self.root.rglob('*'):
            if path.is_file():
                self.assertNotIn(h.TOKENS[h.B].encode(), path.read_bytes())
                self.assertNotIn(b'Synthetic peer', path.read_bytes())
        self.assertEqual({path.name for path in self.path.parent.iterdir()}, {'settings.beamer'})

    def test_legacy_settings_are_never_imported_or_rewritten(self):
        legacy = app_config.legacy_config_path(self.path)
        original = json.dumps({'host': '192.0.2.99', 'port': protocol.DEFAULT_PORT,
                               'auth_token': 'legacy-secret-must-stay-out'}).encode()
        legacy.write_bytes(original)
        settings = app_config.load_settings(self.path)
        self.assertEqual(settings['peers'], [])
        self.assertEqual(legacy.read_bytes(), original)
        self.assertNotIn(b'legacy-secret-must-stay-out', self.path.read_bytes())

    def test_locked_state_fails_closed_without_replacing_existing_vault(self):
        before = self.path.read_bytes()
        self.vault.lock()
        with self.assertRaises(app_config.SettingsFileError):
            app_config.load_settings(self.path)
        with self.assertRaises(app_config.SettingsFileError):
            app_config.write_settings(self.path, app_config.migrate(None))
        self.assertEqual(self.path.read_bytes(), before)

    def test_settings_cannot_be_written_to_a_host_path(self):
        outside = self.root.parent / 'host-settings.json'
        with self.assertRaises(app_config.SettingsFileError):
            app_config.write_settings(outside, app_config.migrate(None))
        self.assertFalse(outside.exists())

    def test_logging_failure_never_falls_back_to_host_temp(self):
        with mock.patch.object(kvm, 'RotatingFileHandler', side_effect=PermissionError('read only')) as handler, \
                mock.patch.object(kvm.tempfile, 'gettempdir', side_effect=AssertionError('host temp fallback')):
            with self.assertRaises(PermissionError):
                kvm.configure_logging()
        self.assertEqual(Path(handler.call_args.args[0]).parent, self.path.parent)
        self.assertEqual(handler.call_count, 1)

    def test_profile_upgrade_never_creates_a_plaintext_backup(self):
        app_config._backup_before_profile_upgrade(self.path)
        self.assertEqual({path.name for path in self.path.parent.iterdir()}, {'settings.beamer'})

    def test_cancel_unlock_leaves_pairings_locked_and_unchanged(self):
        before = self.path.read_bytes()
        portable_runtime.store = None
        with mock.patch.object(kvm.QInputDialog, 'getText', return_value=('', False)):
            self.assertFalse(kvm.unlock_portable())
        self.assertIsNone(portable_runtime.store)
        self.assertEqual(self.path.read_bytes(), before)

    def test_cancel_bootstrap_never_constructs_native_application(self):
        args = SimpleNamespace(config=self.path, hidden=True, relaunched=False, identify_x11=None)
        release = mock.Mock()
        app = mock.Mock()
        with mock.patch.object(portable_runtime, 'initialise'), \
                mock.patch.object(portable_runtime, 'close'), \
                mock.patch.object(kvm, 'parse_args', return_value=args), \
                mock.patch.object(kvm, 'prepare_windows_startup'), \
                mock.patch.object(kvm, '_claim_instance', return_value=release), \
                mock.patch.object(kvm, 'configure_logging', return_value=None), \
                mock.patch.object(kvm, '_windows_kernel32', return_value=mock.Mock()), \
                mock.patch.object(kvm, '_stay_unthrottled'), \
                mock.patch.object(kvm, 'QApplication', return_value=app), \
                mock.patch.object(kvm.theme, 'init_fonts'), \
                mock.patch.object(kvm, 'unlock_portable', return_value=False), \
                mock.patch.object(kvm.app_config, 'load_config') as load, \
                mock.patch.object(kvm, 'WindowsApplication') as native:
            kvm.main()
        native.assert_not_called()
        load.assert_not_called()
        app.exec.assert_not_called()
        release.assert_called_once()

    def test_wrong_passphrase_can_retry_without_resetting_pairings(self):
        settings = app_config.load_settings(self.path)
        settings['peers'] = [h.entry(h.B, 'Synthetic peer')]
        app_config.write_settings(self.path, settings)
        before = self.path.read_bytes()
        portable_runtime.store = None
        with mock.patch.object(kvm.QInputDialog, 'getText', side_effect=[('incorrect', True), (PASSWORD, True)]), \
                mock.patch.object(kvm.QMessageBox, 'warning') as warning:
            self.assertTrue(kvm.unlock_portable())
        warning.assert_called_once()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(portable_runtime.store.read()['peers'][0]['token'], h.TOKENS[h.B])

    def test_creation_requires_confirmation_and_cancel_creates_nothing(self):
        self.vault.lock()
        self.path.unlink()
        portable_runtime.store = None
        with mock.patch.object(kvm.QInputDialog, 'getText', side_effect=[(PASSWORD, True), ('', False)]):
            self.assertFalse(kvm.unlock_portable())
        self.assertIsNone(portable_runtime.store)
        self.assertFalse(self.path.exists())

    def test_creation_retries_short_and_mismatched_passphrases(self):
        self.vault.lock()
        self.path.unlink()
        portable_runtime.store = None
        answers = [('short', True), (PASSWORD, True), ('different long passphrase', True),
                   (PASSWORD, True), (PASSWORD, True)]
        with mock.patch.object(kvm.QInputDialog, 'getText', side_effect=answers), \
                mock.patch.object(kvm.QMessageBox, 'warning') as warning:
            self.assertTrue(kvm.unlock_portable())
        self.assertEqual(warning.call_count, 2)
        self.assertEqual(portable_runtime.store.read(), {})
        self.assertNotIn(PASSWORD.encode(), self.path.read_bytes())

    def test_startup_and_autostart_controls_never_touch_tasks(self):
        runner = mock.Mock(side_effect=AssertionError('task command attempted'))
        with mock.patch.object(kvm.sys, 'platform', 'win32'), \
                mock.patch.object(kvm.sys, 'frozen', True, create=True), \
                mock.patch.object(autostart_win, 'migrate_legacy_task') as migrate:
            kvm.prepare_windows_startup()
            self.assertIsNone(autostart_win.installed_exe())
            self.assertFalse(autostart_win.is_enabled(run=runner))
        migrate.assert_not_called()
        autostart_win.migrate_legacy_task(run=runner)
        autostart_win.remove_legacy_task(run=runner)
        for enabled in (True, False):
            with self.assertRaisesRegex(OSError, 'Portable'):
                autostart_win.set_enabled(enabled, str(self.root / 'Beamer.exe'), run=runner)
        runner.assert_not_called()

    def test_installer_entry_points_leave_tasks_and_firewall_untouched(self):
        with mock.patch.object(autostart_win, 'set_enabled') as task, \
                mock.patch.object(autostart_win, 'remove_legacy_task') as legacy, \
                mock.patch.object(autostart_win, '_run') as run:
            self.assertEqual(kvm.register_autostart_for_setup(), 1)
            self.assertEqual(kvm.cleanup_for_uninstall(), 1)
        task.assert_not_called()
        legacy.assert_not_called()
        run.assert_not_called()

    def test_gameinput_service_is_not_restarted_in_portable_mode(self):
        after = mock.Mock()
        with mock.patch.object(input_injector, 'foreground_class') as foreground, \
                mock.patch.object(subprocess, 'run') as run, \
                mock.patch.object(input_injector.threading, 'Thread') as thread:
            self.assertFalse(input_injector.release_gameinput_foreground(after=after))
        run.assert_not_called()
        thread.assert_not_called()
        after.assert_not_called()

    def test_manual_automatic_and_startup_firewall_paths_never_mutate_host(self):
        owner = SimpleNamespace(
            update_checker=SimpleNamespace(start=mock.Mock()), _listen=mock.Mock(),
            firewall_note=mock.Mock(), firewall_button=mock.Mock(), _run_firewall=mock.Mock(),
            _firewall_advice=SimpleNamespace(action='repair', rule_profiles=('Private',)),
        )
        with mock.patch.object(kvm.sys, 'frozen', True, create=True), \
                mock.patch.object(kvm.firewall_win, 'is_elevated', return_value=True), \
                mock.patch.object(kvm.firewall_win, 'repair') as repair, \
                mock.patch.object(kvm.firewall_win, 'trust_network') as trust, \
                mock.patch.object(kvm.firewall_win, 'status') as status, \
                mock.patch.object(kvm.threading, 'Thread') as thread:
            kvm.WindowsApplication.start(owner)
            kvm.WindowsApplication._firewall_action(owner)
            kvm.WindowsApplication._on_firewall(owner, SimpleNamespace(elevated=True))
            kvm.WindowsApplication._check_firewall(owner)
        owner._listen.assert_called_once()
        owner._run_firewall.assert_not_called()
        owner.firewall_button.hide.assert_called_once()
        for operation in (repair, trust, status, thread):
            operation.assert_not_called()

    def test_windows_mutex_and_activation_names_do_not_clash_with_installed_app(self):
        kernel = mock.Mock()
        kernel.CreateMutexW.return_value = 42

        def session(_pid, pointer):
            pointer._obj.value = 7
            return True

        kernel.ProcessIdToSessionId.side_effect = session
        with mock.patch.object(kvm.sys, 'platform', 'win32'), \
                mock.patch.object(kvm, '_windows_kernel32', return_value=kernel), \
                mock.patch.object(kvm.ctypes, 'get_last_error', return_value=0, create=True):
            portable_name = kvm._instance_server_name()
            release = kvm._claim_instance()
            portable_mutex = kernel.CreateMutexW.call_args.args[2]
            release()
            with mock.patch.object(portable_runtime, '_root', None), \
                    mock.patch.object(portable_runtime, 'portable_root', return_value=None):
                installed_name = kvm._instance_server_name()
                release = kvm._claim_instance()
                installed_mutex = kernel.CreateMutexW.call_args.args[2]
                release()
        self.assertEqual(installed_name, 'Beamer.Receiver.7')
        self.assertEqual(installed_mutex, 'Local\\Beamer.Receiver')
        self.assertNotEqual(portable_name, installed_name)
        self.assertNotEqual(portable_mutex, installed_mutex)
        self.assertTrue(portable_name.endswith(portable_runtime.instance_suffix()))
        self.assertTrue(portable_mutex.endswith(portable_runtime.instance_suffix()))

    def test_legacy_installed_instance_prevents_portable_input_lease(self):
        kernel = mock.Mock()
        kernel.OpenMutexW.return_value = 77
        with mock.patch.object(kvm, '_windows_kernel32', return_value=kernel), \
                mock.patch.object(kvm.ctypes, 'get_last_error', return_value=0, create=True):
            self.assertIsNone(kvm._claim_input_controller())
        kernel.OpenMutexW.assert_called_once_with(0x00100000, False, 'Local\\Beamer.Receiver')
        kernel.CloseHandle.assert_called_once_with(77)
        kernel.CreateMutexW.assert_not_called()

    def test_inaccessible_legacy_instance_also_blocks_portable_input(self):
        kernel = mock.Mock()
        kernel.OpenMutexW.return_value = 0
        with mock.patch.object(kvm, '_windows_kernel32', return_value=kernel), \
                mock.patch.object(kvm.ctypes, 'get_last_error', return_value=5, create=True):
            self.assertIsNone(kvm._claim_input_controller())
        kernel.CreateMutexW.assert_not_called()
        kernel.CloseHandle.assert_not_called()

    def test_existing_shared_input_lease_is_refused_and_handle_closed(self):
        kernel = mock.Mock()
        kernel.OpenMutexW.return_value = 0
        kernel.CreateMutexW.return_value = 99
        with mock.patch.object(kvm, '_windows_kernel32', return_value=kernel), \
                mock.patch.object(kvm.ctypes, 'get_last_error', side_effect=[2, 183], create=True):
            self.assertIsNone(kvm._claim_input_controller())
        kernel.CreateMutexW.assert_called_once_with(None, False, 'Local\\Beamer.InputController')
        kernel.CloseHandle.assert_called_once_with(99)

    def test_available_input_lease_returns_a_release_for_its_own_handle(self):
        kernel = mock.Mock()
        kernel.OpenMutexW.return_value = 0
        kernel.CreateMutexW.return_value = 99
        with mock.patch.object(kvm, '_windows_kernel32', return_value=kernel), \
                mock.patch.object(kvm.ctypes, 'get_last_error', side_effect=[2, 0], create=True):
            release = kvm._claim_input_controller()
        self.assertTrue(callable(release))
        kernel.CloseHandle.assert_not_called()
        release()
        kernel.CloseHandle.assert_called_once_with(99)

    def test_blocked_input_keeps_pairing_available_and_retries_without_hooks(self):
        owner = SimpleNamespace(
            _closing=False, pairing=mock.Mock(), _input_controller_blocked=True,
            _config=SimpleNamespace(port=51234), _paired=True,
            _set_status=mock.Mock(), _listen=mock.Mock(), _start_receiver=mock.Mock(),
            _start_sending=mock.Mock(), _make_idle_pointer=mock.Mock(), _check_firewall=mock.Mock(),
        )
        with mock.patch.object(kvm.sys, 'platform', 'win32'), \
                mock.patch.object(kvm, '_claim_input_controller', return_value=None), \
                mock.patch.object(kvm.QTimer, 'singleShot') as retry, \
                mock.patch.object(kvm.platform_parts.injector, 'start', create=True) as injector:
            kvm.WindowsApplication._listen(owner)
        owner.pairing.start.assert_called_once()
        owner._set_status.assert_called_once()
        self.assertEqual(owner._set_status.call_args.args[0], kvm.ServerState.ERROR)
        self.assertIn('Another Beamer copy controls input', owner._set_status.call_args.args[1])
        retry.assert_called_once_with(1000, owner._listen)
        for blocked in (owner._start_receiver, owner._start_sending, owner._make_idle_pointer, injector):
            blocked.assert_not_called()
        self.assertTrue(owner._input_controller_blocked)

    def test_retry_after_input_lease_release_starts_this_copys_input(self):
        release, idle_pointer = mock.Mock(), object()
        owner = SimpleNamespace(
            _closing=False, pairing=mock.Mock(), _input_controller_blocked=True,
            _config=SimpleNamespace(port=51234), _paired=True,
            _set_status=mock.Mock(), _listen=mock.Mock(), _start_receiver=mock.Mock(),
            _start_sending=mock.Mock(), _make_idle_pointer=mock.Mock(return_value=idle_pointer),
            _check_firewall=mock.Mock(),
        )
        with mock.patch.object(kvm.sys, 'platform', 'win32'), \
                mock.patch.object(kvm, '_claim_input_controller', return_value=release), \
                mock.patch.object(kvm.QTimer, 'singleShot') as retry, \
                mock.patch.object(kvm.platform_parts.injector, 'start', create=True) as injector:
            kvm.WindowsApplication._listen(owner)
        self.assertFalse(owner._input_controller_blocked)
        self.assertIs(owner._input_controller_release, release)
        self.assertIs(owner._idle_pointer, idle_pointer)
        owner._start_receiver.assert_called_once_with(owner._config)
        owner._start_sending.assert_called_once_with(owner._config)
        owner._check_firewall.assert_called_once()
        injector.assert_called_once()
        retry.assert_not_called()
        release.assert_not_called()

    def test_direct_input_start_methods_respect_blocked_controller(self):
        owner = SimpleNamespace(_input_controller_blocked=True,
                                server=mock.Mock(), sender=mock.Mock(), hooks=mock.Mock())
        config = SimpleNamespace(port=51234)
        kvm.WindowsApplication._start_receiver(owner, config)
        kvm.WindowsApplication._start_sending(owner, config)
        owner.server.start.assert_not_called()
        owner.sender.start.assert_not_called()
        owner.hooks.start.assert_not_called()

    @unittest.skipUnless(sys.platform == 'win32', 'pointer restoration is a Windows desktop API')
    def test_blocked_controller_does_not_restore_pointer(self):
        owner = SimpleNamespace(_input_controller_blocked=True)
        with mock.patch.object(portable_runtime, 'enabled', return_value=False), \
                mock.patch.object(kvm.desktop_win, 'restore_pointer') as restore, \
                mock.patch.object(kvm.idle_pointer, 'IdlePointer') as pointer:
            self.assertIsNone(kvm.WindowsApplication._make_idle_pointer(owner))
        restore.assert_not_called()
        pointer.assert_not_called()

    def test_link_identity_and_pairing_beacon_use_the_same_portable_name(self):
        settings = app_config.load_settings(self.path)
        settings['name'] = ''
        app_config.write_settings(self.path, settings)
        owner = SimpleNamespace(
            config_path=self.path,
            _config=SimpleNamespace(name='', machine_id=settings['machine_id']),
            _settings_port=51234, _announced_port=lambda: 51234,
            book=SimpleNamespace(peers=lambda: []), _store_peer=mock.Mock(),
            bridge=SimpleNamespace(paired=SimpleNamespace(emit=mock.Mock())),
        )
        service = SimpleNamespace(active_discovery=False)
        with mock.patch.object(kvm.pairing, 'machine_name', return_value='Synthetic PC'), \
                mock.patch.object(kvm, 'PairingService', return_value=service) as factory:
            identity = kvm.WindowsApplication._identity(owner)
            self.assertIs(kvm.WindowsApplication._make_pairing(owner), service)
        self.assertEqual(identity['name'], 'Synthetic PC (Portable)')
        self.assertEqual(factory.call_args.args[0], identity['name'])
        self.assertTrue(service.active_discovery)

    def test_portable_yields_when_legacy_installed_copy_starts_later(self):
        events = []
        owner = SimpleNamespace(
            _closing=False, _input_controller_blocked=False,
            _input_controller_release=lambda: events.append('release'),
            server=SimpleNamespace(stop=lambda: events.append('stop receiver')),
            _stop_sending=lambda: events.append('stop sender'),
            _listen=lambda: events.append('wait for input'),
        )
        with mock.patch.object(kvm, '_installed_instance_running', return_value=True):
            kvm.WindowsApplication._check_input_controller(owner)
        self.assertEqual(events, ['stop receiver', 'stop sender', 'release', 'wait for input'])
        self.assertTrue(owner._input_controller_blocked)
        self.assertIsNone(owner._input_controller_release)

    def test_about_identifies_portable_passphrase_storage_and_uipi_limit(self):
        box = mock.Mock()
        box.findChildren.return_value = []
        with mock.patch.object(kvm, 'QMessageBox', return_value=box):
            kvm.WindowsApplication.show_about(object())
        text = box.setInformativeText.call_args.args[0]
        self.assertIn('Portable', text)
        self.assertIn('passphrase', text)
        self.assertIn('Administrator apps cannot be controlled', text)

    def test_download_opens_portable_asset_and_notes_keep_release_page(self):
        payload = {'tag_name': 'v' + VERSION, 'html_url': PAGE,
                   'assets': [{'name': 'Beamer-Portable-' + VERSION + '-windows-x64.zip',
                               'browser_download_url': DOWNLOAD}]}
        checker = updates.Checker('1.5.10', lambda: True, lambda result: None,
                                  fetcher=lambda: json.dumps(payload).encode(), portable=True)
        checker.check_once()
        owner = SimpleNamespace(_update=checker.latest, update_checker=checker)
        with mock.patch.object(kvm.portal, 'sandboxed', return_value=False), \
                mock.patch.object(kvm.QDesktopServices, 'openUrl') as opened:
            kvm.WindowsApplication.download_update(owner)
            self.assertEqual(opened.call_args.args[0].toString(), DOWNLOAD)
            kvm.WindowsApplication.open_update(owner)
            self.assertEqual(opened.call_args.args[0].toString(), PAGE)
        self.assertEqual(opened.call_count, 2)

    def test_flatpak_never_opens_portable_zip_directly(self):
        payload = {'tag_name': 'v' + VERSION, 'html_url': PAGE,
                   'assets': [{'name': 'Beamer-Portable-' + VERSION + '-windows-x64.zip',
                               'browser_download_url': DOWNLOAD}]}
        checker = updates.Checker('1.5.10', lambda: True, lambda result: None,
                                  fetcher=lambda: json.dumps(payload).encode(), portable=True)
        checker.check_once()
        self.assertEqual(checker.download_url, DOWNLOAD)
        owner = SimpleNamespace(_update=checker.latest, update_checker=checker)
        with mock.patch.object(kvm.portal, 'sandboxed', return_value=True), \
                mock.patch.object(kvm.QDesktopServices, 'openUrl') as opened:
            kvm.WindowsApplication.download_update(owner)
        opened.assert_not_called()

    def test_missing_portable_asset_does_not_open_installer_or_release_page(self):
        owner = SimpleNamespace(_update=(VERSION, PAGE),
                                update_checker=SimpleNamespace(download_url=None))
        with mock.patch.object(kvm.QDesktopServices, 'openUrl') as opened:
            kvm.WindowsApplication.download_update(owner)
        opened.assert_not_called()


if __name__ == '__main__':
    unittest.main()
