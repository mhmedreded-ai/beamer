"""Core screen numbering and link contracts, separate from the native overlay tests."""

import unittest

from core import protocol, receiver, ways
from core.tests.responder_harness import B, CAPS, HERE, TOKENS, Machine, entry, wait_for
from core.tests.test_link import Side


class ScreenNumberTests(unittest.TestCase):
    def test_numbers_follow_map_order_even_with_split_display_offsets(self):
        from core.identify_screens import display_numbers
        tile = {"x": 100, "y": 200, "w": 300, "h": 200, "displays": {
            "0": {"x": 1920, "y": 0, "w": 1920, "h": 1080},
            "2": {"x": 0, "y": -1080, "w": 1920, "h": 1080},
            "1": {"x": 0, "y": 0, "w": 1920, "h": 1080}},
            "display_offsets": {"2": {"x": 500, "y": 500}}}
        self.assertEqual(display_numbers(tile), {"2": 1, "1": 2, "0": 3})
        self.assertEqual([display_numbers(tile)[frame["display"]]
                          for frame in ways._display_frames(tile)], [1, 2, 3])

    def test_single_display_fallback_needs_no_saved_geometry(self):
        from core.identify_screens import display_numbers
        self.assertEqual(display_numbers({}), {"0": 1})
        self.assertEqual(display_numbers(None), {"0": 1})


class IdentifyProtocolTests(unittest.TestCase):
    def test_message_round_trips_without_changing_wire_version(self):
        labels = {"1": 1, "0": 2}
        message = protocol.identify_screens_msg(labels)
        self.assertEqual(message, {"type": "identify_screens", "data": {"displays": labels}})
        self.assertEqual(protocol.read_identify_screens(message), labels)
        labels["0"] = 3
        self.assertEqual(protocol.read_identify_screens(message), {"1": 1, "0": 2})
        self.assertEqual(protocol.LINK_VERSION, 6)

    def test_rejects_invalid_labels_before_showing_any_overlay(self):
        for labels in ({}, [], {"0": True}, {"0": 0}, {"0": 17}, {"0": 1.5},
                       {"0": 1, "1": 1}, {"a" * 65: 1}, {"bad key": 1},
                       {"é": 1}, {str(n): n + 1 for n in range(17)}):
            with self.subTest(labels=labels):
                message = {"type": "identify_screens", "data": {"displays": labels}}
                self.assertIsNone(protocol.read_identify_screens(message))
        for message in (None, {}, {"type": "identify_screens", "data": []},
                        {"type": "settings", "data": {"displays": {"0": 1}}}):
            self.assertIsNone(protocol.read_identify_screens(message))


class IdentifyLinkTests(unittest.TestCase):
    def test_older_outbound_peer_is_a_successful_no_op(self):
        link = Side().link()
        link._live = True
        link._caps = frozenset(CAPS)
        self.assertTrue(link.post(protocol.identify_screens_msg({"0": 1})))
        self.assertEqual(list(link._outbox), [])
        self.assertEqual(link.last_sent_seq, 0)

    def test_existing_link_delivers_both_directions_without_taking_input_ownership(self):
        caps = tuple(CAPS) + ("identify_screens",)
        seen = []
        machine = Machine([entry(B, "Peer")], caps=caps,
                          identify_callback=lambda peer, labels: seen.append((peer, labels))).start()
        self.addCleanup(machine.stop)
        side = Side(machine)
        identity = side.identity
        side.identity = lambda: identity(caps=list(caps))
        link = side.link()
        self.addCleanup(link.stop)
        link.start()
        self.assertTrue(wait_for(lambda: bool(side.ups)))
        self.assertTrue(link.post(protocol.identify_screens_msg({"0": 2, "1": 1})))
        self.assertTrue(wait_for(lambda: bool(seen)))
        self.assertEqual(seen, [(B, {"0": 2, "1": 1})])
        self.assertIsNone(machine.responder.owner)
        self.assertEqual(machine.injected(), [])
        self.assertTrue(machine.responder.send(B, protocol.identify_screens_msg({"0": 1})))
        self.assertTrue(wait_for(lambda: bool(side.messages)))
        self.assertEqual(protocol.read_identify_screens(side.messages[0]), {"0": 1})
        self.assertEqual(link.last_sent_seq, 0)

    def test_older_inbound_peer_gets_no_new_message(self):
        machine = Machine([entry(B, "Peer")])
        link = receiver._Link(None, None, B, protocol.key_id(TOKENS[B]), "127.0.0.1",
                              "Older", CAPS, HERE, CAPS, True)
        machine.responder._links[B] = link
        self.assertTrue(machine.responder.send(B, protocol.identify_screens_msg({"0": 1})))
        self.assertEqual(list(link.outbox), [])

    def test_receiver_ignores_unnegotiated_and_unknown_requests(self):
        seen = []
        machine = Machine([entry(B, "Peer")], identify_callback=lambda *args: seen.append(args))
        link = receiver._Link(None, None, B, protocol.key_id(TOKENS[B]), "127.0.0.1",
                              "Older", CAPS, HERE, CAPS, True)
        machine.responder._dispatch(link, protocol.identify_screens_msg({"0": 1}))
        machine.responder._dispatch(link, {"type": "future_request", "data": {}})
        self.assertEqual(seen, [])
        self.assertEqual(machine.injected(), [])
        self.assertFalse(link.closed.is_set())
