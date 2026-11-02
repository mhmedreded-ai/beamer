"""Opt-in frozen portable qualification, imported by --import-probe; source imports do nothing."""

import json
import os
from pathlib import Path
import sys


def qualify():
    import base64
    import ctypes
    import ipaddress
    import socket
    import tempfile
    import time
    from contextlib import ExitStack

    from PySide6.QtWidgets import QApplication, QMessageBox, QSystemTrayIcon, QWidget
    import app_config
    import kvm_bridge_win as kvm
    import theme
    from core import portable_runtime
    from core.portable_store import PortableStore

    root = Path(sys.executable).resolve().parent
    output = root / 'data' / 'acceptance.json'
    result = {'passed': False, 'checks': [], 'network_scope': 'loopback', 'medium_token': False}
    window = None
    app = None

    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        result['checks'].append(name)

    def forbidden(*args, **kwargs):
        raise AssertionError('A host mutation or network update was attempted')

    class patch:
        def __init__(self, owner, name, value):
            self.owner, self.name, self.value = owner, name, value

        def __enter__(self):
            self.before = getattr(self.owner, self.name)
            setattr(self.owner, self.name, self.value)
            return self.value

        def __exit__(self, *exc):
            setattr(self.owner, self.name, self.before)

    def wait_for(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return True
            time.sleep(0.02)
        return False

    original_socket = socket.socket
    non_loopback_attempts = []

    def loopback_address(address):
        try:
            allowed = ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            allowed = False
        if not allowed:
            non_loopback_attempts.append('blocked')
            raise OSError('Qualification permits loopback sockets only')
        return address

    class LoopbackSocket(original_socket):
        def bind(self, address):
            if self.family == socket.AF_INET and address[0] in ('', '0.0.0.0'):
                address = ('127.0.0.1', address[1])
            if self.family == socket.AF_INET6 and address[0] in ('', '::'):
                address = ('::1', *address[1:])
            if self.family in (socket.AF_INET, socket.AF_INET6):
                loopback_address(address)
            return super().bind(address)

        def connect(self, address):
            return super().connect(loopback_address(address))

        def connect_ex(self, address):
            return super().connect_ex(loopback_address(address))

        def sendto(self, data, *args):
            loopback_address(args[-1])
            return super().sendto(data, *args)

    try:
        check('frozen_portable_boundary', bool(getattr(sys, 'frozen', False))
              and portable_runtime.enabled() and (root / 'Beamer.portable').is_file())
        check('fresh_synthetic_folder', not portable_runtime.settings_path().exists())
        check('offscreen_platform', os.environ.get('QT_QPA_PLATFORM') == 'offscreen')
        data = root / 'data'
        check('temporary_files_stay_in_payload', Path(tempfile.gettempdir()).resolve() == data / 'tmp'
              and all(Path(os.environ[name]).resolve() == data / 'tmp' for name in ('TEMP', 'TMP', 'TMPDIR')))
        check('cache_stays_in_payload', Path(os.environ['XDG_CACHE_HOME']).resolve() == data / 'cache')
        check('profile_paths_stay_in_payload', Path(os.environ['LOCALAPPDATA']).resolve() == data / 'cache' / 'local'
              and Path(os.environ['APPDATA']).resolve() == data / 'cache' / 'roaming')
        log_path = kvm.configure_logging()
        check('actual_log_stays_in_payload', log_path is not None and log_path.resolve().parent == data
              and kvm.LOG_PATH == log_path and log_path.is_file())
        # The linked UAC token is used by the wrapper when available; prove the child's token.
        advapi = ctypes.WinDLL('advapi32', use_last_error=True)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        token = ctypes.c_void_p()
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        advapi.OpenProcessToken.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(ctypes.c_void_p)]
        advapi.GetTokenInformation.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                              ctypes.c_ulong, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise OSError('Could not inspect qualification process token')
        try:
            elevation, needed = ctypes.c_ulong(), ctypes.c_ulong()
            if not advapi.GetTokenInformation(token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(needed)):
                raise OSError('Could not inspect token elevation')
            advapi.GetTokenInformation(token, 25, None, 0, ctypes.byref(needed))
            integrity = ctypes.create_string_buffer(needed.value)
            if not advapi.GetTokenInformation(token, 25, integrity, needed, ctypes.byref(needed)):
                raise OSError('Could not inspect token integrity')
            sid = ctypes.c_void_p.from_buffer(integrity).value
            advapi.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
            advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
            advapi.GetSidSubAuthority.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            advapi.GetSidSubAuthority.restype = ctypes.POINTER(ctypes.c_ulong)
            count = advapi.GetSidSubAuthorityCount(sid)[0]
            level = advapi.GetSidSubAuthority(sid, count - 1)[0]
            result['token_elevated'] = bool(elevation.value)
            result['integrity_rid'] = int(level)
            result['medium_token'] = not elevation.value and level == 0x2000
        finally:
            kernel.CloseHandle(token)

        locks = kvm._windows_kernel32()
        sentinel = locks.CreateMutexW(None, False, 'Local\\Beamer.Receiver')
        if not sentinel:
            # An existing elevated app may admit synchronise access but deny a new all-access handle.
            locks.OpenMutexW.restype = ctypes.c_void_p
            locks.OpenMutexW.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_wchar_p]
            sentinel = locks.OpenMutexW(0x00100000, False, 'Local\\Beamer.Receiver')
        check('installed_mutex_sentinel_open', bool(sentinel))
        release = None
        try:
            release = kvm._claim_instance()
            check('portable_coexists_with_installed_mutex', release is not None)
            duplicate = kvm._claim_instance()
            if duplicate is not None:
                duplicate()
            check('same_portable_folder_is_single_instance', duplicate is None)
            release()
            release = None
            release = kvm._claim_instance()
            check('portable_mutex_can_be_reclaimed', release is not None)
            session = ctypes.c_ulong()
            check('windows_session_identified', bool(locks.ProcessIdToSessionId(locks.GetCurrentProcessId(), ctypes.byref(session))))
            activation = kvm._instance_server_name()
            installed_activation = 'Beamer.Receiver.' + str(session.value)
            check('portable_activation_name_is_separate', activation != installed_activation
                  and activation.endswith(portable_runtime.instance_suffix()))
        finally:
            if release is not None:
                release()
            locks.CloseHandle(sentinel)

        about = []
        with ExitStack() as stack:
            stack.enter_context(patch(socket, 'socket', LoopbackSocket))
            stack.enter_context(patch(kvm.pairing, 'pairing_address', lambda: '127.0.0.1'))
            stack.enter_context(patch(kvm.pairing, '_local_ipv4_addresses', lambda: ['127.0.0.1']))
            stack.enter_context(patch(QWidget, 'show', lambda self: None))
            stack.enter_context(patch(QSystemTrayIcon, 'setVisible', lambda *args: None))
            stack.enter_context(patch(QSystemTrayIcon, 'showMessage', forbidden))
            stack.enter_context(patch(QMessageBox, 'show', lambda box: about.append(box.informativeText())))
            stack.enter_context(patch(QMessageBox, 'warning', forbidden))
            stack.enter_context(patch(kvm.WindowsApplication, '_make_idle_pointer', lambda self: None))
            for name in ('repair', 'trust_network'):
                stack.enter_context(patch(kvm.firewall_win, name, forbidden))
            for name in ('_run', 'set_enabled', 'remove_legacy_task', 'migrate_legacy_task'):
                stack.enter_context(patch(kvm.autostart_win, name, forbidden))
            app = QApplication.instance() or QApplication([])
            app.setQuitOnLastWindowClosed(False)
            theme.init_fonts()
            passphrase = 'Synthetic portable qualification passphrase'
            answers = iter([(passphrase, True), (passphrase, True)])
            with patch(kvm.QInputDialog, 'getText', lambda *args: next(answers)):
                check('passphrase_bootstrap', kvm.unlock_portable())
            settings = app_config.load_settings(portable_runtime.settings_path())
            settings.update(name='Portable acceptance', check_updates=False)
            app_config.write_settings(portable_runtime.settings_path(), settings)
            synthetic_token = base64.urlsafe_b64encode(bytes(range(32))).decode('ascii').rstrip('=')
            paired = app_config.migrate({'host': '127.0.0.1', 'port': 24820, 'auth_token': synthetic_token})
            settings['peers'] = paired['peers']
            app_config.write_settings(portable_runtime.settings_path(), settings)
            portable_runtime.store.lock()
            reopened = PortableStore(portable_runtime.settings_path())
            reopened.unlock(passphrase)
            portable_runtime.store = reopened
            check('encrypted_pairing_round_trip', app_config.load_settings(portable_runtime.settings_path())['peers'][0]['token'] == synthetic_token)
            settings['peers'] = []
            app_config.write_settings(portable_runtime.settings_path(), settings)
            check('no_plaintext_pairing_file', all(synthetic_token.encode() not in file.read_bytes()
                  for file in (root / 'data').rglob('*') if file.is_file()))
            window = kvm.WindowsApplication(portable_runtime.settings_path())
            check('actual_native_window_constructed', not window.isVisible() and window._config is not None)
            window.update_checker.fetcher = forbidden
            check('portable_update_channel', window.update_checker.portable)
            check('settings_save', window._persist())
            window.show_about()
            check('about_portable_label', bool(about) and 'Portable' in about[0] and 'passphrase' in about[0])
            check('autostart_unavailable', kvm.autostart_win.installed_exe() is None)
            check('pairing_service_constructed', window.pairing is not None)
            with original_socket(socket.AF_INET, socket.SOCK_DGRAM) as beacon_sink:
                beacon_sink.bind(('127.0.0.1', 0))
                window.pairing.announce_to = beacon_sink.getsockname()
                window.start()
                check('real_udp_pairing_listener', wait_for(lambda: window.pairing.listening_port is not None))
                beacon_sink.settimeout(3)
                beacon, address = beacon_sink.recvfrom(4096)
                check('real_loopback_pairing_datagram', bool(beacon) and address[0] == '127.0.0.1')
                code = window.pairing.begin_pairing()
                check('real_tcp_pairing_listener', bool(code) and window.pairing.code == code
                      and wait_for(lambda: window.pairing._listener is not None))
                window.server.start(settings['port'])
                check('real_receiver_listener', wait_for(lambda: window.server.listening_port is not None))
                result['ports'] = {'pairing_udp': window.pairing.listening_port,
                                   'pairing_tcp': window.pairing.tcp_port,
                                   'receiver': window.server.listening_port}
                check('receiver_uses_reserved_port', window.server.listening_port == settings['port'])
                check('loopback_listener_scope', window.server._listener.getsockname()[0] == '127.0.0.1'
                      and window.pairing._listener[0].getsockname()[0] == '127.0.0.1'
                      and window.pairing._sock.getsockname()[0] == '127.0.0.1')
                window.quit()
                check('clean_socket_shutdown', not window.server.listening and window.pairing.listening_port is None)
            check('window_never_shown', not window.isVisible())
            check('no_non_loopback_network_attempt', not non_loopback_attempts)
            result['passed'] = True
    except BaseException as exc:
        result['error_type'] = type(exc).__name__
        if isinstance(exc, AssertionError):
            result['failed_check'] = str(exc)
        raise
    finally:
        if window is not None:
            try:
                window.quit()
            except Exception:
                result['passed'] = False
                result['cleanup_error'] = True
            window.deleteLater()
        portable_runtime.close()
        output.write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')


if (getattr(sys, 'frozen', False) and os.environ.get('BEAMER_PORTABLE_ACCEPTANCE') == '1'
        and (Path(sys.executable).resolve().parent / 'Beamer.portable').is_file()):
    qualify()
