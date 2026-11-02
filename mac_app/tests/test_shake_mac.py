"""Synthetic Quartz deltas and fake links, without AppKit UI or real input."""

import copy
import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from bridge import KVMController
from bridge_fakes import FakeClock, FakeClipboard, FakeQuartz, PAIRED_TOKEN, make_config, quiet_logger, redirect_to
from fake_link import FakeLink
from core import profiles, receiver
from core.tests.test_profiles import B, raw
from core.tests.test_shake import qualifying_trace


class ShakeMacTests(unittest.TestCase):
    def setUp(self):
        FakeQuartz.hid_keys = set()
        FakeQuartz.hid_buttons = set()
        self.clock = FakeClock(0)
        self.held, _ = profiles.before_load(raw(), now=100)
        self.held, _ = profiles.after_load(self.held, platform="mac", now=100)
        self.held["peers"][0].update(token=PAIRED_TOKEN, name="Desk PC")
        self.controller = KVMController(make_config(), quartz=FakeQuartz, clock=self.clock,
            clipboard=FakeClipboard(), logger=quiet_logger(), link_factory=FakeLink)
        self.controller.book = receiver.PeerBook(lambda: self.held, self.save, threading.RLock())
        self.alerts = []
        self.controller.on_user_alert = lambda title, text: self.alerts.append(text)
        self.controller._set_cursor_follows_mouse = lambda value: None
        self.controller._warp_cursor_to_pin = lambda: None
        self.controller._capture_cursor_pin_point = lambda: (0, 0)
        redirect_to(self.controller, B)

    def save(self, settings):
        self.held = copy.deepcopy(settings)

    def trace(self, start=.31):
        results = []
        self.firing = None
        for t, dx, dy in qualifying_trace(start=start):
            self.clock.value = t
            event = {FakeQuartz.kCGMouseEventDeltaX: dx, FakeQuartz.kCGMouseEventDeltaY: dy}
            away = self.controller.redirecting
            results.append(self.controller._event_tap_callback(None, FakeQuartz.kCGEventMouseMoved, event, None))
            if away and not self.controller.redirecting and self.firing is None:
                self.firing = results[-1]
        # The notice is written off the event tap, on its own thread.
        for thread in threading.enumerate():
            if thread.name == "Beamer-shake-notice":
                thread.join(2)
        return results

    def start_redirecting_with_trigger(self):
        self.controller.set_redirecting(False)
        self.controller._primary_link.up(self.controller, ident=bytes([2]) * 16, name="Desk PC")
        down = {FakeQuartz.kCGKeyboardEventKeycode: 0x3D, "flags": FakeQuartz.kCGEventFlagMaskAlternate}
        up = {FakeQuartz.kCGKeyboardEventKeycode: 0x3D, "flags": 0}
        for t, event in ((10.0, down), (10.03, up), (10.2, down), (10.22, up)):
            self.clock.value = t
            self.controller._event_tap_callback(None, FakeQuartz.kCGEventFlagsChanged, event, None)
        self.assertTrue(self.controller.redirecting)
        self.assertFalse(self.controller.shake._held)

    def test_first_shake_after_trigger_double_tap_returns_home_without_intervening_motion(self):
        self.start_redirecting_with_trigger()

        self.trace(start=10.7)

        self.assertFalse(self.controller.redirecting)
        self.assertIsNone(self.controller.owner.on)

    def test_first_shake_after_trigger_double_tap_returns_home_after_small_move(self):
        self.start_redirecting_with_trigger()
        self.clock.value = 10.3
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventMouseMoved,
            {FakeQuartz.kCGMouseEventDeltaX: 1, FakeQuartz.kCGMouseEventDeltaY: 0}, None)

        self.trace(start=10.7)

        self.assertFalse(self.controller.redirecting)
        self.assertIsNone(self.controller.owner.on)

    def test_shake_returns_home_once_and_drops_firing_motion(self):
        returns = []
        original = self.controller.set_redirecting

        def home(*args, **kwargs):
            returns.append((args, kwargs))
            return original(*args, **kwargs)

        self.controller.set_redirecting = home
        self.trace()
        self.assertIsNone(self.firing, "the event that fired the shake reached this Mac's pointer")
        self.assertFalse(self.controller.redirecting)
        self.assertIsNone(self.controller.owner.on)
        self.assertEqual(len(returns), 1)
        self.assertTrue(returns[0][1]["came_home"])
        self.assertEqual(self.held["shake_notices"], 1)
        self.assertEqual(len(self.alerts), 1)

    def test_float_deltas_reach_detector_before_integer_rounding(self):
        deltas = []
        self.controller.shake.motion = lambda t, dx, dy: deltas.append((dx, dy)) or True
        self.clock.value = .4
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventMouseMoved,
            {FakeQuartz.kCGMouseEventDeltaX: .25, FakeQuartz.kCGMouseEventDeltaY: -.75}, None)
        self.assertEqual(deltas, [(.25, -.75)])
        self.assertTrue(self.controller.outbound.empty())
        self.assertFalse(self.controller.redirecting)

    def test_button_and_key_hold_prevent_shake_until_all_released(self):
        self.clock.value = .3
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventLeftMouseDown, {}, None)
        key = {FakeQuartz.kCGKeyboardEventKeycode: 0, "unicode": "a"}
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventKeyDown, key, None)
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventLeftMouseUp, {}, None)
        self.trace()
        self.assertTrue(self.controller.redirecting)
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventKeyUp, key, None)
        self.trace(start=1.1)
        self.assertFalse(self.controller.redirecting)

    def test_returning_input_clears_keys_released_while_receiving(self):
        self.clock.value = .3
        shift = {FakeQuartz.kCGKeyboardEventKeycode: 56, "unicode": ""}
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventKeyDown, shift, None)
        self.controller.set_receiving(True)
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventKeyUp, shift, None)
        self.controller.set_receiving(False)
        redirect_to(self.controller, B)

        self.trace(start=1.1)

        self.assertFalse(self.controller.redirecting)
        self.assertIsNone(self.controller.owner.on)

    def test_returning_input_keeps_keys_still_down(self):
        shift = {FakeQuartz.kCGKeyboardEventKeycode: 56, "unicode": ""}
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventKeyDown, shift, None)
        FakeQuartz.hid_keys.add(56)
        self.controller.set_receiving(True)
        self.controller.set_receiving(False)

        self.assertIn(("key", 56), self.controller._shake_presses)

    def test_returning_input_maps_middle_button_to_hid_index(self):
        event = {FakeQuartz.kCGMouseEventButtonNumber: 2}
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventOtherMouseDown, event, None)
        FakeQuartz.hid_buttons.add(2)
        self.controller.set_receiving(True)
        self.controller.set_receiving(False)

        self.assertIn(("button", "middle"), self.controller._shake_presses)

    def test_reconcile_keeps_fresh_button_down_during_hid_query(self):
        self.clock.value = .3
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventLeftMouseDown, {}, None)
        button_state = FakeQuartz.CGEventSourceButtonState

        def fresh_down(cls, source, button):
            down = button_state(source, button)
            if button == 0:
                self.controller._event_tap_callback(None, FakeQuartz.kCGEventLeftMouseDown, {}, None)
            return down

        with patch.object(FakeQuartz, "CGEventSourceButtonState", classmethod(fresh_down)):
            self.controller._shake_reconcile_input_state()

        self.assertIn(("button", "left"), self.controller._shake_presses)
        self.trace(start=1.1)
        self.assertTrue(self.controller.redirecting)

    def test_reconcile_removes_button_released_at_hid(self):
        self.clock.value = .3
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventLeftMouseDown, {}, None)

        self.controller._shake_reconcile_input_state()

        self.assertNotIn(("button", "left"), self.controller._shake_presses)
        self.trace(start=1.1)
        self.assertFalse(self.controller.redirecting)

    def test_scroll_resets_motion_and_starts_quiet_period(self):
        self.clock.value = .3
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventScrollWheel, {}, None)
        for t, dx, dy in qualifying_trace(start=.31):
            if t > .55:
                break
            self.clock.value = t
            self.controller._event_tap_callback(None, FakeQuartz.kCGEventMouseMoved,
                {FakeQuartz.kCGMouseEventDeltaX: dx, FakeQuartz.kCGMouseEventDeltaY: dy}, None)
        self.assertTrue(self.controller.redirecting)
        self.trace(start=1.1)
        self.assertFalse(self.controller.redirecting)

    def test_settings_disable_shake_while_away(self):
        self.held["crossing_own"]["shake"] = False
        self.controller.zones_changed()
        self.trace()
        self.assertTrue(self.controller.redirecting)
        self.assertFalse(self.alerts)

    def test_press_started_at_home_still_blocks_shake_after_leaving(self):
        self.controller.set_redirecting(False)
        key = {FakeQuartz.kCGKeyboardEventKeycode: 0, "unicode": "a"}
        other = {FakeQuartz.kCGKeyboardEventKeycode: 1, "unicode": "s"}
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventKeyDown, key, None)
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventKeyDown, other, None)
        redirect_to(self.controller, B)
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventKeyUp, key, None)
        self.trace()
        self.assertTrue(self.controller.redirecting)
        self.controller._event_tap_callback(None, FakeQuartz.kCGEventKeyUp, other, None)
        self.trace(start=1.1)
        self.assertFalse(self.controller.redirecting)

    def test_notice_stops_after_three_returns(self):
        self.held["shake_notices"] = 3
        self.trace()
        self.assertFalse(self.controller.redirecting)
        self.assertEqual(self.held["shake_notices"], 3)
        self.assertFalse(self.alerts)

    def test_home_hold_resets_edge_pressure_until_mouse_has_been_quiet(self):
        self.trace()
        feeds = []
        from crossing import CrossingEngine
        self.controller.crossing = CrossingEngine.from_config(dict(self.controller.cfg.crossing, methods=["edge"]))
        self.controller._crossing_ready = lambda peer: True
        self.controller._current_desktop_bounds = lambda: (0, 0, 100, 100)
        self.controller._current_displays = lambda: []
        from crossing import Step
        self.controller.crossing.feed = lambda *args, **kwargs: feeds.append(args) or Step()
        event = {"location": (99, 50), FakeQuartz.kCGMouseEventDeltaX: 20}
        self.clock.value += .1
        self.controller._handle_local_mouse(FakeQuartz.kCGEventMouseMoved, event)
        self.assertFalse(feeds)
        self.clock.value += .201
        self.controller._handle_local_mouse(FakeQuartz.kCGEventMouseMoved, event)
        self.assertEqual(len(feeds), 1)


if __name__ == "__main__":
    unittest.main()
