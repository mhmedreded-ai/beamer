SPECIAL_KEY_NAMES = {
    0x24: "enter",
    0x30: "tab",
    0x31: "space",
    0x33: "backspace",
    0x35: "esc",
    0x36: "cmd_r",
    0x37: "cmd",
    0x38: "shift",
    0x39: "caps_lock",
    0x3A: "alt",
    0x3B: "ctrl",
    0x3C: "shift_r",
    0x3D: "alt_r",
    0x3E: "ctrl_r",
    0x40: "f17",
    0x41: "numpad_decimal",
    0x43: "numpad_multiply",
    0x45: "numpad_add",
    0x4B: "numpad_divide",
    0x4C: "numpad_enter",
    0x4E: "numpad_subtract",
    0x51: "numpad_equal",
    0x52: "numpad_0", 0x53: "numpad_1", 0x54: "numpad_2", 0x55: "numpad_3",
    0x56: "numpad_4", 0x57: "numpad_5", 0x58: "numpad_6", 0x59: "numpad_7",
    0x5B: "numpad_8", 0x5C: "numpad_9", 0x5F: "numpad_separator",
    0x4F: "f18",
    0x50: "f19",
    0x5A: "f20",
    0x60: "f5",
    0x61: "f6",
    0x62: "f7",
    0x63: "f3",
    0x64: "f8",
    0x65: "f9",
    0x67: "f11",
    0x68: "hangul",
    0x66: "hanja",
    0x69: "f13",
    0x6A: "f16",
    0x6B: "f14",
    0x6D: "f10",
    0x6F: "f12",
    0x71: "f15",
    0x72: "insert",
    0x73: "home",
    0x74: "page_up",
    0x75: "delete",
    0x76: "f4",
    0x77: "end",
    0x78: "f2",
    0x79: "page_down",
    0x7A: "f1",
    0x7B: "left",
    0x7C: "right",
    0x7D: "down",
    0x7E: "up",
}

KEY_NAME_TO_CODE = {}
for _code, _name in SPECIAL_KEY_NAMES.items():
    KEY_NAME_TO_CODE.setdefault(_name, _code)

MODIFIER_KEY_CODES = {
    0x36,
    0x37,
    0x38,
    0x39,
    0x3A,
    0x3B,
    0x3C,
    0x3D,
    0x3E,
}

PRINTABLE_KEY_FALLBACKS = {
    0x00: "a",
    0x01: "s",
    0x02: "d",
    0x03: "f",
    0x04: "h",
    0x05: "g",
    0x06: "z",
    0x07: "x",
    0x08: "c",
    0x09: "v",
    0x0B: "b",
    0x0C: "q",
    0x0D: "w",
    0x0E: "e",
    0x0F: "r",
    0x10: "y",
    0x11: "t",
    0x12: "1",
    0x13: "2",
    0x14: "3",
    0x15: "4",
    0x16: "6",
    0x17: "5",
    0x18: "=",
    0x19: "9",
    0x1A: "7",
    0x1B: "-",
    0x1C: "8",
    0x1D: "0",
    0x1E: "]",
    0x1F: "o",
    0x20: "u",
    0x21: "[",
    0x22: "i",
    0x23: "p",
    0x25: "l",
    0x26: "j",
    0x27: "'",
    0x28: "k",
    0x29: ";",
    0x2A: "\\",
    0x2B: ",",
    0x2C: "/",
    0x2D: "n",
    0x2E: "m",
    0x2F: ".",
    0x32: "`",
}
US_KEY_CODES = {char: code for code, char in PRINTABLE_KEY_FALLBACKS.items()}

# What the settings window calls a key. Names the wire and config keep as they are. AppKit-free,
# so widgets.py re-exports key_title rather than defining it, and anything that only needs a
# display name can import it without pulling AppKit in.
KEY_TITLES = {
    "alt": "Left Option",
    "alt_r": "Right Option",
    "cmd": "Left Command",
    "cmd_r": "Right Command",
    "ctrl": "Left Control",
    "ctrl_r": "Right Control",
    "shift": "Left Shift",
    "shift_r": "Right Shift",
    "caps_lock": "Caps Lock",
    "hangul": "Han/Yeong",
    "hanja": "Hanja",
    "esc": "Escape",
    "page_up": "Page Up",
    "page_down": "Page Down",
}


def key_title(name):
    return KEY_TITLES.get(name, name.replace("_", " ").title())


# The symbol printed on a Mac's modifier keys, for a key drawn as a cap.
KEY_SYMBOLS = {"alt": "⌥", "cmd": "⌘", "ctrl": "⌃", "shift": "⇧", "caps_lock": "⇪"}


def key_cap(name):
    """A key as its cap reads: the modifier's symbol, then its name ("⌥ Right Option")."""
    symbol = KEY_SYMBOLS.get(name[:-2] if name.endswith("_r") else name)
    return f"{symbol} {key_title(name)}" if symbol else key_title(name)
