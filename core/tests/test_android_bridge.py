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

    def test_honor_magicpad2_cursor_coordinates_and_boundary_return_host(self) -> None:
        # Configure Honor MagicPad 2 display geometry (3000x1920 @ 290 DPI)
        self.bridge.set_screen_geometry(3000, 1920, 290.0 / 160.0)
        self.bridge._desktop_adapter.set_cursor_position(40, 960)
        self.bridge.drain_remote_events_json()

        # Simulate remote PC taking ownership on left edge (arrival_at in the past past 0.30s grace)
        self.bridge._on_responder_owner("win-desktop-peer")
        self.bridge._on_responder_arrival("left", 40, 960)
        self.bridge._arrival_at -= 1.0
        self.bridge.drain_remote_events_json()

        # Move left toward x=0 and push past boundary to trigger SWITCH_HOST back to Windows
        self.bridge.inject_incoming_remote_event(json.dumps({"type": "mousemove", "data": {"dx": -25, "dy": 0}}))
        self.bridge.inject_incoming_remote_event(json.dumps({"type": "mousemove", "data": {"dx": -30, "dy": 0}}))

        events = json.loads(self.bridge.drain_remote_events_json())
        kinds = [e["kind"] for e in events]
        self.assertIn("move", kinds)
        self.assertIn("switch_host", kinds)
        self.assertIn("cursor_hide", kinds)
        switch_ev = next(e for e in events if e["kind"] == "switch_host")
        self.assertEqual(switch_ev["edge"], "left")

    def test_token_manager_proactive_refresh_and_http_401_retry(self) -> None:
        from core import token_manager
        import email.message
        import urllib.error

        now_Box = [1000.0]
        refresh_calls = [0]

        def fake_refresh(state: dict) -> dict:
            refresh_calls[0] += 1
            return {
                "access_token": f"fresh_token_{refresh_calls[0]}",
                "refresh_token": state.get("refresh_token", "rt_1"),
                "expires_in": 3600,
            }

        cache_file = Path(self.temp_dir.name) / "oauth_cache.json"
        mgr = token_manager.TokenManager(
            cache_path=cache_file,
            access_token="stale_token",
            refresh_token="rt_valid",
            expires_at=1050.0,  # Within 120s refresh margin -> proactive refresh!
            refresh_margin_seconds=120.0,
            refresh_callback=fake_refresh,
            clock=lambda: now_Box[0],
        )

        # 1. Proactive refresh before expiration
        tok1 = mgr.get_valid_token()
        self.assertEqual(tok1, "fresh_token_1")
        self.assertEqual(refresh_calls[0], 1)
        self.assertTrue(cache_file.exists())

        # 2. Automatic retry on HTTP 401 Www-Authenticate: Bearer error="invalid_token"
        attempts = [0]

        def flaky_api_call(access_token: str = "") -> str:
            attempts[0] += 1
            if attempts[0] == 1:
                hdrs = email.message.Message()
                hdrs["Www-Authenticate"] = 'Bearer realm="https://accounts.google.com/", error="invalid_token"'
                raise urllib.error.HTTPError(
                    url="https://api.example.com/v1/resource",
                    code=401,
                    msg="Unauthorized",
                    hdrs=hdrs,
                    fp=None,
                )
            return f"OK:{access_token}"

        result = mgr.execute_with_retry(flaky_api_call, inject_token_kwarg="access_token")
        self.assertEqual(result, "OK:fresh_token_2")
        self.assertEqual(attempts[0], 2)
        self.assertEqual(refresh_calls[0], 2)


if __name__ == "__main__":
    unittest.main()

