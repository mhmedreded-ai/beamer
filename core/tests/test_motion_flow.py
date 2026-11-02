"""GH15: motion stays bounded without mistaking a busy receiver for a dead peer."""

import threading
import time
import unittest
import collections
import queue
from types import SimpleNamespace

from core import link as link_module, owner, protocol
from core.tests.app_methods import load_methods
from core.tests.test_link import Side


class MotionFlowTests(unittest.TestCase):
    def test_non_motion_queue_and_clipboard_barriers_do_not_consult_motion_capabilities(self):
        sender_type = load_methods("win_app/sender.py", "LinkSender", ["_queue", "_deliver"],
                                   {"owner_module": owner, "protocol": protocol, "queue": queue,
                                    "MODIFIER_KEYS": frozenset()})
        sender = sender_type()
        sender.links = SimpleNamespace()
        sender._outbound = queue.Queue()
        key = owner.Send("far", protocol.key_msg("keydown", "a"))
        scroll = owner.Send("far", protocol.scroll_msg(1, 0, "line"))
        sender._queue([key, scroll, owner.Send("far", protocol.mousemove_msg(1, 1))])
        self.assertEqual(sender._outbound.qsize(), 3)
        sender._clip_lock = threading.Lock()
        sender._clip_wait = {"far": collections.deque([key])}
        sender._deliver(scroll)
        sender._deliver(owner.Send("far", protocol.mousemove_msg(1, 1)))
        self.assertEqual(len(sender._clip_wait["far"]), 3)

    def test_sender_clipboard_wait_coalesces_motion_without_crossing_a_key(self):
        sender_type = load_methods("win_app/sender.py", "LinkSender", ["_deliver"],
                                   {"owner_module": owner, "protocol": protocol})
        sender = sender_type()
        peer = "far"
        sender._clip_lock = threading.Lock()
        sender.links = SimpleNamespace(caps=lambda _: {"motion_path"})
        sender._clip_wait = {peer: collections.deque()}
        for index in range(1000):
            sender._deliver(owner.Send(peer, protocol.mousemove_msg(1, 1 if index % 2 else -1)))
        sender._deliver(owner.Send(peer, protocol.key_msg("keydown", "a")))
        sender._deliver(owner.Send(peer, protocol.mousemove_msg(2, 3)))
        waiting = sender._clip_wait[peer]
        self.assertLessEqual(len(waiting), 18)
        self.assertEqual(sum(a.message["data"]["dx"] for a in list(waiting)[:-2]), 1000)
        self.assertEqual(sum(a.message["data"]["dy"] for a in list(waiting)[:-2]), 0)
        self.assertEqual(waiting[-2].message["type"], "keydown")

    def setUp(self):
        self.now = [10.0]
        self.link = Side().link(clock=lambda: self.now[0])
        self.link._live = True
        self.link._caps = frozenset({"motion_path"})
        self.link._checked_at = self.link._logged_at = 10.0
        self.link._last_tx = 10.0

    def test_unsent_motion_merges_mixed_axes_and_keeps_control_order(self):
        for dx, dy in ((1, 0), (0, 2), (3, 4)):
            self.assertTrue(self.link.send_input(protocol.mousemove_msg(dx, dy)))
        self.assertTrue(self.link.send_input(protocol.key_msg("keydown", "a")))
        self.assertTrue(self.link.send_input(protocol.mousemove_msg(5, 6)))
        messages = list(self.link._outbox)
        self.assertEqual([m["type"] for m in messages], ["mousemove", "keydown", "mousemove"])
        self.assertEqual(protocol.read_input(messages[0]), {"type": "mousemove", "dx": 4, "dy": 6, "seq": 3,
                                                          "samples": [[1, 0], [0, 2], [3, 4]]})
        self.assertEqual(set(self.link._queued_at), {3, 4, 5})
        self.assertEqual(self.link._queued_bytes, sum(link_module._weight(m) for m in messages))

    def test_progressing_ack_does_not_drop_a_slow_receiver(self):
        self.link._written_seq = 100
        self.link._sent_at.extend((seq, 10.0) for seq in range(1, 101))
        self.now[0] = 11.4
        self.link._ack({"seq": 1, "held_us": 0, "locked": False})
        self.now[0] = 11.6
        try:
            self.link._tick(self.now[0])
        except link_module.LinkEnded as exc:
            self.fail(f"An advancing ACK must keep a busy receiver connected: {exc}")
        self.assertTrue(self.link.live())
        self.now[0] = 11.4 + link_module.ACK_STALL_SECONDS + .01
        with self.assertRaises(link_module.LinkEnded):
            self.link._tick(self.now[0])

    def test_idle_or_duplicate_ack_cannot_keep_unprocessed_input_alive(self):
        self.link._written_seq = 1
        self.link._sent_at.append((1, 10.0))
        self.now[0] = 11.4
        self.link._ack({"seq": 0, "held_us": 0, "locked": False})
        self.now[0] = 11.6
        with self.assertRaises(link_module.LinkEnded):
            self.link._tick(self.now[0])

    def test_steady_travel_with_reversing_axis_jitter_cannot_fill_the_outbox(self):
        for index in range(10000):
            self.assertTrue(self.link.send_input(protocol.mousemove_msg(1, 1 if index % 2 else -1)))
        self.assertLessEqual(len(self.link._outbox), 157)
        self.assertEqual(sum(m["data"]["dx"] for m in self.link._outbox), 10000)
        self.assertEqual(sum(m["data"]["dy"] for m in self.link._outbox), 0)

    def test_writer_bounds_unacknowledged_input_and_resumes_on_ack(self):
        class Session:
            def seal(self, message):
                return b"x"

        class Socket:
            def send(self, frame):
                return len(frame)

            def shutdown(self, how):
                pass

            def close(self):
                pass

        sock = Socket()
        self.link._sock = sock
        for index in range(100):
            self.assertTrue(self.link.send_input(protocol.key_msg("keydown", "a")))
        worker = threading.Thread(target=self.link._write, args=(sock, Session()))
        worker.start()
        self.addCleanup(worker.join, 1)
        self.addCleanup(self.link._close)
        deadline = time.monotonic() + 1
        while self.link._written_seq < 32 and time.monotonic() < deadline:
            time.sleep(.001)
        time.sleep(.02)
        self.assertEqual(self.link._written_seq, 32)
        self.link._ack({"seq": 32, "held_us": 0, "locked": False})
        deadline = time.monotonic() + 1
        while self.link._written_seq < 64 and time.monotonic() < deadline:
            time.sleep(.001)
        self.assertEqual(self.link._written_seq, 64)


if __name__ == "__main__":
    unittest.main()
