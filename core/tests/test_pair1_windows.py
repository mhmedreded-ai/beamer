"""PAIR1: two synthetic Windows peers, using loopback and in-memory peer books only."""

import socket
import unittest
from unittest import mock

from core import pairing, peerlist, protocol
from . import responder_harness as responder
from .test_pairing_service import Machine, PC_ID, MAC_ID, wait_until
from .test_pairing_v3 import other_code


class WindowsPairingTests(unittest.TestCase):
    def setUp(self):
        self.target = Machine(self, "PAIR1 Target", PC_ID, "windows")
        self.source = Machine(self, "PAIR1 Source", MAC_ID, "windows", peer_udp=self.target.udp_port)
        self.target.pair_with(self.source)

    def showing_code(self):
        code = self.target.service.begin_pairing()
        self.assertTrue(wait_until(lambda: any(m.get("pair_id") for m in self.source.service.machines())))
        return code, next(m for m in self.source.service.machines() if m.get("pair_id"))

    def test_two_windows_peers_pair_over_udp_with_receiving_enabled(self):
        code, seen = self.showing_code()
        saved = self.source.service.pair(seen, code)
        self.assertTrue(wait_until(lambda: self.target.paired))
        self.assertEqual(saved["token"], self.target.paired[0]["token"])
        for entry in (saved, self.target.paired[0]):
            self.assertEqual(entry["platform"], "windows")
            self.assertTrue(entry["send"])
            self.assertTrue(entry["allow_drive"])

    def test_two_windows_peers_pair_by_address(self):
        code, _ = self.showing_code()
        saved = self.source.service.pair_by_address("127.0.0.1", code, self.target.tcp_port)
        self.assertTrue(wait_until(lambda: self.target.paired))
        self.assertEqual(saved["token"], self.target.paired[0]["token"])

    def test_closed_code_refuses_a_stale_discovery_entry_without_an_accept_prompt(self):
        code, seen = self.showing_code()
        self.target.service.cancel_pairing()
        with self.assertRaises(pairing.PairingError) as caught:
            self.source.service.pair(seen, code)
        self.assertEqual(str(caught.exception), pairing.ERROR_NOT_PAIRING)
        self.assertIn("not showing a code", peerlist.pairing_error_text(caught.exception, "PAIR1 Target"))
        self.assertEqual(self.target.book.entries, [])

    def test_expired_code_refuses_a_stale_discovery_entry(self):
        code, seen = self.showing_code()
        self.target.service.host.clock = lambda: self.target.service.host.expires_at + 1
        with self.assertRaises(pairing.PairingError) as caught:
            self.source.service.pair(seen, code)
        self.assertEqual(str(caught.exception), pairing.ERROR_NOT_PAIRING)
        self.assertEqual(self.target.service.outcome, "expired")

    def test_wrong_code_is_refused_and_cancelled(self):
        code, seen = self.showing_code()
        with self.assertRaises(pairing.PairingError) as caught:
            self.source.service.pair(seen, other_code(code))
        self.assertEqual(str(caught.exception), pairing.ERROR_REFUSED)
        self.assertIn("code was not accepted", peerlist.pairing_error_text(caught.exception, "PAIR1 Target"))
        self.assertTrue(wait_until(lambda: self.target.service.code is None))
        self.assertEqual(self.target.book.entries, [])

    def test_filtered_pairing_packets_time_out_instead_of_reporting_permission_denied(self):
        code, seen = self.showing_code()
        dispatch = self.target.service._dispatch

        def filtered(sock, message, *args):
            if message.get("type") not in (pairing.MSG_PAIR_START, pairing.MSG_PAIR_CONFIRM):
                dispatch(sock, message, *args)

        with mock.patch.object(self.target.service, "_dispatch", filtered), \
                mock.patch.object(pairing, "PAIR_REPLY_TIMEOUT_SECONDS", 0.1), \
                mock.patch.object(pairing, "PAIR_ATTEMPTS", 2):
            with self.assertRaises(pairing.PairingError) as caught:
                self.source.service.pair(seen, code)
        self.assertEqual(str(caught.exception), "no_answer")
        self.assertIn("did not answer", peerlist.pairing_error_text(caught.exception, "PAIR1 Target"))
        self.assertIsNotNone(self.target.service.code)
        self.assertEqual(self.target.book.entries, [])

    def test_tcp_transport_failure_is_not_a_receiver_permission_refusal(self):
        with mock.patch.object(socket, "create_connection", side_effect=TimeoutError("synthetic filter")):
            with self.assertRaises(pairing.PairingError) as caught:
                self.source.service.pair_by_address("127.0.0.1", "123456", self.target.tcp_port)
        self.assertEqual(str(caught.exception), "no_answer")


class WindowsReceivingPermissionTests(unittest.TestCase):
    def test_receiving_off_refuses_input_after_a_successful_authenticated_link(self):
        machine = responder.Machine([responder.entry(responder.B, "PAIR1 Source", platform="windows", allow_drive=False)]).start()
        self.addCleanup(machine.stop)
        source = responder.Initiator(machine)
        self.addCleanup(source.close)
        welcome = source.handshake(source.hello(platform="windows"))
        self.assertFalse(welcome["accepts"])
        route = source.take()
        self.assertEqual(source.answer(route), (protocol.MSG_REFUSE, {"route": route, "why": "not_allowed"}))
        machine.settings.peer(protocol.id_text(responder.B))["allow_drive"] = True
        machine.responder.peers_changed()
        self.assertEqual(source.expect(protocol.MSG_ACCEPTS), {"accepts": True})
        route = source.take()
        self.assertEqual(source.answer(route), (protocol.MSG_ACCEPT, {"route": route}))


if __name__ == "__main__":
    unittest.main()
