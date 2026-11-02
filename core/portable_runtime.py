"""Run-from-folder paths and process-only portable state; no native imports."""

import hashlib
import os
from pathlib import Path
import socket
import sys
import tempfile

MARKER = "Beamer.portable"
_root = None
store = None
listener = None
_port = None


def portable_root(executable=None, frozen=None):
    frozen = getattr(sys, "frozen", False) if frozen is None else frozen
    root = Path(executable or sys.executable).resolve().parent
    return root if frozen and (root / MARKER).is_file() else None


def root():
    return _root if _root is not None else portable_root()


def enabled():
    return root() is not None


def data_path():
    directory = root()
    if directory is None:
        raise OSError("Beamer is not running in portable mode")
    return directory / "data"


def settings_path():
    return data_path() / "settings.beamer"


def instance_suffix():
    directory = root()
    if directory is None:
        return ""
    return ".Portable." + hashlib.sha256(os.path.normcase(str(directory)).encode("utf-8")).hexdigest()[:24]


def initialise(require_marker=False):
    global _root
    detected = portable_root()
    _root = detected.resolve() if detected is not None else None
    if _root is None:
        if require_marker:
            raise OSError("Keep Beamer.portable beside Beamer.exe to run the portable app")
        return
    # Establish writable storage before Qt, logging or settings can choose a host fallback.
    directory = data_path()
    directory.mkdir(exist_ok=True)
    for child in (directory, directory / "tmp", directory / "cache",
                  directory / "cache" / "local", directory / "cache" / "roaming"):
        child.mkdir(exist_ok=True)
        if not child.resolve().is_relative_to(_root):
            raise OSError("Portable data must stay inside the app folder")
        with tempfile.TemporaryFile(dir=child):
            pass
    temporary = str(directory / "tmp")
    for key in ("TMP", "TEMP", "TMPDIR"):
        os.environ[key] = temporary
    os.environ["XDG_CACHE_HOME"] = str(directory / "cache")
    # Dependencies must not inherit host profile locations.
    os.environ["LOCALAPPDATA"] = str(directory / "cache" / "local")
    os.environ["APPDATA"] = str(directory / "cache" / "roaming")
    tempfile.tempdir = temporary
    sys.dont_write_bytecode = True


def reserve_listener(preferred=0):
    """Keep the OS-selected TCP port reserved until LinkResponder takes this socket."""
    global listener, _port
    if _port is not None:
        return _port
    if listener is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            if type(preferred) is not int or not 0 <= preferred <= 65535:
                preferred = 0
            try:
                sock.bind(("0.0.0.0", preferred))
            except OSError:
                if not preferred:
                    raise
                sock.bind(("0.0.0.0", 0))
        except BaseException:
            sock.close()
            raise
        listener = sock
    _port = listener.getsockname()[1]
    return _port


def close():
    global store, listener, _port
    if store is not None:
        store.lock()
        store = None
    if listener is not None:
        listener.close()
        listener = None
    _port = None
