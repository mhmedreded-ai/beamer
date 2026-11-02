"""Key results shared by capture backends and their consumers."""

import unicodedata


class DeadKey:
    def __init__(self, character: str) -> None:
        self.character = character


class TextInput:
    def __init__(self, text: str) -> None:
        self.text = text


def compose_dead_key(dead_key, character):
    name = unicodedata.name(dead_key, "")
    if name.startswith("MODIFIER LETTER "):
        name = name[len("MODIFIER LETTER "):]
    try:
        combining = unicodedata.lookup("COMBINING " + name)
    except KeyError:
        return TextInput(dead_key + character)
    composed = unicodedata.normalize("NFC", character + combining)
    return composed if len(composed) == 1 else TextInput(dead_key + character)


class MacDeadKeys:
    """Composition belongs to the remote capture session, never macOS's transient dead state."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.pending = None
        self.swallowed = {}
        self.layout = None

    def feed(self, code, down, translation, layout, shortcut=False):
        if self.layout != layout:
            self.reset()
            self.layout = layout
        if shortcut:
            self.pending = None
        if code in self.swallowed:
            repeat = self.swallowed[code]
            if not down:
                self.swallowed.pop(code)
            return repeat if down and not shortcut else ""
        if not down:
            return None
        if shortcut:
            self.pending = None
            return None
        if isinstance(translation, DeadKey):
            accent = translation.character
            text = self.pending or ""
            self.pending = None if self.pending == accent else accent
            self.swallowed[code] = ""
            return text
        if self.pending and isinstance(translation, str) and translation.isprintable():
            accent, self.pending = self.pending, None
            result = accent if translation == " " else compose_dead_key(accent, translation)
            self.swallowed[code] = translation
            return result.text if isinstance(result, TextInput) else result
        self.pending = None
        return None
