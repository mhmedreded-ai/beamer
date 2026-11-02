"""Receiver backpressure and input ordering over authenticated encrypted loopback sockets."""

import itertools
import json
import socket
import threading
import time
import unittest
from unittest import mock

from core import protocol, receiver
from core.tests.responder_harness import B, Initiator, Machine, entry, wait_for


class ReceiverBacklogTests(unittest.TestCase):
    def owner(self):
        machine = Machine([entry(B, "Driver")]).start()
        self.addCleanup(machine.stop)
        link = Initiator(machine, B)
        self.addCleanup(link.close)
        link.handshake()
        route = link.take()
        self.assertEqual(link.answer(route)[0], protocol.MSG_ACCEPT)
        return machine, link

    def test_1000_hz_motion_stays_current_with_a_slow_injector(self):
        self.load(drag=False)

    def test_1000_hz_drag_stays_current_and_releases_the_button(self):
        self.load(drag=True)

    def test_1000_hz_bending_hand_drag_stays_current(self):
        # GH15c: a real hand bends on almost every 1000 Hz sample; 1.5.10 replayed each bend natively
        # and fell seconds behind.
        self.load(drag=True, path=((2, 0), (1, 2), (-2, 1), (-1, -2), (2, -1)))

    def load(self, drag, path=((1, 0),)):
        machine, link = self.owner()
        if drag:
            pressed = link.input({"type": "mousedown", "data": {"button": "left"}})
            self.assertIsNotNone(link.acked(pressed))
        first_seq = link.seq + 1
        last_seq = link.seq + 400
        original = machine.injector.inject_mouse_move
        completed, sent = [], {}
        dispatch = machine.responder._dispatch
        peak_pending = 0

        def slow(dx, dy):
            time.sleep(0.004)
            original(dx, dy)

        def measured(inbound, message):
            dispatch(inbound, message)
            read = protocol.read_input(message)
            if read is not None and read["type"] == protocol.MSG_MOUSEMOVE:
                completed.append((read["seq"], time.monotonic()))

        machine.injector.inject_mouse_move = slow
        machine.responder._dispatch = measured
        started = time.monotonic()
        for index in range(400):
            deadline = started + index / 1000
            time.sleep(max(0, deadline - time.monotonic()))
            sent[index + first_seq] = time.monotonic()
            link.move(*path[index % len(path)])
            highest = completed[-1][0] if completed else first_seq - 1
            peak_pending = max(peak_pending, index + first_seq - highest)
        sending_seconds = time.monotonic() - started
        self.assertIsNotNone(link.acked(last_seq, timeout=8))
        self.assertTrue(wait_for(lambda: completed and completed[-1][0] == last_seq))
        delays = sorted(done - sent[seq] for seq, done in completed)
        moves = [call for call in machine.injected() if call[0] == "move"]
        metrics = {
            "motion_hz": 1000, "events": 400, "injector_delay_ms": 4, "drag": drag,
            "sending_ms": round(sending_seconds * 1000, 2),
            "native_calls": len(moves), "peak_pending_input_samples": peak_pending,
            "latency_p95_ms": round(delays[int((len(delays) - 1) * .95)] * 1000, 2),
            "latency_max_ms": round(max(delays) * 1000, 2),
            "final_latency_ms": round((completed[-1][1] - sent[last_seq]) * 1000, 2),
        }
        print("receiver backlog load " + json.dumps(metrics, sort_keys=True), flush=True)
        self.assertEqual(sum(call[1] for call in moves), sum(path[index % len(path)][0] for index in range(400)))
        self.assertEqual(sum(call[2] for call in moves), sum(path[index % len(path)][1] for index in range(400)))
        if drag:
            released = link.input({"type": "mouseup", "data": {"button": "left"}})
            self.assertIsNotNone(link.acked(released))
            self.assertEqual(machine.injected()[0], ("button", "left", True))
            self.assertEqual(machine.injected()[-1], ("button", "left", False))
        self.assertLess(metrics["final_latency_ms"], 200, metrics)
        self.assertLess(metrics["latency_p95_ms"], 100, metrics)
        self.assertLess(peak_pending, 100, metrics)
        self.assertLess(len(moves), 200, metrics)

    def test_pending_moves_merge_without_crossing_input_or_focus_barriers(self):
        machine, link = self.owner()
        entered, release = threading.Event(), threading.Event()
        original = machine.injector.inject_mouse_move

        def blocked(dx, dy):
            if not entered.is_set():
                entered.set()
                release.wait(3)
            original(dx, dy)

        machine.injector.inject_mouse_move = blocked
        self.addCleanup(release.set)
        link.move(1, 0)
        self.assertTrue(entered.wait(1))
        link.move(2, 0)
        link.move(4, 0)
        link.input({"type": "mousedown", "data": {"button": "left"}})
        link.move(3, 2)
        link.move(5, 4)
        link.input(protocol.scroll_msg(2, -1))
        link.key("keydown", "a")
        link.move(-2, 1)
        link.move(-3, 2)
        link.key("keyup", "a")
        link.input({"type": "mouseup", "data": {"button": "left"}})
        last = link.move(20, 30)
        # The blocked native call must not also stop frame receipt.
        inbound = machine.responder._links[B]
        self.assertTrue(wait_for(lambda: inbound.inbox and
                                 inbound.inbox[-1][0].get("type") == "mousedown", 1))
        self.assertLess(inbound.dealt, last)
        release.set()
        self.assertIsNotNone(link.acked(last))
        link.let_go()
        dropped = link.move(20, 30)
        self.assertIsNotNone(link.acked(dropped))
        calls = machine.injected()
        self.assertEqual(calls[:2], [("move", 1, 0), ("move", 6, 0)])
        grouped = []
        for moves, group in itertools.groupby(calls, key=lambda call: call[0] == "move"):
            values = list(group)
            if moves:
                grouped.append(("move", sum(call[1] for call in values), sum(call[2] for call in values)))
            else:
                grouped.extend(values)
        self.assertEqual(grouped, [
            ("move", 7, 0), ("button", "left", True),
            ("move", 8, 6), ("scroll", 2, -1, "line"), ("key", "a", True),
            ("move", -5, 3), ("key", "a", False), ("button", "left", False), ("move", 20, 30),
        ])

    def test_focus_then_large_clipboard_uses_ordered_ownership(self):
        machine, link = self.owner()
        link.let_go()
        self.assertTrue(wait_for(lambda: not machine.responder.driven))
        entered, release = threading.Event(), threading.Event()
        focus = machine.responder._on_focus

        def delayed(inbound, message):
            entered.set()
            release.wait(2)
            focus(inbound, message)

        machine.responder._on_focus = delayed
        self.addCleanup(release.set)
        route = link.take()
        self.assertTrue(entered.wait(1))
        text = "x" * (protocol.SMALL_MESSAGE_BYTES + 1)
        message = protocol.clipboard_msg(text)
        self.assertGreater(len(protocol.dumps_message(message)), protocol.SMALL_MESSAGE_BYTES)
        link.send(message)
        release.set()
        self.assertEqual(link.answer(route)[0], protocol.MSG_ACCEPT)
        self.assertTrue(wait_for(lambda: (text, None) in machine.clipboard.set_calls))

    def test_half_close_drains_motion_and_key_releases_before_cleanup(self):
        machine, link = self.owner()
        entered, release = threading.Event(), threading.Event()
        original = machine.injector.inject_mouse_move

        def blocked(dx, dy):
            if not entered.is_set():
                entered.set()
                release.wait(2)
            original(dx, dy)

        machine.injector.inject_mouse_move = blocked
        self.addCleanup(release.set)
        link.move(1, 0)
        self.assertTrue(entered.wait(1))
        link.move(2, 0)
        link.move(3, 0)
        link.key("keydown", "a")
        link.key("keyup", "a")
        link.sock.shutdown(socket.SHUT_WR)
        inbound = machine.responder._links[B]
        self.assertTrue(wait_for(lambda: inbound.inbox and
                                 inbound.inbox[-1][0].get("type") == "keydown"))
        release.set()
        self.assertTrue(wait_for(lambda: machine.links and machine.links[-1] == (B, False)))
        self.assertEqual(machine.injected(), [
            ("move", 1, 0), ("move", 5, 0), ("key", "a", True), ("key", "a", False),
        ])
        self.assertFalse(machine.responder.driven)

    def test_close_while_the_reader_waits_for_focus_ends_both_threads(self):
        machine, link = self.owner()
        entered, release, reader_done = threading.Event(), threading.Event(), threading.Event()
        focus = machine.responder._on_focus
        read_ahead = machine.responder._read_ahead

        def delayed(inbound, message):
            entered.set()
            release.wait(2)
            focus(inbound, message)

        def read(inbound):
            try:
                read_ahead(inbound)
            finally:
                reader_done.set()

        # A replacement connection lets this test observe its exact reader's lifetime.
        machine.responder._on_focus = delayed
        machine.responder._read_ahead = read
        self.addCleanup(release.set)
        replacement = Initiator(machine, B)
        self.addCleanup(replacement.close)
        replacement.handshake()
        self.assertTrue(wait_for(lambda: machine.responder._links.get(B) is not None and
                                 machine.responder._links[B].sock.getpeername() == replacement.sock.getsockname()))
        inbound = machine.responder._links[B]
        replacement.take()
        self.assertTrue(entered.wait(1))
        inbound.close()
        self.assertTrue(reader_done.wait(1))
        release.set()
        self.assertTrue(wait_for(lambda: not machine.responder.links()))
        self.assertFalse(machine.responder.driven)


class InboxTests(unittest.TestCase):
    def setUp(self):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        self.link = receiver._Link(left, None, B, None, "127.0.0.1", "Driver", [], None, [], True)
        self.addCleanup(self.link.close)

    def moves(self, values):
        for dx, dy, seq in values:
            self.assertTrue(self.link.receive(protocol.mousemove_msg(dx, dy, seq), 80))
        return [(read["dx"], read["dy"], read["seq"])
                for read in (protocol.read_input(item[0]) for item in self.link.inbox)]

    def test_relative_travel_is_preserved_with_the_latest_sequence(self):
        self.assertEqual(self.moves([(2, 0, 1), (3, 0, 2), (4, 0, 3)]), [(9, 0, 3)])

    def test_mixed_axes_and_diagonal_jitter_merge_without_losing_travel(self):
        self.assertEqual(self.moves([(2, 0, 1), (0, 3, 2), (4, 1, 3)]), [(6, 4, 3)])

    def test_reversals_retain_their_geometry_samples(self):
        self.assertEqual(self.moves([(120, 0, 1), (0, 120, 2), (0, -120, 3)]),
                         [(120, 0, 3)])
        self.assertEqual(self.link.inbox[0][0]["data"]["samples"], [[120, 0], [0, 120], [0, -120]])

    def test_delta_overflow_and_decreasing_sequences_stay_separate(self):
        self.assertEqual(self.moves([(65535, 0, 2), (1, 0, 3), (2, 0, 1)]),
                         [(65535, 0, 2), (1, 0, 3), (2, 0, 1)])

    def test_both_queue_limits_block_and_close_wakes_the_reader(self):
        for cap, value in (("INBOX_LIMIT", 1), ("INBOX_BYTES", 100)):
            with self.subTest(cap=cap), mock.patch.object(receiver, cap, value):
                self.link.closed.clear()
                self.link.inbox.clear()
                self.link.inbox_bytes = 0
                self.link.receive(protocol.mousemove_msg(protocol.MOVE_MAX, 0, 1), 80)
                result = []
                entered = threading.Event()

                def receive():
                    entered.set()
                    result.append(self.link.receive(protocol.mousemove_msg(1, 0, 2), 80))

                reader = threading.Thread(target=receive)
                reader.start()
                self.assertTrue(entered.wait(1))
                reader.join(.03)
                self.assertTrue(reader.is_alive())
                self.assertEqual(len(self.link.inbox), 1)
                self.link.close()
                reader.join(1)
                self.assertFalse(reader.is_alive())
                self.assertEqual(result, [False])


if __name__ == "__main__":
    unittest.main()
