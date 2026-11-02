"""Portable boot boundary: fail closed before importing the native application."""

import ctypes
import runpy
import sys

from core import portable_runtime
from core.i18n import tr


def prepare():
    try:
        portable_runtime.initialise(require_marker=True)
    except OSError:
        ctypes.windll.user32.MessageBoxW(
            None,
            tr("Beamer Portable needs its Beamer.portable marker and a writable folder beside the app. "
               "Move the whole folder to writable storage and try again."),
            tr("Beamer Portable"), 0x10,
        )
        return False
    return True


def main():
    if not prepare():
        return 1
    try:
        runpy.run_module("kvm_bridge_win", run_name="__main__")
    finally:
        portable_runtime.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
