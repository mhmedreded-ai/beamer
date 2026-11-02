"""Portable sockets coexist with another Beamer using synthetic peers on loopback."""

from collections import deque
import logging
import socket
from types import SimpleNamespace
import unittest
from unittest import mock

from core import pairing, portable_runtime as runtime, receiver
from core.tests.responder_harness import (
    B, C, HERE, CAPS, FakeDesktop, FakeInjector, Initiator, Settings, entry, wait_for,
)
from core.tests.test_pairing_v3 import Book


class PortableNetworkTests(unittest.TestCase):
    def setUp(self):
        runtime.close()
        self.addCleanup(runtime.close)

    def responder(self, name, own=HERE, port=0, listener=None):
        settings = Settings([entry(B, "Synthetic peer", host="127.0.0.1")], own=own)
        book = receiver.PeerBook(settings.load, settings.save)
        statuses = []
        responder = receiver.LinkResponder(
            book, lambda: {"id": own, "name": name, "platform": "windows",
                           "app": "1.5.10", "caps": list(CAPS)},
            status_callback=lambda state, detail: statuses.append((state, detail)),
            injector=FakeInjector(), desktop=FakeDesktop(), listener=listener,
        )
        self.addCleanup(responder.stop)
        responder.start(port)
        self.assertTrue(wait_for(lambda: responder.listening_port is not None), statuses)
        return SimpleNamespace(responder=responder, port=responder.listening_port,
                               settings=settings, book=book, statuses=statuses)

    def connect(self, machine):
        initiator = Initiator(machine, B)
        self.addCleanup(initiator.close)
        return initiator.handshake()

    def test_reserved_listener_serves_its_actual_port_beside_an_ordinary_responder(self):
        port = runtime.reserve_listener()
        reserved = runtime.listener
        portable = self.responder("Synthetic portable", port=0, listener=reserved)
        installed = self.responder("Synthetic installed", own=C)

        self.assertEqual(portable.port, port)
        self.assertIs(portable.responder._listener, reserved)
        self.assertNotEqual(installed.port, portable.port)
        portable_welcome = self.connect(portable)
        installed_welcome = self.connect(installed)
        self.assertEqual(portable_welcome["name"], "Synthetic portable")
        self.assertEqual(installed_welcome["name"], "Synthetic installed")
        self.assertEqual(portable_welcome["id"], HERE)
        self.assertEqual(installed_welcome["id"], C)
        self.assertTrue(wait_for(lambda: portable.responder.links() == {B}))
        self.assertTrue(wait_for(lambda: installed.responder.links() == {B}))

    def test_cached_port_survives_listener_handoff_close_and_can_be_bound_again(self):
        port = runtime.reserve_listener()
        original = runtime.listener
        first = self.responder("Synthetic first", port=port, listener=original)
        first.responder.stop()
        self.assertEqual(original.fileno(), -1)
        self.assertEqual(runtime.reserve_listener(), port)

        rebound = self.responder("Synthetic rebound", port=runtime.reserve_listener())
        self.assertEqual(rebound.port, port)
        self.assertEqual(self.connect(rebound)["name"], "Synthetic rebound")

    def pairing_service(self, name, identity, link_port, announce_to):
        book = Book()
        logger = logging.getLogger("portable-loopback-tests")
        logger.handlers = [logging.NullHandler()]
        logger.propagate = False
        service = pairing.PairingService(
            name, identity, "windows", lambda: link_port, book.peers, book.store,
            bind_port=0, tcp_port=0, announce_to=announce_to, logger=logger,
        )
        received = deque()
        dispatch = service._dispatch

        def record(sock, message, address, *rest):
            received.append((message.get("type"), address))
            return dispatch(sock, message, address, *rest)

        service._dispatch = record
        self.addCleanup(service.stop)
        service.start()
        self.assertTrue(wait_for(lambda: service.listening_port is not None), service.error)
        return service, book, received

    def test_active_discovery_finds_peer_from_its_reply_and_both_tcp_ports_are_unique(self):
        # Interface enumeration is outside this test; every transmitted datagram stays on loopback.
        for target, value in (("_local_ipv4_addresses", ["127.0.0.1"]),
                              ("pairing_address", "127.0.0.1"), ("_answer_source", "127.0.0.1")):
            patch = mock.patch.object(pairing, target, return_value=value)
            patch.start()
            self.addCleanup(patch.stop)
        broadcasts = mock.patch.object(pairing, "broadcast_targets",
                                       side_effect=AssertionError("LAN broadcast is forbidden"))
        broadcast_targets = broadcasts.start()
        self.addCleanup(broadcasts.stop)
        sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sink.bind(("127.0.0.1", 0))
        self.addCleanup(sink.close)
        destination = sink.getsockname()
        first, first_book, first_received = self.pairing_service(
            "Synthetic portable", HERE, 41001, destination)
        second, second_book, second_received = self.pairing_service(
            "Synthetic installed", C, 41002, destination)
        self.assertNotEqual(first.listening_port, second.listening_port)
        self.assertEqual(first.machines(), [])

        # The second service only announces to the sink, so its discovery here requires a find reply.
        first.announce_to = ("127.0.0.1", second.listening_port)
        first.active_discovery = True
        first._announce(first._sock)
        self.assertTrue(wait_for(lambda: any(kind == pairing.MSG_FIND for kind, _ in second_received)))
        self.assertTrue(wait_for(lambda: any(item["name"] == "Synthetic installed"
                                             for item in first.machines())))
        discovered = next(item for item in first.machines() if item["name"] == "Synthetic installed")
        self.assertEqual(discovered["address"], "127.0.0.1")
        self.assertEqual(discovered["port"], 41002)
        self.assertEqual(discovered["reply_port"], second.listening_port)
        self.assertEqual(len(first.machines()), 1)
        self.assertTrue(any(kind == pairing.MSG_BEACON for kind, _ in first_received))

        first.begin_pairing()
        second.begin_pairing()
        self.assertIsNone(first.tcp_error)
        self.assertIsNone(second.tcp_error)
        self.assertGreater(first.tcp_port, 0)
        self.assertGreater(second.tcp_port, 0)
        self.assertNotEqual(first.tcp_port, second.tcp_port)
        for service in (first, second):
            self.assertEqual(service._listener[0].getsockname()[1], service.tcp_port)
            with socket.create_connection(("127.0.0.1", service.tcp_port), timeout=2) as connection:
                self.assertEqual(connection.getpeername(), ("127.0.0.1", service.tcp_port))
        self.assertEqual(first_book.entries, [])
        self.assertEqual(second_book.entries, [])
        broadcast_targets.assert_not_called()


if __name__ == "__main__":
    unittest.main()
