"""Comprehensive integration and unit tests for BeamerAndroidBridge and pure-Python nacl bindings."""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ANDROID_PY = Path(__file__).resolve().parent.parent.parent / "android" / "app" / "src" / "main" / "python"
if str(ANDROID_PY) not in sys.path:
    sys.path.insert(0, str(ANDROID_PY))

import beamer_android_bridge as bridge_mod
from core import protocol


class AndroidBridgeIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        bridge_mod._BRIDGE_INSTANCE = None
        bridge_mod.init_bridge(self.temp_dir.name, "Galaxy Tab S9 Ultra", enable_network=False)
        self.bridge = bridge_mod.get_bridge()
        self.addCleanup(self.bridge.stop)

    def test_initial_state_and_cpace_pairing_qr(self) -> None:
        state = json.loads(self.bridge.get_state_json())
        self.assertEqual(state["wire_version"], protocol.LINK_VERSION)
        self.assertEqual(state["platform"], "android")
        self.assertEqual(state["name"], "Galaxy Tab S9 Ultra")
        self.assertIsNone(state["owner_on"])
        self.assertFalse(state["owner_away"])
        self.assertGreaterEqual(len(state["peers"]), 2)

        paired_state = json.loads(self.bridge.generate_pairing_code())
        self.assertEqual(len(paired_state["pairing_code"]), 6)
        self.assertTrue(paired_state["pairing_code"].isdigit())
        self.assertIn("beamer://pair?", paired_state["qr_payload"])

    def test_edge_pressure_accumulation_triggers_automatic_crossing(self) -> None:
        state = json.loads(self.bridge.get_state_json())
        right_peer = next(p for p in state["peers"] if p["side"] == "right")

        # Resistance is 120 dp; 3 pushes of 45 dp at the right edge overcome 120 dp resistance
        self.bridge.record_mousemove(45, 0, at_edge="right", offset=0.42)
        mid_state = json.loads(self.bridge.get_state_json())
        self.assertIsNone(mid_state["owner_on"])
        self.assertGreater(mid_state["edge_pressure"], 0.3)

        self.bridge.record_mousemove(45, 0, at_edge="right", offset=0.42)
        self.bridge.record_mousemove(45, 0, at_edge="right", offset=0.42)
        crossed = json.loads(self.bridge.get_state_json())
        self.assertEqual(crossed["owner_on"], right_peer["id"])
        self.assertTrue(crossed["owner_away"])
        self.assertEqual(crossed["edge_pressure"], 0.0)

        # Returning home resets ownership
        home = json.loads(self.bridge.return_home())
        self.assertIsNone(home["owner_on"])
        self.assertFalse(home["owner_away"])

    def test_cross_family_modifier_translation_mac_vs_windows(self) -> None:
        state = json.loads(self.bridge.get_state_json())
        mac_peer = next(p for p in state["peers"] if p["platform"] == "macos")
        win_peer = next(p for p in state["peers"] if p["platform"] == "windows")

        # When driving a Mac from Android (PC family), 'ctrl' maps to 'cmd' by meaning
        self.bridge.switch_to_peer(mac_peer["id"])
        mac_res = json.loads(self.bridge.record_key_chord("ctrl", "c"))
        self.assertIn("Key chord cmd+c", mac_res["last_event"])

        # When driving Windows from Android (same PC family), 'ctrl' stays 'ctrl'
        self.bridge.switch_to_peer(win_peer["id"])
        win_res = json.loads(self.bridge.record_key_chord("ctrl", "c"))
        self.assertIn("Key chord ctrl+c", win_res["last_event"])

    def test_mouse_buttons_wheel_gestures_and_unicode_text(self) -> None:
        state = json.loads(self.bridge.get_state_json())
        peer_id = state["peers"][0]["id"]
        self.bridge.switch_to_peer(peer_id)

        btn_state = json.loads(self.bridge.record_mouse_button("left", True))
        self.assertIn("mousedown(left)", btn_state["last_event"])
        btn_up = json.loads(self.bridge.record_mouse_button("left", False))
        self.assertIn("mouseup(left)", btn_up["last_event"])

        wheel_state = json.loads(self.bridge.record_wheel(0.0, -4.5, "pixel"))
        self.assertIn("wheel(dx=0.0, dy=-4.5, pixel)", wheel_state["last_event"])

        gesture_state = json.loads(self.bridge.record_gesture("swipe", "up", 3))
        self.assertIn("Gesture swipe(up, 3f)", gesture_state["last_event"])

        text_state = json.loads(self.bridge.record_text("مرحبا Beamer 🚀"))
        self.assertIn("Sent Unicode text", text_state["last_event"])

    def test_clipboard_sync_and_256kb_cap(self) -> None:
        state = json.loads(self.bridge.get_state_json())
        peer_id = state["peers"][0]["id"]
        self.bridge.switch_to_peer(peer_id)

        ok_state = json.loads(self.bridge.on_local_clipboard_changed("Shared clipboard text"))
        self.assertIn("Clipboard updated", ok_state["last_event"])

        oversized = "A" * (protocol.CLIPBOARD_MAX_BYTES + 10)
        cap_state = json.loads(self.bridge.on_local_clipboard_changed(oversized))
        self.assertIn("exceeds 256KB cap", cap_state["last_event"])

    def test_tablet_responder_incoming_events_and_input_scale(self) -> None:
        # Configure 200% pointer speed, 150% scroll speed, and reverse scrolling
        self.bridge.update_preferences(
            style="beam",
            resistance_px=140,
            responder_enabled=True,
            pointer_speed=2.0,
            scroll_speed=1.5,
            reverse_scroll=True,
            same_on_all=True,
        )
        self.bridge.inject_incoming_remote_event(json.dumps({"type": "mousemove", "data": {"dx": 10, "dy": -5}}))
        self.bridge.inject_incoming_remote_event(json.dumps({"type": "wheel", "data": {"dx": 0, "dy": 4, "mode": "line"}}))
        self.bridge.inject_incoming_remote_event(json.dumps({"type": "gesture", "data": {"kind": "swipe", "direction": "up"}}))

        drained = json.loads(self.bridge.drain_remote_events_json())
        move_ev = next(e for e in drained if e["kind"] == "move")
        wheel_ev = next(e for e in drained if e["kind"] == "wheel")
        gest_ev = next(e for e in drained if e["kind"] == "gesture")

        self.assertEqual((move_ev["dx"], move_ev["dy"]), (20, -10))
        self.assertEqual(wheel_ev["dy"], -6.0)
        self.assertEqual(gest_ev["direction"], "up")

    def test_press_nearby_and_bilingual_toggle(self) -> None:
        nb_state = json.loads(self.bridge.begin_nearby())
        self.assertEqual(nb_state["nearby"]["state"], "searching")
        self.assertGreater(nb_state["nearby"]["seconds_left"], 0)

        cancelled = json.loads(self.bridge.cancel_nearby())
        self.assertIn(cancelled["nearby"]["state"], ("inactive", "refused", "expired"))

        en_state = json.loads(self.bridge.set_language("en"))
        self.assertEqual(en_state["ui_lang"], "en")

        ar_state = json.loads(self.bridge.toggle_language())
        self.assertEqual(ar_state["ui_lang"], "ar")


if __name__ == "__main__":
    unittest.main()

