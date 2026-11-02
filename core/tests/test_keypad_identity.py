"""Physical keypad keys through capture, the real wire, and each desktop injector."""

from collections import namedtuple
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest

from core import keytable, protocol
from core.capture_types import MacDeadKeys
from core.tests.app_methods import load_methods
from core.tests.test_mac_key_repeat import load_injector, QuartzRecorder
from core.tests import test_windows_accent_releases as windows

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "win_app"))
import capture_portal
import capture_x11
import input_injector
import inject_portal
import inject_x11

# name, Windows VK/scan/extended, Mac keycode, evdev code
KEYS = [
    ("numpad_0", 0x60, 82, 0, 82, 82),
    ("numpad_1", 0x61, 79, 0, 83, 79),
    ("numpad_2", 0x62, 80, 0, 84, 80),
    ("numpad_3", 0x63, 81, 0, 85, 81),
    ("numpad_4", 0x64, 75, 0, 86, 75),
    ("numpad_5", 0x65, 76, 0, 87, 76),
    ("numpad_6", 0x66, 77, 0, 88, 77),
    ("numpad_7", 0x67, 71, 0, 89, 71),
    ("numpad_8", 0x68, 72, 0, 91, 72),
    ("numpad_9", 0x69, 73, 0, 92, 73),
    ("numpad_decimal", 0x6e, 83, 0, 65, 83),
    ("numpad_add", 0x6b, 78, 0, 69, 78),
    ("numpad_subtract", 0x6d, 74, 0, 78, 74),
    ("numpad_multiply", 0x6a, 55, 0, 67, 55),
    ("numpad_divide", 0x6f, 53, 1, 75, 98),
    ("numpad_enter", 0x0d, 28, 1, 76, 96),
    ("numpad_equal", 0x92, 89, 0, 81, 117),
    ("numpad_separator", 0x6c, 126, 0, 95, 121),
]


def mac_translator():
    injector = load_injector()
    spec = importlib.util.spec_from_file_location("keypad_codes", Path(__file__).resolve().parents[2] / "mac_app/key_codes.py")
    key_codes = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(key_codes)
    namespace = {"KeyResult": namedtuple("KeyResult", "is_trigger is_down is_repeat messages"),
                 "SPECIAL_KEY_NAMES": key_codes.SPECIAL_KEY_NAMES,
                 "MODIFIER_KEY_CODES": key_codes.MODIFIER_KEY_CODES,
                 "PRINTABLE_KEY_FALLBACKS": key_codes.PRINTABLE_KEY_FALLBACKS,
                 "protocol": protocol, "keytable": keytable,
                 "keyboard_layout": injector.keyboard_layout, "MacDeadKeys": MacDeadKeys}
    cls = load_methods("mac_app/bridge.py", "QuartzEventTranslator",
                       ["key_result", "_special_key_messages", "_dead_key_messages"], namespace)
    translator = cls()
    translator.quartz = SimpleNamespace(kCGKeyboardEventKeycode=9, kCGKeyboardEventAutorepeat=8,
        kCGEventFlagsChanged=12, kCGEventKeyDown=10, kCGEventKeyUp=11,
        CGEventGetIntegerValueField=lambda event, field: event.get(field, 0), CGEventGetFlags=lambda event: 0)
    translator.printable_down = {}
    translator.modifier_down = set()
    translator.dead_keys = MacDeadKeys()
    translator._unicode_character = lambda event, code: "0"
    return translator, injector


class KeypadTests(unittest.TestCase):
    def test_windows_capture_wire_and_all_desktop_receivers_keep_keypad_codes(self):
        case = windows.WindowsReleaseTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        for name, vk, scan, extended, mac, evdev in KEYS:
            with self.subTest(key=name):
                case.feed(vk, True, scan, extended)
                case.feed(vk, False, scan, extended)
                self.check_receivers(case.messages(), name, vk, scan, extended, mac, evdev)

    def check_receivers(self, messages, name, vk, scan, extended, mac, evdev):
        self.assertEqual([(m["type"], m["key"]) for m in messages],
                         [("keydown", name), ("keyup", name)])
        injector = load_injector()
        quartz = QuartzRecorder()
        injector.Quartz = quartz
        win_events, linux_events = [], []
        state = inject_x11.KeyState()
        for message in messages:
            key, down = message["key"], message["type"] == "keydown"
            for source in ("windows", "macos", "linux"):
                for target in ("windows", "macos", "linux"):
                    self.assertEqual(keytable.wire_name(key, source, target, "semantic"), name)
            win_events += input_injector.plan_key_inputs(key, down, set(), {},
                lambda ch: self.fail("keypad became text"), lambda value: scan)
            injector.inject_key(key, down)
            linux_events += inject_x11.plan_key(key, down, None, state, None, 0, 0)
        self.assertEqual(win_events, [(vk, scan, extended), (vk, scan, extended | 2)])
        self.assertEqual([event["code"] for event in quartz.events], [mac, mac])
        self.assertTrue(all(event["flags"] & (1 << 21) for event in quartz.events))
        self.assertEqual(linux_events, [(inject_x11.PRESS, evdev + 8), (inject_x11.RELEASE, evdev + 8)])
        self.assertEqual(inject_portal.wayland_steps(linux_events), [(evdev, True), (evdev, False)])

    def test_mac_and_linux_capture_send_named_keypad_events(self):
        translator, _ = mac_translator()
        for row in KEYS:
            name, vk, scan, extended, mac, evdev = row
            with self.subTest(key=name):
                messages = []
                for kind in (10, 11):
                    for msg in translator.key_result(kind, {9: mac}, None, {}).messages:
                        msg["data"]["seq"] = len(messages) + 1
                        messages.append(protocol.read_input(protocol.parse_message(protocol.dumps_message(msg))))
                self.check_receivers(messages, *row)
                for backend in (capture_x11, capture_portal):
                    events = []
                    hook = backend.Hooks(lambda *args: events.append(args), lambda *args: False,
                                         lambda *args: None)
                    if backend is capture_x11:
                        hook._connection = SimpleNamespace(keymap=None)
                        for down in (True, False):
                            hook._key(evdev + 8, down, 0, 0, False)
                    else:
                        device = object()
                        for down in (True, False):
                            hook._key_event(evdev, down, device)
                    self.assertEqual([(e[0], e[1]) for e in events], [(name, True), (name, False)])

    def test_windows_numlock_off_navigation_and_main_enter_remain_distinct(self):
        case = windows.WindowsReleaseTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        for vk, scan, name in ((0x24, 71, "numpad_7"), (0x2e, 83, "numpad_decimal"),
                               (0x0c, 76, "numpad_5")):
            case.feed(vk, True, scan)
            case.feed(vk, False, scan)
            self.assertEqual([m["key"] for m in case.messages()], [name, name])
        case.feed(0x0d, True, 28)
        case.feed(0x0d, True, 28, 1)
        case.feed(0x0d, False, 28)
        case.feed(0x0d, False, 28, 1)
        self.assertEqual([m["key"] for m in case.messages()],
                         ["enter", "numpad_enter", "enter", "numpad_enter"])
        self.assertEqual(case.sender._keys_down, {})


if __name__ == "__main__":
    unittest.main()
