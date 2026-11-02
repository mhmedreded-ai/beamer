"""Real loopback input completion and explicit harness timeout outcomes."""

import json
import threading
import unittest
from unittest import mock

from core import protocol, receiver
from core.tests.test_crossing_e2e import Pair


class RealResponderInputFence(Pair):
    def test_shortcut_does_not_return_a_desk_after_ownership_wait_timeout(self):
        with mock.patch.object(self.mac.hand, "go"), mock.patch.object(self.mac.hand, "until", return_value=False):
            with self.assertRaisesRegex(AssertionError, "ownership.*complete"):
                self.net.shortcut(self.mac)

    def test_shortcut_does_not_return_a_desk_before_settings_settle(self):
        with mock.patch.object(self.mac.hand, "go"), mock.patch.object(self.mac.hand, "until", return_value=True):
            with mock.patch.object(self.net, "settle", return_value=False):
                with self.assertRaisesRegex(AssertionError, "settle"):
                    self.net.shortcut(self.mac)

    def test_visiting_push_waits_for_real_receiver_input_before_returning(self):
        self.assertTrue(self.place(self.mac, self.pc, "right"))
        self.settled()
        self.assertIs(self.net.shortcut(self.pc, self.mac), self.mac)
        self.assertTrue(self.pc.hand.until(
            lambda: self.mac.machine.responder.owner == self.pc.own, timeout=0.5))

        entered, release, processed, finished = (threading.Event() for _ in range(4))
        answers, errors, gate_timeouts = [], [], []
        actual = receiver.LinkResponder._on_input
        target = self.mac.machine.responder

        def gated(server, link, message):
            if server is target and message.get("type") == protocol.MSG_MOUSEMOVE:
                entered.set()
                if not release.wait(2.0):
                    gate_timeouts.append(True)
                result = actual(server, link, message)
                processed.set()
                return result
            return actual(server, link, message)

        def push():
            try:
                answers.append(self.net.push(self.pc, "right", 1 / 6))
            except BaseException as error:
                errors.append(error)
            finally:
                finished.set()

        with mock.patch.object(receiver.LinkResponder, "_on_input", gated):
            worker = threading.Thread(target=push, name="real-input-fence-push", daemon=True)
            worker.start()
            try:
                self.assertTrue(entered.wait(1.0), "the real receiver never entered the gated input")
                returned_before_release = finished.wait(0.65)
                print("FENCE_BEFORE_RELEASE " + json.dumps({
                    "returned": returned_before_release, "processed": processed.is_set(),
                    "errors": [{"type": type(error).__name__, "message": str(error)} for error in errors],
                    "answers": len(answers)}), flush=True)
                self.assertFalse(returned_before_release,
                                 "Net._push exhausted its explicit deadline before the gated real input was released")
                self.assertFalse(processed.is_set(), "the closed receiver gate did not hold")
            finally:
                release.set()
                worker.join(2.0)
        self.assertFalse(worker.is_alive(), "the push worker did not finish after receiver release")
        self.assertFalse(gate_timeouts, "the fixture gate reached its emergency release bound")
        self.assertTrue(processed.is_set(), "the real receiver did not process the released input")
        self.assertEqual(errors, [], "the push raised instead of completing its real input")
        self.assertEqual(len(answers), 1)
        self.assertIs(answers[0], self.pc)
        self.assertIs(self.net.at(self.pc), self.pc)


if __name__ == "__main__":
    unittest.main(defaultTest="RealResponderInputFence", verbosity=2)
