"""Directional ACK latency and diagnostics, using real encrypted loopback links."""

import time
import unittest

from core import protocol
from core.tests.test_link import Side
from core.tests.responder_harness import B, HERE, TOKENS, Machine, entry, wait_for


class DirectionalLatencyTests(unittest.TestCase):
    def test_slow_receiver_delays_only_its_input_direction_and_logs_the_stage(self):
        slow = Machine([entry(B, "A")]).start()
        fast = Machine([entry(HERE, "B", token=TOKENS[B])], own=B).start()
        self.addCleanup(slow.stop)
        self.addCleanup(fast.stop)
        forward = Side(slow).link()
        backward = Side(fast, peer=B, own=HERE).link()
        self.addCleanup(forward.stop)
        self.addCleanup(backward.stop)
        forward.start()
        backward.start()
        self.assertTrue(wait_for(lambda: forward.live() and backward.live()))
        for link, target in ((forward, HERE), (backward, B)):
            self.assertTrue(link.post(protocol.focus_v6(1, target, resistance_px=120, reach=[])))
        self.assertTrue(wait_for(lambda: slow.responder.driven and fast.responder.driven))
        original = slow.injector.inject_key

        def delayed(key, down, us=None):
            time.sleep(0.02)
            original(key, down, us=us)

        slow.injector.inject_key = delayed
        inbound_route = slow.responder._links[B].timing.route
        with self.assertLogs("Beamer", "INFO") as captured:
            for _ in range(24):
                self.assertTrue(forward.send_input(protocol.key_msg("keydown", "a")))
            self.assertTrue(backward.send_input(protocol.key_msg("keydown", "a")))
            self.assertTrue(wait_for(lambda: forward._last_ack_seq == 24 and backward._last_ack_seq == 1))
            self.assertGreater(forward.round_trips()[-1], 0.35)
            self.assertGreater(forward.round_trips()[-1] - forward.round_trips()[0], 0.2)
            self.assertLess(backward.round_trips()[-1], 0.2)
            forward.stop()
            backward.stop()
            slow.stop()
            fast.stop()
            self.assertTrue(wait_for(lambda: any(inbound_route in line and "inject_ms" in line
                                                 for line in captured.output), 5.0))
        text = "\n".join(captured.output)
        self.assertIn("link timing inbound", text)
        self.assertIn("inject_ms", text)
        self.assertIn("dispatch_ms", text)

    def test_lock_wait_is_measured_separately_from_injection(self):
        machine = Machine([entry(B, "A")]).start()
        self.addCleanup(machine.stop)
        link = Side(machine).link()
        self.addCleanup(link.stop)
        link.start()
        self.assertTrue(wait_for(link.live))
        link.post(protocol.focus_v6(1, HERE, resistance_px=120, reach=[]))
        self.assertTrue(wait_for(lambda: machine.responder.driven))
        with machine.responder._lock:
            link.send_input(protocol.mousemove_msg(1, 1))
            self.assertTrue(wait_for(lambda: link._written_seq == 1))
            time.sleep(0.1)
        self.assertTrue(wait_for(lambda: link._last_ack_seq == 1))
        inbound = machine.responder._links[B]
        with inbound.timing.lock:
            lock_max = inbound.timing.stages["input_lock"][2]
            inject_max = inbound.timing.stages["inject"][2]
        self.assertGreater(lock_max, 50)
        self.assertLess(inject_max, lock_max)

    def test_route_logs_use_actual_socket_endpoints(self):
        machine = Machine([entry(B, "A")]).start()
        self.addCleanup(machine.stop)
        link = Side(machine).link()
        self.addCleanup(link.stop)
        with self.assertLogs("Beamer", "INFO") as captured:
            link.start()
            self.assertTrue(wait_for(link.live))
            self.assertTrue(wait_for(lambda: sum("link route" in line for line in captured.output) == 2))
            link.stop()
        text = "\n".join(captured.output)
        self.assertIn("link route outbound", text)
        self.assertIn("link route inbound", text)
        self.assertIn("127.0.0.1", text)
        self.assertIn("nodelay=1", text)


if __name__ == "__main__":
    unittest.main()
