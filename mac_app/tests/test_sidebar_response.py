"""The persistent sidebar reports only a fresh response time for its connected peer."""

import sys
import unittest
from pathlib import Path
from threading import RLock
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import AppKit
import bridge
import kvm_bridge_app
import widgets
from core import protocol


class SidebarResponseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        AppKit.NSApplication.sharedApplication()

    def test_response_time_has_pending_fresh_and_disconnected_states(self):
        sidebar = widgets.Sidebar([("home", "Home", "")], lambda _key: None)
        self.assertTrue(sidebar.response_time.view.isHidden())

        sidebar.set_link(type("State", (), {"led": "signal", "blink": False, "word": "Connected", "tone": "ink"})(),
                         "Windows PC", response_time_ms=None, connected=True)
        self.assertEqual(sidebar.response_time.text, "Response time: —")
        self.assertEqual(sidebar.header.accessibilityLabel(), "Connected, Windows PC, Response time: no fresh measurement")

        sidebar.set_link(type("State", (), {"led": "signal", "blink": False, "word": "Connected", "tone": "ink"})(),
                         "Windows PC", response_time_ms=24, connected=True)
        self.assertEqual(sidebar.response_time.text, "Response time: 24 ms")
        self.assertEqual(sidebar.header.accessibilityLabel(), "Connected, Windows PC, Response time: 24 ms")

        sidebar.set_link(type("State", (), {"led": "ink_3", "blink": False, "word": "Disconnected", "tone": "ink_3"})(),
                         "Windows PC", response_time_ms=24, connected=False)
        self.assertEqual(sidebar.response_time.text, "")
        self.assertTrue(sidebar.response_time.view.isHidden())

    def test_mac_identity_advertises_ping_response_measurements(self):
        self.assertIn("ping_rtt", bridge.CAPABILITIES)

    def test_compact_sidebar_keeps_the_response_value_and_unit_visible(self):
        sidebar = widgets.Sidebar([("home", "Home", "")], lambda _key: None)
        state = SimpleNamespace(led="signal", blink=False, word="Connected", tone="ink")
        sidebar.set_link(state, "Windows PC", response_time_ms=3000, connected=True)
        sidebar.set_collapsed(True)
        sidebar.view.widthAnchor().constraintEqualToConstant_(56).setActive_(True)
        sidebar.view.setFrameSize_(AppKit.NSMakeSize(56, 500))
        sidebar.view.layoutSubtreeIfNeeded()
        view = sidebar.response_rail.view
        self.assertFalse(view.isHiddenOrHasHiddenAncestor())
        self.assertEqual(sidebar.response_rail.text, "3000 ms")
        self.assertGreaterEqual(view.frame().size.width, view.cell().cellSize().width)
        bounds = view.convertRect_toView_(view.bounds(), sidebar.view)
        text_width = view.attributedStringValue().size().width
        text_left = bounds.origin.x + (bounds.size.width - text_width) / 2
        self.assertGreaterEqual(text_left, 0)
        self.assertLessEqual(text_left + text_width, 56)
        sidebar.set_link(state, "Windows PC", connected=False)
        self.assertTrue(view.isHidden())

    def test_full_response_value_wraps_inside_supported_sidebar_widths(self):
        sidebar = widgets.Sidebar([("home", "Home", "")], lambda _key: None)
        sidebar.set_collapsed(False)
        sidebar.set_link(type("State", (), {"led": "signal", "blink": False, "word": "Connected", "tone": "ink"})(),
                         "Windows PC", response_time_ms=3000, connected=True)
        for width in (150, 180):
            sidebar.view.setFrameSize_(AppKit.NSMakeSize(width, 500))
            sidebar.view.layoutSubtreeIfNeeded()
            self.assertEqual(sidebar.response_time.text, "Response time: 3000 ms")
            self.assertTrue(sidebar.response_time.wrap)
            response_view = sidebar.response_time.view
            bounds = response_view.frame().size
            self.assertGreater(bounds.width, 0)
            needed = response_view.cell().cellSizeForBounds_(AppKit.NSMakeRect(0, 0, bounds.width, 500)).height
            self.assertGreaterEqual(bounds.height, needed)

    def test_active_peer_wins_and_outgoing_sample_is_preferred(self):
        outgoing_peer, incoming_peer = protocol.id_text(b"o" * 16), b"i" * 16
        outgoing_link = SimpleNamespace(live=lambda: True, response_time_ms=18)
        controller = SimpleNamespace(
            owner=SimpleNamespace(on=outgoing_peer), _owner_lock=RLock(), _peers_up={outgoing_peer: outgoing_link},
            _link_of=lambda peer: outgoing_link if peer == outgoing_peer else None,
            _status_link=lambda: None,
            _label=lambda wanted: "Outgoing" if wanted({"id": outgoing_peer}) else "Incoming",
        )
        server = SimpleNamespace(owner=incoming_peer, links=lambda: {incoming_peer},
                                 response_time_ms=lambda _peer: 31)
        window = SimpleNamespace(controller=controller, windows_input=SimpleNamespace(server=server))

        self.assertEqual(kvm_bridge_app.ControlWindow._header_response_time(window), ("Outgoing", 18, True))

    def test_receive_only_peer_uses_its_incoming_measurement(self):
        incoming_peer = b"i" * 16
        controller = SimpleNamespace(
            owner=SimpleNamespace(on=None), _owner_lock=RLock(), _peers_up={}, _link_of=lambda _peer: None,
            _status_link=lambda: None, _label=lambda _wanted: "Incoming",
        )
        server = SimpleNamespace(owner=incoming_peer, links=lambda: {incoming_peer},
                                 response_time_ms=lambda peer: 42 if peer == incoming_peer else None)
        window = SimpleNamespace(controller=controller, windows_input=SimpleNamespace(server=server))

        self.assertEqual(kvm_bridge_app.ControlWindow._header_response_time(window), ("Incoming", 42, True))

    def test_incoming_measurement_fills_a_stale_outgoing_sample_for_the_same_peer(self):
        ident = b"p" * 16
        peer = protocol.id_text(ident)
        outgoing = SimpleNamespace(live=lambda: True, response_time_ms=None)
        controller = SimpleNamespace(
            owner=SimpleNamespace(on=peer), _owner_lock=RLock(), _peers_up={peer: outgoing},
            _link_of=lambda candidate: outgoing if candidate == peer else None,
            _status_link=lambda: outgoing,
            _label=lambda wanted: "Windows PC" if wanted({"id": peer}) else None,
        )
        server = SimpleNamespace(owner=ident, links=lambda: {ident},
                                 response_time_ms=lambda candidate: 37 if candidate == ident else None)
        window = SimpleNamespace(controller=controller, windows_input=SimpleNamespace(server=server))

        self.assertEqual(kvm_bridge_app.ControlWindow._header_response_time(window), ("Windows PC", 37, True))

    def test_idle_text_outgoing_link_matches_bytes_incoming_measurement(self):
        ident = b"p" * 16
        peer = protocol.id_text(ident)
        outgoing = SimpleNamespace(peer_id=peer, live=lambda: True, response_time_ms=None)
        controller = SimpleNamespace(
            owner=SimpleNamespace(on=None), _owner_lock=RLock(), _peers_up={peer: outgoing},
            _link_of=lambda candidate: outgoing if candidate == peer else None,
            _status_link=lambda: outgoing,
            _label=lambda wanted: "Windows PC" if wanted({"id": peer}) else None,
        )
        server = SimpleNamespace(owner=None, links=lambda: {ident},
                                 response_time_ms=lambda candidate: 37 if candidate == ident else None)
        window = SimpleNamespace(controller=controller, windows_input=SimpleNamespace(server=server))

        self.assertEqual(kvm_bridge_app.ControlWindow._header_response_time(window), ("Windows PC", 37, True))

    def test_idle_status_link_uses_its_string_peer_id(self):
        peer = protocol.id_text(b"o" * 16)
        link = SimpleNamespace(peer_id=peer, live=lambda: True, response_time_ms=27)
        controller = SimpleNamespace(
            owner=SimpleNamespace(on=None), _owner_lock=RLock(), _peers_up={peer: link},
            _link_of=lambda candidate: link if candidate == peer else None,
            _status_link=lambda: link,
            _label=lambda wanted: "Windows PC" if wanted({"id": peer}) else None,
        )
        window = SimpleNamespace(controller=controller, windows_input=None)

        self.assertEqual(kvm_bridge_app.ControlWindow._header_response_time(window), ("Windows PC", 27, True))

    def test_stale_status_link_falls_back_to_a_live_peer(self):
        stale_peer, live_peer = protocol.id_text(b"s" * 16), protocol.id_text(b"l" * 16)
        stale = SimpleNamespace(peer_id=stale_peer, live=lambda: False)
        live = SimpleNamespace(peer_id=live_peer, live=lambda: True, response_time_ms=34)
        controller = SimpleNamespace(
            owner=SimpleNamespace(on=None), _owner_lock=RLock(), _peers_up={live_peer: live},
            _link_of=lambda peer: live if peer == live_peer else None,
            _status_link=lambda: stale,
            _label=lambda wanted: "Live PC" if wanted({"id": live_peer}) else None,
        )
        window = SimpleNamespace(controller=controller, windows_input=None)

        self.assertEqual(kvm_bridge_app.ControlWindow._header_response_time(window), ("Live PC", 34, True))

    def test_stale_status_link_without_other_live_peer_is_disconnected(self):
        stale_peer = protocol.id_text(b"s" * 16)
        stale = SimpleNamespace(peer_id=stale_peer, live=lambda: False)
        controller = SimpleNamespace(
            owner=SimpleNamespace(on=None), _owner_lock=RLock(), _peers_up={},
            _link_of=lambda _peer: None, _status_link=lambda: stale,
            _label=lambda _wanted: None,
        )
        window = SimpleNamespace(controller=controller, windows_input=None)

        self.assertEqual(kvm_bridge_app.ControlWindow._header_response_time(window), ("—", None, False))


if __name__ == "__main__":
    unittest.main()
