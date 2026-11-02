"""GH16 synthetic Linux scroll through the wire parser and Mac injector's CGEvent call.

Run in a Claude session, one module per process. Quartz posting is replaced; no live input.
"""

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "mac_app"))

import input_injector_mac as injector
from core import protocol, receiver
from core.tests.test_wayland_scroll_wire import ScrollPath


class WaylandScrollMacTests(unittest.TestCase):
    def setUp(self):
        self.quartz = mock.Mock(kCGScrollEventUnitLine=1, kCGScrollEventUnitPixel=0)
        patch = mock.patch.object(injector, "Quartz", self.quartz)
        patch.start()
        self.addCleanup(patch.stop)
        injector.reset_scroll_remainders()
        self.addCleanup(injector.reset_scroll_remainders)
        self.responder = receiver.LinkResponder(None, lambda: {}, status_callback=None, injector=injector)
        self.responder.input_scale = receiver.InputScale()
        self.link = SimpleNamespace(peer_platform="linux")

    def deliver(self, messages):
        for seq, message in enumerate(messages, 1):
            message["data"]["seq"] = seq
            read = protocol.read_input(json.loads(json.dumps(message)))
            self.assertIsNotNone(read)
            self.responder._inject_locked(self.link, read)

    def test_gnome_wheel_posts_three_lines_per_notch_on_both_axes(self):
        for x, y, want_y, want_x in ((0, 120, -3, 0), (0, -120, 3, 0),
                                    (120, 0, 0, -3), (-120, 0, 0, 3)):
            with self.subTest(x=x, y=y):
                self.quartz.reset_mock()
                path = ScrollPath()
                path.feed((603, x, y), (100,))
                self.deliver(path.messages())
                self.quartz.CGEventCreateScrollWheelEvent.assert_called_once_with(None, 1, 2, want_y, want_x)
                self.quartz.CGEventPost.assert_called_once()

    def test_high_resolution_wheel_accumulates_instead_of_disappearing(self):
        path = ScrollPath()
        for _ in range(8):
            path.feed((603, 0, 15), (100,))
        self.deliver(path.messages())
        self.assertEqual(self.quartz.CGEventCreateScrollWheelEvent.call_args_list,
                         [mock.call(None, 1, 2, -1, 0)] * 3)

    def test_continuous_delta_and_stop_reach_mac(self):
        path = ScrollPath()
        path.feed((600, 0, 10), (601,), (100,))
        self.deliver(path.messages())
        self.quartz.CGEventCreateScrollWheelEvent.assert_called_once_with(None, 1, 2, -3, 0)

    def test_low_scroll_speed_preserves_the_remainder(self):
        self.responder.input_scale = receiver.InputScale(scroll=0.25)
        path = ScrollPath()
        for _ in range(4):
            path.feed((603, 0, 120), (100,))
        self.deliver(path.messages())
        self.assertEqual(self.quartz.CGEventCreateScrollWheelEvent.call_args_list,
                         [mock.call(None, 1, 2, -1, 0)] * 3)

    def test_pixel_wire_input_keeps_pixel_units(self):
        self.deliver([protocol.scroll_msg(-10, 5, "pixel")])
        self.quartz.CGEventCreateScrollWheelEvent.assert_called_once_with(None, 0, 2, -10, 5)


if __name__ == "__main__":
    unittest.main()
