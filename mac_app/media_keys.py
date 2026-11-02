"""macOS media/volume key capture.

The F7-F12 row does not produce ordinary key events. macOS delivers those
presses as NSSystemDefined events (raw NSEventType 14) carrying subtype 8,
NX_SUBTYPE_AUX_CONTROL_BUTTONS, with the actual button and its up/down state
packed into `data1`. There is no public kCGEventType constant for this, so
the tap mask is built from the raw value, exactly as gestures.py does.

Bit layout of data1 (IOKit's hidsystem/ev_keymap.h):
    bits 31..16  NX key code (which button)
    bits 15..8   key state: 0x0A down, 0x0B up
    bit 0        autorepeat
"""

NX_SYSDEFINED_EVENT_TYPE = 14
NX_SUBTYPE_AUX_CONTROL_BUTTONS = 8

NX_KEY_NAMES = {
    0: "volume_up",
    1: "volume_down",
    2: "brightness_up",
    3: "brightness_down",
    7: "volume_mute",
    16: "media_play_pause",
    17: "media_next",
    18: "media_prev",
    19: "media_next",
    20: "media_prev",
}

_KEY_STATE_DOWN = 0x0A


def decode(subtype, data1):
    """Return (key_name, is_down, is_repeat) for a media-key press, or None
    if this system-defined event is not one (brightness, keyboard backlight,
    Mission Control and several undocumented subtypes share this event
    type)."""
    if int(subtype) != NX_SUBTYPE_AUX_CONTROL_BUTTONS:
        return None
    data1 = int(data1)
    name = NX_KEY_NAMES.get((data1 & 0xFFFF0000) >> 16)
    if name is None:
        return None
    state = (data1 & 0xFF00) >> 8
    return name, state == _KEY_STATE_DOWN, bool(data1 & 0x1)
