"""Establish portable storage before PyInstaller's Qt runtime hook imports QtCore."""

from portable_entry import prepare

if not prepare():
    raise SystemExit(1)
