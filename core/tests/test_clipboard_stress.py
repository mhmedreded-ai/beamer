"""Encrypted clipboard bursts over loopback; all desktop edges are synthetic."""

import unittest

from core import protocol
from core.tests.responder_harness import B, Initiator, Machine, entry, wait_for


class ClipboardWireStress(unittest.TestCase):
    def setUp(self):
        self.machine = Machine([entry(B, "Peer")]).start()
        self.addCleanup(self.machine.stop)

    def connect(self):
        link = Initiator(self.machine)
        self.addCleanup(link.close)
        link.handshake()
        route = link.take()
        self.assertEqual(link.answer(route)[0], protocol.MSG_ACCEPT)
        self.assertTrue(wait_for(lambda: self.machine.responder.owner == B))
        return link

    def test_rapid_successive_unicode_copies_keep_order_and_reach_the_newest_copy(self):
        link = self.connect()
        copies = [f"copy {n}: café, 日本語, Україна, \U0001d11e\nsecond line" for n in range(300)]
        for text in copies:
            link.send(protocol.clipboard_msg(text))
        self.assertTrue(wait_for(lambda: self.machine.clipboard.text == copies[-1], timeout=5))
        indices = [copies.index(text) for text, _ in self.machine.clipboard.set_calls]
        self.assertEqual(indices, sorted(set(indices)))

    def test_large_unicode_boundary_and_oversize_do_not_drop_the_next_copy(self):
        link = self.connect()
        largest = "\U0001d11e" * (protocol.CLIPBOARD_MAX_BYTES // 4)
        link.send(protocol.clipboard_msg(largest))
        self.assertTrue(wait_for(lambda: self.machine.clipboard.text == largest))
        for text in (largest + "x", "after oversize"):
            link.send(protocol.clipboard_msg(text))
        self.assertTrue(wait_for(lambda: len(self.machine.clipboard.set_calls) == 2))
        self.assertEqual(self.machine.clipboard.set_calls, [(largest, None), ("after oversize", None)])

    def test_resume_with_a_new_link_accepts_a_fresh_clipboard(self):
        before = self.connect()
        before.send(protocol.clipboard_msg("before sleep"))
        self.assertTrue(wait_for(lambda: self.machine.clipboard.text == "before sleep"))
        before.close()
        self.assertTrue(wait_for(lambda: self.machine.responder.owner is None))
        after = self.connect()
        after.send(protocol.clipboard_msg("after wake"))
        self.assertTrue(wait_for(lambda: self.machine.clipboard.text == "after wake"))


if __name__ == "__main__":
    unittest.main()
