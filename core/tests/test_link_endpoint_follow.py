"""Discovery endpoints are hints until a real loopback peer authenticates."""

import socket
import threading
import unittest

from core import pairing, peerlist
from core.tests.responder_harness import B, C, HERE, TOKENS, Machine, entry, wait_for
from core.tests.test_link import FakeClock, Side


class EndpointFollowTests(unittest.TestCase):
    def test_repaired_pin_reconnects_after_outage_without_following_wifi(self):
        machine = self.machine()
        side, clock = Side(machine), FakeClock()
        side.peer().update(host_pinned=True)
        old = dict(side.peer())
        repaired = pairing.peer_entry(HERE, "Far", "windows", machine.port, old["token"],
                                      "127.0.0.2", 100)
        peerlist.add_peer(side.settings.data, repaired, old)
        outage = threading.Event()
        attempted = []

        def factory(endpoint, timeout=None):
            attempted.append(endpoint)
            if outage.is_set() or endpoint[0] != "127.0.0.1":
                raise OSError("synthetic cable outage")
            return socket.create_connection(endpoint, timeout=timeout)

        link = self.link(side, clock=clock, socket_factory=factory)
        link.start()
        self.assertTrue(wait_for(link.live), side.states)
        outage.set()
        link._close()
        self.assertTrue(wait_for(lambda: not link.live()))
        clock.now += 11
        link.follow([{"name": "Here", "address": "127.0.0.2", "port": machine.port}])
        self.assertIsNone(link._try_host)
        outage.clear()
        self.assertTrue(wait_for(link.live), side.states)
        self.assertEqual(link.dialled, ("127.0.0.1", machine.port))
        self.assertEqual(side.peer()["host"], "127.0.0.1")
        self.assertTrue(all(endpoint[0] == "127.0.0.1" for endpoint in attempted), attempted)

    def machine(self, port=0, token=TOKENS[B], own=None):
        fields = {} if own is None else {"own": own}
        machine = Machine([entry(B, "Synthetic peer", token=token, send=False)], **fields)
        self.addCleanup(machine.stop)
        machine.responder.start(port)
        self.assertTrue(wait_for(lambda: machine.responder.listening_port is not None))
        machine.port = machine.responder.listening_port
        return machine

    def link(self, side, **kwargs):
        link = side.link(**kwargs)
        self.addCleanup(link.stop)
        return link

    def test_receive_only_peer_restarts_at_new_port_while_old_port_is_occupied(self):
        first = self.machine()
        side, clock = Side(first), FakeClock()
        side.peer()["host_pinned"] = False
        link = self.link(side, clock=clock)
        link.start()
        self.assertTrue(wait_for(link.live))
        old_port = first.port
        first.stop()
        self.assertTrue(wait_for(lambda: not link.live()))

        collision = self.machine(port=old_port, token=TOKENS[C], own=C)
        restarted = self.machine()
        self.assertEqual(collision.port, old_port)
        self.assertNotEqual(restarted.port, old_port)
        self.assertEqual(side.peer()["port"], old_port)
        clock.now += 11
        link.follow([{"name": "Here", "address": "127.0.0.1", "port": restarted.port}])
        self.assertTrue(wait_for(lambda: link.live() and link.dialled == ("127.0.0.1", restarted.port)),
                        side.states)
        self.assertEqual(side.peer()["host"], "127.0.0.1")
        self.assertEqual(side.peer()["port"], restarted.port)
        self.assertTrue(wait_for(lambda: restarted.responder.links() == {B}))

    def test_unauthenticated_alternate_port_never_changes_saved_endpoint(self):
        wrong = self.machine(token=TOKENS[C], own=C)
        reserved = socket.socket()
        reserved.bind(("127.0.0.1", 0))
        self.addCleanup(reserved.close)
        side, clock = Side(None), FakeClock()
        side.peer().update(host="127.0.0.1", port=reserved.getsockname()[1], host_pinned=False)
        saved = dict(side.peer())
        attempted = []
        rejected = threading.Event()

        class ObservedConnection:
            def __init__(self, connection):
                self.connection = connection

            def __getattr__(self, name):
                return getattr(self.connection, name)

            def close(self):
                self.connection.close()
                rejected.set()

        def factory(endpoint, timeout=None):
            attempted.append(endpoint)
            connection = socket.create_connection(endpoint, timeout=timeout)
            return ObservedConnection(connection) if endpoint[1] == wrong.port else connection

        link = self.link(side, clock=clock, socket_factory=factory)
        link.start()
        self.assertTrue(wait_for(lambda: attempted))
        clock.now += 11
        link.follow([{"name": "Far", "address": "127.0.0.1", "port": wrong.port}])
        self.assertTrue(wait_for(lambda: ("127.0.0.1", wrong.port) in attempted))
        self.assertTrue(rejected.wait(3), "the unauthenticated alternate never finished its handshake")
        self.assertFalse(link.live())
        self.assertEqual(side.peer(), saved)
        self.assertEqual(side.settings.saves, 0)

    def test_same_address_endpoints_rotate_after_downtime_and_cooldown(self):
        side, clock, attempted = Side(None), FakeClock(), []
        side.peer()["host_pinned"] = False

        def factory(endpoint, timeout=None):
            attempted.append(endpoint)
            raise OSError("synthetic unavailable endpoint")

        link = self.link(side, clock=clock, socket_factory=factory, reconnect_seconds=0.01)
        link.start()
        self.assertTrue(wait_for(lambda: attempted))
        beacons = [{"name": "Far", "address": "127.0.0.1", "port": port}
                   for port in (41001, 41002)]
        link.follow(beacons)
        self.assertIsNone(link._try_host)
        self.assertNotIn(("127.0.0.1", 41001), attempted)
        clock.now += 11
        link.follow(beacons)
        self.assertTrue(wait_for(lambda: ("127.0.0.1", 41001) in attempted))
        clock.now += 5
        link.follow(beacons)
        self.assertNotIn(("127.0.0.1", 41002), attempted)
        clock.now += 30
        link.follow(beacons)
        self.assertTrue(wait_for(lambda: ("127.0.0.1", 41002) in attempted))
        attempted.clear()
        clock.now += 31
        link.follow(beacons)
        self.assertTrue(wait_for(lambda: ("127.0.0.1", 41001) in attempted))
        self.assertEqual(side.peer()["port"], 24820)

    def test_pinned_choices_and_invalid_beacon_ports_are_never_followed(self):
        side, clock = Side(None), FakeClock()
        link = self.link(side, clock=clock)
        clock.now += 11
        for pinned in (None, True):
            side.peer()["host_pinned"] = pinned
            link.follow([{"name": "Far", "address": "127.0.0.1", "port": 41001}])
            self.assertIsNone(link._try_host)
        side.peer()["host_pinned"] = False
        for port in (True, False, 0, -1, 65536, "41001", None, 41001.0):
            with self.subTest(port=port):
                link.follow([{"name": "Far", "address": "127.0.0.1", "port": port}])
                self.assertIsNone(link._try_host)
                self.assertIsNone(link._followed_at)

    def test_missing_beacon_port_uses_saved_port_after_authentication(self):
        machine = self.machine()
        side, clock = Side(machine), FakeClock()
        side.peer().update(host="127.0.0.2", host_pinned=False)
        attempted = []

        def factory(endpoint, timeout=None):
            attempted.append(endpoint)
            if endpoint[0] == "127.0.0.2":
                raise OSError("synthetic old address unavailable")
            return socket.create_connection(endpoint, timeout=timeout)

        link = self.link(side, clock=clock, socket_factory=factory)
        link.start()
        self.assertTrue(wait_for(lambda: attempted))
        clock.now += 11
        link.follow([{"name": "Far", "address": "127.0.0.1"}])
        self.assertTrue(wait_for(link.live), side.states)
        self.assertEqual(side.peer()["host"], "127.0.0.1")
        self.assertEqual(side.peer()["port"], machine.port)


if __name__ == "__main__":
    unittest.main()
