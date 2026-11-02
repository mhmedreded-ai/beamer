import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import AppKit
import Quartz
from unittest import mock

from frame_clock import PREVIEW_MAX_RATE, FrameClock, set_divisor


class FrameClockTests(unittest.TestCase):
    def test_preferred_rate_uses_the_display_divisor(self):
        ranges = []

        class Link:
            def setPreferredFrameRateRange_(self, value):
                ranges.append(value)

            def addToRunLoop_forMode_(self, *_args):
                pass

            def invalidate(self):
                pass

        link = Link()
        screen = mock.Mock()
        screen.maximumFramesPerSecond.return_value = 120
        # A view in a window, as the previews give it: NSScreen itself cannot be patched under PyObjC.
        view = mock.Mock()
        view.window.return_value.screen.return_value = screen
        view.displayLinkWithTarget_selector_.return_value = link
        try:
            with mock.patch.object(Quartz, "CAFrameRateRangeMake", side_effect=lambda *args: args):
                for divisor, expected in ((1, (30, 120, 120)), (2, (30, 60, 60)), (4, (30, 30, 30))):
                    set_divisor(divisor)
                    clock = FrameClock(view, lambda: None)
                    clock.start()
                    clock.stop()
                    self.assertEqual(ranges[-1], expected)
        finally:
            set_divisor(1)

    def test_a_preview_clock_runs_at_sixty_at_most(self):
        ranges = []
        link = mock.Mock()
        link.setPreferredFrameRateRange_.side_effect = ranges.append
        screen = mock.Mock()
        screen.maximumFramesPerSecond.return_value = 120
        view = mock.Mock()
        view.window.return_value.screen.return_value = screen
        view.displayLinkWithTarget_selector_.return_value = link
        with mock.patch.object(Quartz, "CAFrameRateRangeMake", side_effect=lambda *args: args):
            for max_rate, expected in ((None, (30, 120, 120)), (PREVIEW_MAX_RATE, (30, 60, 60))):
                clock = FrameClock(view, lambda: None, max_rate)
                clock.start()
                clock.stop()
                self.assertEqual(ranges[-1], expected)

    def test_main_screen_clock_fires_in_the_common_run_loop_and_stops(self):
        calls = []
        clock = FrameClock(None, lambda: calls.append(1))
        clock.start()
        self.assertTrue(clock.is_alive())
        run_loop = AppKit.NSRunLoop.mainRunLoop()
        deadline = datetime.now() + timedelta(seconds=0.2)
        while datetime.now() < deadline and len(calls) < 3:
            run_loop.runUntilDate_(AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.01))
        self.assertGreaterEqual(len(calls), 3)
        clock.stop()
        self.assertFalse(clock.is_alive())
        stopped_at = len(calls)
        run_loop.runUntilDate_(AppKit.NSDate.dateWithTimeIntervalSinceNow_(0.05))
        self.assertEqual(len(calls), stopped_at)


if __name__ == "__main__":
    unittest.main()
