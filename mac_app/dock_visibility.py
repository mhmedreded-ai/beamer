"""Pure policy selection for the Mac app's Dock and menu bar presence."""

DEFAULT_SHOW_IN_DOCK = True
# One key on all three apps: the menu bar item on the Mac, the tray icon on Windows and Linux.
DEFAULT_SHOW_IN_TRAY = True


def complete_settings(settings):
    settings.setdefault("show_in_dock", DEFAULT_SHOW_IN_DOCK)
    settings.setdefault("show_in_tray", DEFAULT_SHOW_IN_TRAY)
    return settings


def validate_settings(settings):
    if not isinstance(settings.get("show_in_dock"), bool):
        raise ValueError("show_in_dock must be true or false")
    if not isinstance(settings.get("show_in_tray"), bool):
        raise ValueError("show_in_tray must be true or false")


def activation_policy(show_in_dock, window_open):
    return "regular" if show_in_dock or window_open else "accessory"


def menu_bar_visible(show_in_dock, show_in_tray):
    """The menu bar item stays whenever the Dock icon is hidden. With both switched off and the
    window closed there would be nothing to reopen Beamer from, and the menu bar is the one to keep:
    it is where a Dock-less Beamer already lived, and it carries the drive state and Quit."""
    return bool(show_in_tray) or not show_in_dock
