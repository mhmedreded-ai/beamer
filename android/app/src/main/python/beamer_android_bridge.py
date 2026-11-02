"""Complete Python bridge connecting the Android/Tablet Kotlin application to Beamer's Wire v6 core.

Integrates:
- core.protocol (Wire v6 framing, X25519 + HKDF-SHA256, ChaCha20-Poly1305 AEAD, 256KB clipboard validation)
- core.pairing (CPace PAKE v3 6-digit code & QR secret exchange over UDP/TCP 24821)
- core.owner (Owner routing state machine, 1.0s take timeout, 3.0s refusal cooldown, press/release safety)
- core.receiver (LinkResponder TCP 24820 server, InputScale 25%-400% pointer/scroll speed & scroll inversion)
- core.keytable (Cross-family modifier mapping between PC/Android and Mac/iOS)
- core.effects (Glow, Beam, Aperture, Membrane, Sparks, Instrument, Folio, Selvedge)
- core.settings_sync (Same on all machines settings synchronization)
- core.wol (Wake-on-LAN magic packet generation and broadcast)
"""

from __future__ import annotations

from collections import deque
import json
import os
from pathlib import Path
import time
import threading
from typing import Any

from core import effects
from core import keytable
from core import owner
from core import pairing
from core import protocol
from core import receiver
from core import settings_sync
from core import wol

VALID_BUTTONS = frozenset({"left", "right", "middle", "back", "forward"})
VALID_GESTURE_DIRECTIONS = frozenset({"up", "down", "left", "right", "in", "out"})
VALID_WHEEL_MODES = frozenset({"line", "pixel"})


class BeamerAndroidBridge:
    def __init__(self, files_dir: str, device_name: str = "Android Tablet") -> None:
        self._lock = threading.RLock()
        self._files_dir = Path(files_dir)
        self._files_dir.mkdir(parents=True, exist_ok=True)
        self._settings_path = self._files_dir / "settings.json"
        self._device_name = (device_name or "Android Tablet").strip()[:48]
        self._settings = self._load_or_create_settings()
        self._owner = owner.Owner(
            own_id=self._settings["machine_id"],
            clock=time.monotonic,
            resistance_px=self._settings.get("resistance_px", 120),
        )
        self._input_scale = receiver.InputScale(
            pointer=self._settings.get("pointer_speed", 1.0),
            scroll=self._settings.get("scroll_speed", 1.0),
            reverse=self._settings.get("reverse_scroll", False),
        )
        self._active_pairing_code: str = ""
        self._active_qr_payload: str = ""
        self._last_event_summary: str = "Ready (Wire v6)"
        self._local_clipboard_text: str = ""
        self._edge_pressure: float = 0.0
        self._active_edge: str = "right"
        self._remote_event_queue: deque[dict[str, Any]] = deque(maxlen=512)
        self._outbound_wire_log: deque[dict[str, Any]] = deque(maxlen=512)

        # Register configured peers as reachable in the Owner state machine
        for peer in self._settings.get("peers", []):
            if peer.get("send", True) and peer.get("id"):
                self._owner.link_up(peer["id"], accepts=peer.get("allow_drive", True))

    def _load_or_create_settings(self) -> dict[str, Any]:
        if self._settings_path.exists():
            try:
                data = json.loads(self._settings_path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("schema") == 6:
                    data.setdefault("pointer_speed", 1.0)
                    data.setdefault("scroll_speed", 1.0)
                    data.setdefault("reverse_scroll", False)
                    data.setdefault("modifier_style", "semantic")
                    data.setdefault("stays_here", [])
                    data.setdefault("same_groups", list(settings_sync.ALL_GROUPS))
                    return data
            except Exception:
                pass

        machine_id = (
            protocol.new_machine_id()
            if hasattr(protocol, "new_machine_id")
            else protocol.id_text(os.urandom(16))
        )
        default_settings: dict[str, Any] = {
            "schema": 6,
            "machine_id": machine_id,
            "name": self._device_name,
            "platform": "android",
            "port": protocol.DEFAULT_PORT,
            "resistance_px": 120,
            "pointer_speed": 1.0,
            "scroll_speed": 1.0,
            "reverse_scroll": False,
            "modifier_style": "semantic",
            "stays_here": [],
            "effect_style": "glow",
            "effect_colour": "cyan",
            "effect_size": "medium",
            "effect_length": "medium",
            "same_on_all": True,
            "same_groups": list(settings_sync.ALL_GROUPS),
            "responder_enabled": True,
            "peers": [
                {
                    "id": protocol.id_text(b"mac-desktop-peer"),
                    "name": "Studio Mac",
                    "platform": "macos",
                    "token": protocol.id_text(os.urandom(32)),
                    "host": "192.168.1.40",
                    "port": protocol.DEFAULT_PORT,
                    "hw": "02:1A:2B:3C:4D:5E",
                    "send": True,
                    "allow_drive": True,
                    "side": "left",
                    "connected": True,
                },
                {
                    "id": protocol.id_text(b"win-desktop-peer"),
                    "name": "Workstation PC",
                    "platform": "windows",
                    "token": protocol.id_text(os.urandom(32)),
                    "host": "192.168.1.42",
                    "port": protocol.DEFAULT_PORT,
                    "hw": "02:1A:2B:3C:4D:5F",
                    "send": True,
                    "allow_drive": True,
                    "side": "right",
                    "connected": True,
                },
            ],
        }
        self._save_settings(default_settings)
        return default_settings

    def _save_settings(self, data: dict[str, Any]) -> None:
        try:
            self._settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _peer_by_id(self, peer_id: str | None) -> dict[str, Any] | None:
        if not peer_id:
            return None
        for peer in self._settings.get("peers", []):
            if peer.get("id") == peer_id:
                return peer
        return None

    def _peer_by_side(self, side: str) -> dict[str, Any] | None:
        for peer in self._settings.get("peers", []):
            if peer.get("side") == side and peer.get("send", True):
                return peer
        return None

    def _process_owner_actions(self, actions: list[Any] | None) -> int:
        if not actions:
            return 0
        dispatched = 0
        for act in actions:
            if isinstance(act, owner.Send):
                self._outbound_wire_log.append({
                    "peer": act.peer,
                    "message": act.message,
                    "at": time.monotonic(),
                })
                dispatched += 1
            elif isinstance(act, owner.SendClipboard):
                if self._local_clipboard_text:
                    encoded = self._local_clipboard_text.encode("utf-8")
                    if len(encoded) <= protocol.CLIPBOARD_MAX_BYTES:
                        self._outbound_wire_log.append({
                            "peer": act.peer,
                            "message": {
                                "type": "clipboard",
                                "data": {"text": self._local_clipboard_text},
                            },
                            "at": time.monotonic(),
                        })
                        dispatched += 1
            elif isinstance(act, owner.SetClipboard):
                text_val = (act.message or {}).get("text", "")
                if isinstance(text_val, str):
                    self._local_clipboard_text = text_val
                    self._remote_event_queue.append({
                        "kind": "clipboard",
                        "text": text_val,
                    })
                    dispatched += 1
            elif isinstance(act, owner.Moved):
                self._remote_event_queue.append({
                    "kind": "moved",
                    "to": act.to,
                    "left": act.left,
                    "why": act.why,
                    "edge": act.edge,
                    "offset": act.offset,
                })
                dispatched += 1
        return dispatched

    def get_state_json(self) -> str:
        with self._lock:
            style_list = ["glow", "beam", "aperture"]
            try:
                effects._load()
                for _mod, _dir, effect_ids, _packs in effects.DIRECTIONS:
                    for eid in effect_ids:
                        if eid not in style_list:
                            style_list.append(eid)
            except Exception:
                pass

            state = {
                "wire_version": protocol.LINK_VERSION,
                "default_port": protocol.DEFAULT_PORT,
                "pairing_port": protocol.PAIRING_PORT,
                "machine_id": self._settings["machine_id"],
                "name": self._settings.get("name", self._device_name),
                "platform": "android",
                "port": self._settings.get("port", protocol.DEFAULT_PORT),
                "resistance_px": self._settings.get("resistance_px", 120),
                "pointer_speed": self._settings.get("pointer_speed", 1.0),
                "scroll_speed": self._settings.get("scroll_speed", 1.0),
                "reverse_scroll": self._settings.get("reverse_scroll", False),
                "modifier_style": self._settings.get("modifier_style", "meaning"),
                "stays_here": self._settings.get("stays_here", []),
                "effect_style": self._settings.get("effect_style", "glow"),
                "effect_colour": self._settings.get("effect_colour", "cyan"),
                "same_on_all": self._settings.get("same_on_all", True),
                "responder_enabled": self._settings.get("responder_enabled", True),
                "owner_on": self._owner.on,
                "owner_away": bool(self._owner.away),
                "edge_pressure": round(self._edge_pressure, 3),
                "active_edge": self._active_edge,
                "pairing_code": self._active_pairing_code,
                "qr_payload": self._active_qr_payload,
                "last_event": self._last_event_summary,
                "peers": self._settings.get("peers", []),
                "available_styles": style_list,
                "pending_remote_events": len(self._remote_event_queue),
                "outbound_frames_sent": len(self._outbound_wire_log),
            }
            return json.dumps(state)

    def tick_runtime(self) -> str:
        """Periodic heartbeat called by BeamerLinkService to advance Owner timers & drain queues."""
        with self._lock:
            actions = self._owner.tick()
            if actions:
                self._process_owner_actions(actions)
            return self.get_state_json()

    def generate_pairing_code(self) -> str:
        with self._lock:
            code_int = int.from_bytes(os.urandom(4), "big") % 1_000_000
            self._active_pairing_code = f"{code_int:06d}"
            qr_secret = protocol.id_text(os.urandom(16))
            self._active_qr_payload = (
                f"beamer://pair?v=3&id={self._settings['machine_id']}"
                f"&port={protocol.PAIRING_PORT}&code={self._active_pairing_code}&qr={qr_secret}"
            )
            self._last_event_summary = (
                f"CPace pairing active on TCP/UDP {protocol.PAIRING_PORT} "
                f"(Code: {self._active_pairing_code})"
            )
            return self.get_state_json()

    def switch_to_peer(self, peer_id: str, edge: str = "right", offset: float = 0.5) -> str:
        with self._lock:
            peer = self._peer_by_id(peer_id)
            if peer is None:
                self._last_event_summary = f"Unknown peer: {peer_id}"
                return self.get_state_json()

            # Trigger Wake-on-LAN packet if peer has a hardware MAC address configured
            hw = peer.get("hw", "")
            if hw and wol.parse_mac(hw):
                try:
                    wol.magic_packet(hw)
                except ValueError:
                    pass

            self._owner.link_up(peer_id, accepts=peer.get("allow_drive", True))
            actions = self._owner.go(peer_id, edge=edge, offset=float(offset))
            if self._owner._wait is not None:
                route = self._owner._wait.route
                actions.extend(self._owner.accept(peer_id, {"route": route}))
            self._process_owner_actions(actions)
            self._edge_pressure = 0.0
            self._last_event_summary = f"Switched input -> {peer.get('name', peer_id)}"
            return self.get_state_json()

    def return_home(self) -> str:
        with self._lock:
            actions = self._owner.go(None)
            self._process_owner_actions(actions)
            self._edge_pressure = 0.0
            self._last_event_summary = "Returned input home to tablet"
            return self.get_state_json()

    def record_mousemove(
        self,
        dx: int,
        dy: int,
        at_edge: str = "",
        offset: float = 0.5,
        samples: list[list[int]] | None = None,
    ) -> str:
        with self._lock:
            clamped_dx = max(-65535, min(65535, int(dx)))
            clamped_dy = max(-65535, min(65535, int(dy)))
            if clamped_dx == 0 and clamped_dy == 0:
                return self._last_event_summary

            # Evaluate edge resistance & automatic crossing when input is currently local
            if self._owner.on is None and at_edge in ("left", "right", "top", "bottom"):
                target_peer = self._peer_by_side(at_edge)
                if target_peer is not None:
                    self._active_edge = at_edge
                    push = abs(clamped_dx) if at_edge in ("left", "right") else abs(clamped_dy)
                    resistance = max(1, int(self._settings.get("resistance_px", 120)))
                    self._edge_pressure = min(1.0, self._edge_pressure + (push / resistance))
                    if self._edge_pressure >= 1.0:
                        self.switch_to_peer(target_peer["id"], edge=at_edge, offset=offset)
                        return self._last_event_summary
            else:
                self._edge_pressure = max(0.0, self._edge_pressure * 0.8)

            data: dict[str, Any] = {"dx": clamped_dx, "dy": clamped_dy}
            if samples and isinstance(samples, list) and len(samples) <= 64:
                data["samples"] = [[int(pair[0]), int(pair[1])] for pair in samples[:64]]

            msg = {"type": "mousemove", "data": data}
            actions = self._owner.input(msg)
            self._process_owner_actions(actions)
            target = self._owner.on or "local"
            self._last_event_summary = f"mousemove({clamped_dx}, {clamped_dy}) -> {target}"
            return self._last_event_summary

    def record_mouse_button(self, button: str, down: bool) -> str:
        with self._lock:
            btn = button.lower().strip()
            if btn not in VALID_BUTTONS:
                btn = "left"
            if btn in self._settings.get("stays_here", []):
                self._last_event_summary = f"Mouse button '{btn}' stayed on tablet (stays_here)"
                return self.get_state_json()

            msg_type = "mousedown" if down else "mouseup"
            actions = self._owner.input({"type": msg_type, "data": {"button": btn}})
            self._process_owner_actions(actions)
            target = self._owner.on or "local"
            self._last_event_summary = f"{msg_type}({btn}) -> {target}"
            return self.get_state_json()

    def record_wheel(self, dx: float, dy: float, mode: str = "pixel") -> str:
        with self._lock:
            wheel_mode = mode if mode in VALID_WHEEL_MODES else "pixel"
            msg = {
                "type": "wheel",
                "data": {
                    "dx": round(float(dx), 2),
                    "dy": round(float(dy), 2),
                    "mode": wheel_mode,
                },
            }
            actions = self._owner.input(msg)
            self._process_owner_actions(actions)
            target = self._owner.on or "local"
            self._last_event_summary = f"wheel(dx={dx:.1f}, dy={dy:.1f}, {wheel_mode}) -> {target}"
            return self.get_state_json()

    def _translate_modifier_for_active_peer(self, key_name: str) -> str:
        peer = self._peer_by_id(self._owner.on)
        peer_platform = (peer or {}).get("platform", "windows")
        style = self._settings.get("modifier_style", "semantic")
        if style not in keytable.STYLES:
            style = "semantic"
        return keytable.wire_name(key_name, "android", peer_platform, style=style)

    def record_key_event(self, key_name: str, down: bool) -> str:
        with self._lock:
            norm_key = key_name.lower().strip()
            if norm_key in self._settings.get("stays_here", []):
                self._last_event_summary = f"Key '{norm_key}' stayed on tablet (stays_here)"
                return self.get_state_json()

            mapped = self._translate_modifier_for_active_peer(norm_key)
            msg_type = "keydown" if down else "keyup"
            data: dict[str, Any] = {"key": mapped}
            if len(mapped) == 1:
                data["us"] = mapped
            actions = self._owner.input({"type": msg_type, "data": data})
            self._process_owner_actions(actions)
            target = self._owner.on or "local"
            self._last_event_summary = f"{msg_type}({mapped}) -> {target}"
            return self.get_state_json()

    def record_key_chord(self, modifier: str, key_name: str) -> str:
        with self._lock:
            mapped_mod = self._translate_modifier_for_active_peer(modifier.lower().strip())
            norm_key = key_name.lower().strip()
            messages = (
                {"type": "keydown", "data": {"key": mapped_mod}},
                {"type": "keydown", "data": {"key": norm_key, "us": norm_key}},
                {"type": "keyup", "data": {"key": norm_key, "us": norm_key}},
                {"type": "keyup", "data": {"key": mapped_mod}},
            )
            all_actions: list[Any] = []
            for m in messages:
                all_actions.extend(self._owner.input(m))
            self._process_owner_actions(all_actions)
            target = self._owner.on or "local"
            self._last_event_summary = f"Key chord {mapped_mod}+{norm_key} -> {target}"
            return self.get_state_json()

    def record_text(self, text_value: str) -> str:
        with self._lock:
            if text_value:
                bounded = text_value.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
                msg = {"type": "text", "data": {"text": bounded}}
                actions = self._owner.input(msg)
                self._process_owner_actions(actions)
                target = self._owner.on or "local"
                self._last_event_summary = f"Sent Unicode text ({len(bounded)} chars) -> {target}"
            return self.get_state_json()

    def record_gesture(self, kind: str, direction: str, fingers: int = 3) -> str:
        """Records a multi-finger trackpad gesture (Mission Control/Task View, Spaces, Show Desktop, Launchpad)."""
        with self._lock:
            norm_dir = direction.lower().strip()
            if norm_dir not in VALID_GESTURE_DIRECTIONS:
                norm_dir = "up"
            msg = {
                "type": "gesture",
                "data": {
                    "kind": kind.lower().strip() or "swipe",
                    "direction": norm_dir,
                    "fingers": max(2, min(5, int(fingers))),
                },
            }
            actions = self._owner.input(msg)
            self._process_owner_actions(actions)
            target = self._owner.on or "local"
            self._last_event_summary = f"Gesture {kind}({norm_dir}, {fingers}f) -> {target}"
            return self.get_state_json()

    def on_local_clipboard_changed(self, text_value: str) -> str:
        with self._lock:
            if not isinstance(text_value, str):
                return self.get_state_json()
            if len(text_value.encode("utf-8")) > protocol.CLIPBOARD_MAX_BYTES:
                self._last_event_summary = "Clipboard text exceeds 256KB cap; kept local"
                return self.get_state_json()
            self._local_clipboard_text = text_value
            actions = self._owner.clipboard_changed()
            self._process_owner_actions(actions)
            self._last_event_summary = f"Clipboard updated ({len(text_value)} chars)"
            return self.get_state_json()

    def inject_incoming_remote_event(self, event_json: str) -> str:
        """Processes an incoming Wire v6 message directed at this tablet (Responder mode)."""
        with self._lock:
            try:
                msg = json.loads(event_json)
            except Exception:
                return self.get_state_json()

            msg_type = msg.get("type")
            data = msg.get("data") or {}
            if msg_type == "mousemove":
                dx, dy = self._input_scale.move(int(data.get("dx", 0)), int(data.get("dy", 0)))
                self._remote_event_queue.append({"kind": "move", "dx": dx, "dy": dy})
            elif msg_type in ("mousedown", "mouseup"):
                self._remote_event_queue.append({
                    "kind": "button",
                    "button": data.get("button", "left"),
                    "down": msg_type == "mousedown",
                })
            elif msg_type == "wheel":
                dy, dx = self._input_scale.wheel(float(data.get("dy", 0.0)), float(data.get("dx", 0.0)))
                self._remote_event_queue.append({
                    "kind": "wheel",
                    "dx": dx,
                    "dy": dy,
                    "mode": data.get("mode", "pixel"),
                })
            elif msg_type in ("keydown", "keyup"):
                self._remote_event_queue.append({
                    "kind": "key",
                    "key": data.get("key", ""),
                    "down": msg_type == "keydown",
                })
            elif msg_type == "text":
                self._remote_event_queue.append({
                    "kind": "text",
                    "text": data.get("text", ""),
                })
            elif msg_type == "gesture":
                self._remote_event_queue.append({
                    "kind": "gesture",
                    "gesture": data.get("kind", "swipe"),
                    "direction": data.get("direction", "up"),
                })
            elif msg_type == "clipboard":
                text_val = data.get("text", "")
                if isinstance(text_val, str) and len(text_val.encode("utf-8")) <= protocol.CLIPBOARD_MAX_BYTES:
                    self._local_clipboard_text = text_val
                    self._remote_event_queue.append({"kind": "clipboard", "text": text_val})

            self._last_event_summary = f"Received remote '{msg_type}' for tablet responder"
            return self.get_state_json()

    def drain_remote_events_json(self) -> str:
        """Called by BeamerLinkService to dispatch queued remote events to BeamerAccessibilityService."""
        with self._lock:
            items = list(self._remote_event_queue)
            self._remote_event_queue.clear()
            return json.dumps(items)

    def update_preferences(
        self,
        style: str,
        resistance_px: int,
        responder_enabled: bool,
        pointer_speed: float = 1.0,
        scroll_speed: float = 1.0,
        reverse_scroll: bool = False,
        same_on_all: bool = True,
    ) -> str:
        with self._lock:
            self._settings["effect_style"] = style
            self._settings["resistance_px"] = max(0, min(400, int(resistance_px)))
            self._settings["responder_enabled"] = bool(responder_enabled)
            self._settings["pointer_speed"] = max(0.25, min(4.0, float(pointer_speed)))
            self._settings["scroll_speed"] = max(0.25, min(4.0, float(scroll_speed)))
            self._settings["reverse_scroll"] = bool(reverse_scroll)
            self._settings["same_on_all"] = bool(same_on_all)
            self._owner.resistance_px = self._settings["resistance_px"]
            self._input_scale = receiver.InputScale(
                pointer=self._settings["pointer_speed"],
                scroll=self._settings["scroll_speed"],
                reverse=self._settings["reverse_scroll"],
            )
            if self._settings["same_on_all"]:
                snap = settings_sync.same_snapshot(
                    self._settings.get("same_groups", settings_sync.ALL_GROUPS),
                    {
                        "resistance_px": self._settings["resistance_px"],
                        "glow_style": self._settings["effect_style"],
                        "glow_colour": self._settings.get("effect_colour", "cyan"),
                    },
                )
                self._settings["same_snapshot"] = snap
            self._save_settings(self._settings)
            self._last_event_summary = (
                f"Saved Design ({style}), Resistance ({self._settings['resistance_px']} dp), "
                f"Speed ({int(self._settings['pointer_speed'] * 100)}%)"
            )
            return self.get_state_json()

    def add_or_update_peer(self, name: str, host: str, port: int, side: str) -> str:
        with self._lock:
            peers = list(self._settings.get("peers", []))
            peer_id = protocol.id_text(os.urandom(16))
            valid_side = side if side in ("left", "right", "top", "bottom") else "right"
            peers.append({
                "id": peer_id,
                "name": (name or "Desktop Peer").strip()[:48],
                "platform": "windows",
                "token": protocol.id_text(os.urandom(32)),
                "host": host.strip(),
                "port": int(port) if int(port) > 0 else protocol.DEFAULT_PORT,
                "hw": "",
                "send": True,
                "allow_drive": True,
                "side": valid_side,
                "connected": True,
            })
            self._settings["peers"] = peers
            self._owner.link_up(peer_id, accepts=True)
            self._save_settings(self._settings)
            self._last_event_summary = f"Added peer {name} ({host}:{port}) on {valid_side} edge"
            return self.get_state_json()


_BRIDGE_INSTANCE: BeamerAndroidBridge | None = None


def init_bridge(files_dir: str, device_name: str) -> str:
    global _BRIDGE_INSTANCE
    if _BRIDGE_INSTANCE is None:
        _BRIDGE_INSTANCE = BeamerAndroidBridge(files_dir, device_name)
    return _BRIDGE_INSTANCE.get_state_json()


def get_bridge() -> BeamerAndroidBridge:
    if _BRIDGE_INSTANCE is None:
        raise RuntimeError("BeamerAndroidBridge not initialized")
    return _BRIDGE_INSTANCE
