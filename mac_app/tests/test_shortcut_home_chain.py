"""The owner's rc14 log, 10-10-2026: home -> Loan laptop (edge) -> Desk PC (peer switch) -> Loan laptop
(peer switch), then a double tap of right Command on the Mac's own keyboard did nothing, and logged
nothing. Rebuilt with the real owner and the real `switch`/`accept` handling, links faked."""

import logging
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from bridge import KVMController
from core import protocol
from bridge_fakes import PAIRED_TOKEN, FakeClipboard, FakeClock, FakeQuartz, crossing_config, quiet_logger, settle
from fake_link import OTHER_ID, PEER_ID, FakeLink

FEDORA_ID = bytes(range(81, 97))
CMD_R = 0x36
DOWN = FakeQuartz.kCGEventFlagMaskCommand | 0x0010 | 0x0100   # command, right-command device bit, non-coalesced
UP = 0x0100


class Chain(unittest.TestCase):
    style = "double_tap"

    def setUp(self):
        FakeQuartz.reset_cursor_spies()
        self.clock = FakeClock()
        cfg = crossing_config(PAIRED_TOKEN, methods=["edge", "shortcut"], shortcut_goes_to="last")
        cfg.trigger_key = "cmd_r"
        cfg.trigger_style = self.style
        cfg.double_tap_ms = 300
        self.controller = KVMController(
            cfg, logger=quiet_logger(), quartz=FakeQuartz, clock=self.clock, clipboard=FakeClipboard("x"),
            link_factory=FakeLink, desktop_bounds=lambda: (0, 0, 1920, 1080))
        self.addCleanup(self.controller.stop_event.set)
        c = self.controller
        self.own = protocol.id_text(c.identity()["id"])
        with c.book.lock:
            peers = c.book.data["peers"]
            peers.append({**peers[0], "id": protocol.id_text(OTHER_ID), "token": "acg", "send": True})
            peers.append({**peers[0], "id": protocol.id_text(FEDORA_ID), "token": "fedora", "send": True})
        c._sync_links()
        self.win = c._primary_link
        self.acg = c.links["acg"]
        self.fed = c.links["fedora"]
        self.win.up(c, ident=PEER_ID, name="Desk PC", platform="windows")
        self.acg.up(c, ident=OTHER_ID, name="Loan laptop", platform="windows")
        self.fed.up(c, ident=FEDORA_ID, name="fedora", platform="linux")

    def tap(self, event_type, event):
        return self.controller._event_tap_callback(None, event_type, event, None)

    def key(self, down, injected=False):
        event = {FakeQuartz.kCGKeyboardEventKeycode: CMD_R, "flags": DOWN if down else UP}
        return event

    def chain_to_acg(self):
        c = self.controller
        # 1. the tap's engine crosses at the right edge to Loan laptop
        self.assertTrue(c.set_redirecting(True, edge="left", offset=0.5, peer=protocol.id_text(OTHER_ID), reason="edge"))
        self.acg.arrives(c, protocol.accept_msg(c.owner.route))
        # 2. Loan laptop switches to Desk PC
        self.acg.arrives(c, protocol.switch_v6(c.owner.route, PEER_ID, "left", 0.5))
        self.win.arrives(c, protocol.accept_msg(c.owner.route))
        self.assertEqual(c.owner.on, protocol.id_text(PEER_ID))
        # 3. Desk PC switches back to Loan laptop
        self.win.arrives(c, protocol.switch_v6(c.owner.route, OTHER_ID, "left", 0.5))
        self.acg.arrives(c, protocol.accept_msg(c.owner.route))
        self.assertEqual(c.owner.on, protocol.id_text(OTHER_ID))
        self.assertTrue(c.redirecting)

    def double_tap(self, gap=0.1):
        results = []
        for step in range(2):
            results.append(self.tap(FakeQuartz.kCGEventFlagsChanged, self.key(True)))
            self.clock.value += 0.05
            results.append(self.tap(FakeQuartz.kCGEventFlagsChanged, self.key(False)))
            self.clock.value += gap
        return results

    def assertHome(self):
        settle(self.controller)
        c = self.controller
        self.assertFalse(c.redirecting)
        self.assertIsNone(c.owner.on)


class DoubleTapChainTests(Chain):
    def _use_next_cycle(self):
        self.controller.cfg.crossing["shortcut_goes_to"] = "next"
        order = [protocol.id_text(OTHER_ID), protocol.id_text(FEDORA_ID)]
        self.controller._shortcut_cycle = lambda: order
        self.controller._ready = lambda _ident: True

    def test_next_target_after_a_local_move(self):
        self._use_next_cycle()
        c = self.controller
        self.assertTrue(c.set_redirecting(True, peer=protocol.id_text(OTHER_ID)))
        self.acg.arrives(c, protocol.accept_msg(c.owner.route))

        self.assertEqual(c.owner.on, protocol.id_text(OTHER_ID))
        self.assertEqual(c._cycle_target(), protocol.id_text(FEDORA_ID))

    def test_next_target_passes_over_a_machine_that_just_refused_and_comes_home(self):
        # The owner ignores a move to a machine it holds back, so choosing it left input where it was.
        self._use_next_cycle()
        c = self.controller
        self.assertTrue(c.set_redirecting(True, peer=protocol.id_text(OTHER_ID)))
        self.acg.arrives(c, protocol.accept_msg(c.owner.route))
        c.owner._hold_back(protocol.id_text(FEDORA_ID), "refused")

        self.assertIsNone(c._cycle_target())

    def test_next_target_after_a_peer_requested_move(self):
        self._use_next_cycle()
        c = self.controller
        self.assertTrue(c.set_redirecting(True, peer=protocol.id_text(PEER_ID)))
        self.win.arrives(c, protocol.accept_msg(c.owner.route))
        self.win.arrives(c, protocol.switch_v6(c.owner.route, OTHER_ID, "left", 0.5))
        self.acg.arrives(c, protocol.accept_msg(c.owner.route))

        self.assertEqual(c.owner.on, protocol.id_text(OTHER_ID))
        self.assertEqual(c._cycle_target(), protocol.id_text(FEDORA_ID))

    def test_baseline_input_is_home_and_double_tap_goes_away(self):
        self.double_tap()
        self.assertTrue(self.controller.redirecting)

    def test_double_tap_brings_input_home_after_the_log_sequence(self):
        self.chain_to_acg()
        with self.assertLogs(self.controller.logger, level="INFO") as said:
            results = self.double_tap()
        self.assertHome()
        self.assertTrue(any("input returned to this Mac" in line for line in said.output), said.output)
        self.assertEqual(results, [results[0], None, None, None] if results[0] is not None else [None] * 4)

    def test_double_tap_while_receiving_too(self):
        self.chain_to_acg()
        self.controller.receiving = True
        self.controller.send_peer_home = lambda: True
        self.double_tap()
        self.assertHome()

    def test_redirecting_true_but_owner_home(self):
        self.chain_to_acg()
        self.controller.owner.on = None
        self.controller.redirecting = True
        self.double_tap()
        settle(self.controller)
        self.assertFalse(self.controller.redirecting)

    def test_owner_on_but_redirecting_false_moves_on_rather_than_home_but_logs(self):
        # Not reachable by the log's sequence (redirecting was logged True). A redirecting=False /
        # owner.on=ACG disagreement reads the double tap as "go away", and logs it.
        self.chain_to_acg()
        self.controller.redirecting = False
        with self.assertLogs(self.controller.logger, level="INFO") as said:
            self.double_tap()
        settle(self.controller)
        self.assertTrue(any("move applied" in line for line in said.output), said.output)

    def test_the_switch_chain_racing_the_double_tap_always_ends_home(self):
        import threading
        lost = 0
        for _ in range(300):
            self.setUp()
            c = self.controller
            c.set_redirecting(True, edge="left", offset=0.5, peer=protocol.id_text(OTHER_ID), reason="edge")
            self.acg.arrives(c, protocol.accept_msg(c.owner.route))
            self.acg.arrives(c, protocol.switch_v6(c.owner.route, PEER_ID, "left", 0.5))
            self.win.arrives(c, protocol.accept_msg(c.owner.route))
            route = c.owner.route

            def step_3():
                self.win.arrives(c, protocol.switch_v6(route, OTHER_ID, "left", 0.5))
                self.acg.arrives(c, protocol.accept_msg(route + 1))

            worker = threading.Thread(target=step_3, daemon=True)
            worker.start()
            self.double_tap()
            worker.join(2)
            settle(c)
            self.assertEqual(c.redirecting, c.owner.away)
            lost += c.redirecting
        self.assertEqual(lost, 0)

    def test_a_synced_settings_without_the_shortcut_method_swallows_it_silently(self):
        self.chain_to_acg()
        cfg = crossing_config(PAIRED_TOKEN, methods=["edge"])
        cfg.trigger_key = "cmd_r"
        cfg.trigger_style = "double_tap"
        self.controller.apply_settings(cfg)
        with self.assertLogs(self.controller.logger, level="DEBUG") as said:
            self.controller.logger.debug("sentinel")
            results = self.double_tap()
        self.assertEqual(len(said.output), 1)
        self.assertTrue(self.controller.redirecting)
        self.assertEqual(results, [None] * 4)

    def test_double_tap_arriving_while_step_3_is_mid_flight(self):
        c = self.controller
        c.set_redirecting(True, edge="left", offset=0.5, peer=protocol.id_text(OTHER_ID), reason="edge")
        self.acg.arrives(c, protocol.accept_msg(c.owner.route))
        self.acg.arrives(c, protocol.switch_v6(c.owner.route, PEER_ID, "left", 0.5))
        self.win.arrives(c, protocol.accept_msg(c.owner.route))
        # Desk PC's switch is in: Loan laptop asked, its accept not yet back
        self.win.arrives(c, protocol.switch_v6(c.owner.route, OTHER_ID, "left", 0.5))
        self.assertIsNotNone(c.owner._wait)
        self.double_tap()
        settle(c)
        self.assertFalse(c.redirecting)
        self.assertIsNone(c.owner.on)
        # the late accept must not take input back
        self.acg.arrives(c, protocol.accept_msg(c.owner.route))
        self.assertFalse(c.redirecting)

    def test_double_tap_with_a_stale_modifier_bookkeeping(self):
        self.chain_to_acg()
        self.controller.translator.modifier_down.add(CMD_R)
        self.double_tap()
        self.assertHome()

    def test_double_tap_after_a_pause_since_the_last_move(self):
        self.chain_to_acg()
        self.clock.value += 30
        self.double_tap()
        self.assertHome()

    def test_second_press_slower_than_the_threshold_does_nothing(self):
        self.chain_to_acg()
        self.double_tap(gap=0.5)
        self.assertTrue(self.controller.redirecting)


class HoldChainTests(Chain):
    style = "hold"

    def test_hold_from_the_chain_comes_home_on_release(self):
        self.chain_to_acg()
        down = self.tap(FakeQuartz.kCGEventFlagsChanged, self.key(True))
        self.assertIsNone(down)
        # hold: key down from redirecting asks for a take of the hold target
        settle(self.controller)
        self.tap(FakeQuartz.kCGEventFlagsChanged, self.key(False))
        settle(self.controller)
        self.assertFalse(self.controller.redirecting)
        self.assertIsNone(self.controller.owner.on)


class HoldWithEveryCycleMachineHeldBack(Chain):
    style = "hold"

    def test_hold_with_every_cycle_machine_held_back_moves_nothing(self):
        # hold_target names nobody, and a peer of None would fall back to a machine outside the cycle.
        c = self.controller
        c.cfg.crossing["shortcut_goes_to"] = "ticked"
        c._shortcut_cycle = lambda: [protocol.id_text(OTHER_ID), None]
        c.owner._hold_back(protocol.id_text(OTHER_ID), "refused")
        self.tap(FakeQuartz.kCGEventFlagsChanged, self.key(True))
        settle(c)
        self.assertIsNone(c.owner.on)
        self.assertFalse(c.redirecting)


class ArrivalHomeGrace(Chain):
    """The same chain ending as rc14's did at 19:50:51: a switch home at the Mac's bottom edge."""

    def test_a_switch_home_at_an_edge_starts_the_grace_and_a_rebuild_keeps_it(self):
        self.chain_to_acg()
        c = self.controller
        self.acg.arrives(c, protocol.switch_v6(c.owner.route, bytes(c.identity()["id"]), "bottom", 0.0))
        settle(c)
        self.assertFalse(c.redirecting)
        self.assertIsNotNone(c.crossing._grace_at, "no grace after landing home")
        c.zones_changed()
        self.assertIsNotNone(c.crossing._grace_at, "a rebuilt engine dropped the grace")


if __name__ == "__main__":
    unittest.main()
