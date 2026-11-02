import os
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import input_injector_mac as injector
from core import protocol, receiver


class FakeQuartz:
    kCGEventSourceUserData = 42
    kCGScrollEventUnitLine = 1
    kCGScrollEventUnitPixel = 0
    kCGHIDEventTap = 0

    def __init__(self):
        self.posted = []

    def CGEventCreateScrollWheelEvent(self, source, unit, count, wheel_y, wheel_x):
        return types.SimpleNamespace(unit=unit, wheel_y=wheel_y, wheel_x=wheel_x)

    def CGEventSetIntegerValueField(self, event, field, value):
        pass

    def CGEventSetFlags(self, event, flags):
        event.flags = flags

    def CGEventPost(self, tap, event):
        self.posted.append(event)


class WheelLinesTests(unittest.TestCase):
    def setUp(self):
        self.quartz = FakeQuartz()
        self._saved_quartz = injector.Quartz
        injector.Quartz = self.quartz
        injector.release_all()
        injector.reset_scroll_remainders()
        self.responder = receiver.LinkResponder.__new__(receiver.LinkResponder)
        self.responder._injector = injector
        self.responder.input_scale = None
        self.link = receiver._Link(None, None, b"p", b"k", "host", "PC", [], b"o", [], True, "windows")

    def tearDown(self):
        injector.release_all()
        injector.reset_scroll_remainders()
        injector.Quartz = self._saved_quartz

    def scroll(self, dy=1, dx=0, mode="line"):
        self.responder._inject_locked(self.link, {
            "type": protocol.MSG_SCROLL, "dy": dy, "dx": dx, "mode": mode,
        })
        return self.quartz.posted[-1]

    def test_pc_line_notch_posts_three_lines_and_mac_notch_posts_one(self):
        self.assertEqual(self.scroll().wheel_y, 3)
        self.link.peer_platform = "macos"
        self.assertEqual(self.scroll().wheel_y, 1)
        self.link.peer_platform = "ios"
        self.assertEqual(self.scroll().wheel_y, 1)

    def test_scroll_speed_multiplier_applies_to_pc_line_notches(self):
        self.responder.input_scale = receiver.InputScale(scroll=2.0)
        self.assertEqual(self.scroll().wheel_y, 6)

    def test_pixel_mode_keeps_its_pixel_amount_for_a_pc(self):
        event = self.scroll(dy=2, mode="pixel")
        self.assertEqual((event.unit, event.wheel_y), (FakeQuartz.kCGScrollEventUnitPixel, 2))


if __name__ == "__main__":
    unittest.main()
