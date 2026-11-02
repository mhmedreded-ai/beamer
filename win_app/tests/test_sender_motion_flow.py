"""GH15 sender batching: preserve travel and barriers before any socket work."""

import unittest
import collections
from unittest import mock

from links_rig import B, Rig
from core import owner, protocol
import sender


class SenderMotionFlowTests(unittest.TestCase):
    def rig(self):
        rig = Rig()
        rig.links.caps_of[B] = rig.links.caps(B) | {"motion_path"}
        return rig

    def test_motion_waiting_for_clipboard_is_bounded_and_controls_keep_their_place(self):
        rig = self.rig()
        rig.sender._clip_wait[B] = collections.deque()
        for index in range(1000):
            rig.sender._deliver(owner.Send(B, protocol.mousemove_msg(1, 1 if index % 2 else -1)))
        rig.sender._deliver(owner.Send(B, protocol.key_msg("keydown", "a")))
        rig.sender._deliver(owner.Send(B, protocol.mousemove_msg(2, 3)))
        waiting = rig.sender._clip_wait[B]
        self.assertLessEqual(len(waiting), 18)
        self.assertEqual(sum(a.message["data"]["dx"] for a in list(waiting)[:-2]), 1000)
        self.assertEqual(sum(a.message["data"]["dy"] for a in list(waiting)[:-2]), 0)
        self.assertEqual(waiting[-2].message["type"], "keydown")

    def run_worker(self, rig):
        """Runs the outbound worker over one move that queues a second inside its send and a third
        inside its sleep; returns what it sent and every sleep it took."""
        sent = []
        clock = [10.0]
        sleeps = []

        def deliver(action):
            sent.append(action.message)
            if len(sent) == 1:
                rig.sender._queue([owner.Send(B, protocol.mousemove_msg(2, 3))])
            else:
                rig.sender._stop_event.set()

        def sleep(delay):
            sleeps.append(delay)
            clock[0] += delay
            rig.sender._queue([owner.Send(B, protocol.mousemove_msg(4, 5))])

        rig.sender._deliver = deliver
        rig.sender._queue([owner.Send(B, protocol.mousemove_msg(1, 1))])
        with mock.patch.object(sender.time, "monotonic", side_effect=lambda: clock[0]), mock.patch.object(sender.time, "sleep", side_effect=sleep):
            rig.sender._outbound_worker()
        return sent, sleeps

    def test_backed_up_link_paces_motion_and_flushes_trailing_motion(self):
        rig = self.rig()
        rig.links.backlog = sender.PACE_BACKLOG
        sent, sleeps = self.run_worker(rig)
        self.assertEqual([(m["data"]["dx"], m["data"]["dy"]) for m in sent], [(1, 1), (6, 8)])
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], .002)
        self.assertEqual(rig.sender._outbound.unfinished_tasks, 0)

    def test_idle_link_sends_motion_at_once_without_pacing(self):
        rig = self.rig()
        rig.links.backlog = sender.PACE_BACKLOG - 1
        sent, sleeps = self.run_worker(rig)
        self.assertEqual(sleeps, [])
        self.assertEqual([(m["data"]["dx"], m["data"]["dy"]) for m in sent], [(1, 1), (2, 3)])
        self.assertEqual(rig.sender._outbound.unfinished_tasks, 0)

    def test_pacing_follows_the_backlog_of_the_peer_the_move_is_for(self):
        rig = self.rig()
        asked = []
        rig.links.pending = lambda peer: asked.append(peer) or 0
        self.run_worker(rig)
        self.assertEqual(set(asked), {B})

    def test_link_set_reports_a_peers_unsent_and_unacknowledged_input(self):
        class Linked:
            def pending(self):
                return 7

        links = sender.LinkSet(mock.Mock(_peers={}), is_local=lambda host: False)
        links._by_peer[B] = Linked()
        self.assertEqual(links.pending(B), 7)
        self.assertEqual(links.pending(b"\x01" * 16), 0)

    def test_pending_high_polling_samples_are_bounded_relative_batches(self):
        rig = self.rig()
        rig.sender.set_redirecting(True)
        rig.flush()
        for _ in range(1000):
            rig.sender.on_motion(1, 2)
        self.assertLessEqual(rig.sender._outbound.qsize(), 16)
        moves = rig.sent(B, "mousemove")
        self.assertEqual((sum(m["data"]["dx"] for m in moves), sum(m["data"]["dy"] for m in moves)), (1000, 2000))

    def test_buttons_are_batch_boundaries_and_reversing_jitter_is_summed(self):
        rig = self.rig()
        rig.sender.set_redirecting(True)
        rig.flush()
        rig.links.sent.clear()
        rig.sender.on_motion(1, 0)
        rig.sender.on_motion(0, 2)
        rig.sender.on_button("left", True)
        rig.sender.on_motion(3, 4)
        rig.sender.on_motion(-2, -1)
        messages = rig.sent(B)
        self.assertEqual([m["type"] for m in messages], ["mousemove", "mousedown", "mousemove"])
        self.assertEqual((messages[0]["data"]["dx"], messages[0]["data"]["dy"]), (1, 2))
        self.assertEqual((messages[2]["data"]["dx"], messages[2]["data"]["dy"]), (1, 3))

    def test_high_polling_sign_jitter_is_bounded_and_keeps_net_travel(self):
        rig = self.rig()
        rig.sender.set_redirecting(True)
        rig.flush()
        for index in range(1000):
            rig.sender.on_motion(1, 1 if index % 2 else -1)
        self.assertLessEqual(rig.sender._outbound.qsize(), 16)
        moves = rig.sent(B, "mousemove")
        self.assertEqual((sum(m["data"]["dx"] for m in moves), sum(m["data"]["dy"] for m in moves)), (1000, 0))


if __name__ == "__main__":
    unittest.main()
