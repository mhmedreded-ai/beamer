"""Complete Python bridge connecting the Android/Tablet Kotlin application to Beamer's Wire v6 core.

Integrates:
- core.protocol (Wire v6 framing, X25519 + HKDF-SHA256, ChaCha20-Poly1305 AEAD, 256KB clipboard validation)
- core.pairing & core.easy_pair (CPace PAKE v3 6-digit code, QR secret, & "Press Nearby" SAS v1 over UDP/TCP 24821)
- core.link (OutboundLink encrypted TCP 24820 client per paired peer)
- core.owner (Owner routing state machine, 1.0s take timeout, 3.0s refusal cooldown, press/release safety)
- core.receiver (LinkResponder TCP 24820 server, InputScale 25%-400% pointer/scroll speed & scroll inversion)
- core.keytable (Cross-family modifier mapping between PC/Android and Mac/iOS)
- core.effects (Glow, Beam, Aperture, Membrane, Sparks, Instrument, Folio, Selvedge)
- core.settings_sync (Same on all machines settings synchronization)
- core.wol (Wake-on-LAN magic packet generation and broadcast)
"""

from __future__ import annotations

from collections import deque
import copy
import json
import os
from pathlib import Path
import time
import threading
from typing import Any

from core import effects
from core import keytable
from core import link as core_link
from core import owner
from core import pairing
from core import protocol
from core import receiver
from core import return_edge as crossing
from core import settings_sync
from core import token_manager
from core import wol

VALID_BUTTONS = frozenset({"left", "right", "middle", "back", "forward"})
VALID_GESTURE_DIRECTIONS = frozenset({"up", "down", "left", "right", "in", "out"})
VALID_WHEEL_MODES = frozenset({"line", "pixel"})


class _AndroidInjectorAdapter:
    """Adapter translating LinkResponder injections into BeamerAccessibilityService events."""

    def __init__(self, bridge: BeamerAndroidBridge) -> None:
        self._bridge = bridge

    def inject_mouse_move(self, dx: int, dy: int) -> None:
        cur_x, cur_y, hit_edge, hit_offset = self._bridge._desktop_adapter.move_by(int(dx), int(dy))
        self._bridge._enqueue_remote({
            "kind": "move",
            "dx": int(dx),
            "dy": int(dy),
            "x": int(cur_x),
            "y": int(cur_y),
        })
        if hit_edge is not None:
            self._bridge._on_remote_boundary_push(int(dx), int(dy), hit_edge, hit_offset)
        else:
            self._bridge._return_edge_push = max(0.0, self._bridge._return_edge_push * 0.5)

    def inject_mouse_button(self, button: str, down: bool = True) -> None:
        cur_x, cur_y = self._bridge._desktop_adapter.cursor_position()
        self._bridge._enqueue_remote({
            "kind": "button",
            "button": str(button),
            "down": bool(down),
            "x": int(cur_x),
            "y": int(cur_y),
        })

    def inject_scroll(self, dy: float, dx: float, mode: str = "pixel") -> None:
        cur_x, cur_y = self._bridge._desktop_adapter.cursor_position()
        self._bridge._enqueue_remote({
            "kind": "wheel",
            "dx": float(dx),
            "dy": float(dy),
            "mode": str(mode),
            "x": int(cur_x),
            "y": int(cur_y),
        })

    def inject_key(self, key: str, down: bool = True, us: str | None = None) -> None:
        self._bridge._enqueue_remote({"kind": "key", "key": str(key), "down": bool(down), "us": us or ""})

    def inject_gesture(self, name: str) -> None:
        direction = "up"
        if "down" in name or "desktop" in name:
            direction = "down"
        elif "left" in name:
            direction = "left"
        elif "right" in name:
            direction = "right"
        self._bridge._enqueue_remote({"kind": "gesture", "gesture": "swipe", "direction": direction})

    def inject_text(self, chars: str) -> None:
        if chars:
            self._bridge._enqueue_remote({"kind": "text", "text": str(chars)})

    def release_all(self) -> None:
        self._bridge._enqueue_remote({"kind": "release_all"})


class _AndroidDesktopAdapter:
    """Virtual monitor geometry adapter for Honor MagicPad 2 (3000x1920 @ 290 DPI) & Android Tablets."""

    def __init__(self, bridge: BeamerAndroidBridge | None = None) -> None:
        self._bridge = bridge
        self.width: int = 3000
        self.height: int = 1920
        self.density: float = 290.0 / 160.0
        self._cursor: list[int] = [1500, 960]

    def set_geometry(self, width: int, height: int, density: float = 1.8125) -> None:
        w = max(320, int(width))
        h = max(320, int(height))
        self.width = w
        self.height = h
        self.density = max(0.5, float(density))
        self._cursor[0] = max(0, min(w - 1, self._cursor[0]))
        self._cursor[1] = max(0, min(h - 1, self._cursor[1]))

    def monitors(self) -> list[Any]:
        return [crossing.Rect(0, 0, self.width, self.height)]

    def display_map(self) -> dict[str, Any]:
        return {"0": crossing.Rect(0, 0, self.width, self.height)}

    def cursor_position(self) -> tuple[int, int]:
        return (self._cursor[0], self._cursor[1])

    def set_cursor_position(self, x: int, y: int) -> None:
        clamped_x = max(0, min(self.width - 1, int(x)))
        clamped_y = max(0, min(self.height - 1, int(y)))
        self._cursor = [clamped_x, clamped_y]
        if self._bridge is not None:
            self._bridge._enqueue_remote({
                "kind": "cursor_pos",
                "x": clamped_x,
                "y": clamped_y,
            })

    def move_by(self, dx: int, dy: int) -> tuple[int, int, str | None, float]:
        raw_x = self._cursor[0] + int(dx)
        raw_y = self._cursor[1] + int(dy)
        clamped_x = max(0, min(self.width - 1, raw_x))
        clamped_y = max(0, min(self.height - 1, raw_y))
        self._cursor = [clamped_x, clamped_y]

        hit_edge: str | None = None
        hit_offset: float = 0.5
        if raw_x <= 0 and dx < 0:
            hit_edge = "left"
            hit_offset = clamped_y / max(1, self.height - 1)
        elif raw_x >= self.width - 1 and dx > 0:
            hit_edge = "right"
            hit_offset = clamped_y / max(1, self.height - 1)
        elif raw_y <= 0 and dy < 0:
            hit_edge = "top"
            hit_offset = clamped_x / max(1, self.width - 1)
        elif raw_y >= self.height - 1 and dy > 0:
            hit_edge = "bottom"
            hit_offset = clamped_x / max(1, self.width - 1)

        return (clamped_x, clamped_y, hit_edge, max(0.0, min(1.0, float(hit_offset))))


class _AndroidClipboardAdapter:
    """Clipboard adapter bridging LinkResponder clipboard transfers with Android ClipboardManager."""

    def __init__(self, bridge: BeamerAndroidBridge) -> None:
        self._bridge = bridge
        self._stamp = 1

    def bump(self) -> None:
        self._stamp += 1

    def change_stamp(self) -> int:
        return self._stamp

    def get_contents(self) -> tuple[str | None, bytes | None]:
        return (self._bridge._local_clipboard_text or None, None)

    def set_contents(self, text: str | None, image: bytes | None = None) -> bool:
        if isinstance(text, str) and text:
            self._bridge._local_clipboard_text = text
            self._bridge._enqueue_remote({"kind": "clipboard", "text": text})
            self._stamp += 1
        return True


class BeamerAndroidBridge:
    def __init__(
        self,
        files_dir: str,
        device_name: str = "Android Tablet",
        *,
        enable_network: bool = False,
    ) -> None:
        self._lock = threading.RLock()
        self._book_lock = threading.RLock()
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
        self._responder_owner_id: str = ""
        self._entry_edge: str = "left"
        self._arrival_at: float = 0.0
        self._return_edge_push: float = 0.0
        self._remote_event_queue: deque[dict[str, Any]] = deque(maxlen=512)
        self._outbound_wire_log: deque[dict[str, Any]] = deque(maxlen=512)

        self._token_manager = token_manager.TokenManager(
            cache_path=self._files_dir / "token_cache.json"
        )
        if self._token_manager.can_refresh():
            self._token_manager.start_background_refresh()

        self._injector_adapter = _AndroidInjectorAdapter(self)
        self._desktop_adapter = _AndroidDesktopAdapter(self)
        self._clipboard_adapter = _AndroidClipboardAdapter(self)
        self._peer_book = receiver.PeerBook(
            load=lambda: self._settings,
            save=self._save_settings_from_book,
            lock=self._book_lock,
        )
        self._outbound_links: dict[str, core_link.OutboundLink] = {}
        self._pairing: pairing.PairingService | None = None
        self._responder: receiver.LinkResponder | None = None
        self._fallback_nearby: Any = None
        self._enable_network = bool(enable_network)

        # Register configured peers in the Owner state machine
        for peer in self._settings.get("peers", []):
            if peer.get("send", True) and peer.get("id"):
                self._owner.link_up(peer["id"], accepts=peer.get("allow_drive", True))

        if self._enable_network:
            self._start_network_services()

    def _enqueue_remote(self, item: dict[str, Any]) -> None:
        self._remote_event_queue.append(item)

    def _zones_for_responder(self) -> list[dict[str, Any]]:
        """Builds active return-edge zones for LinkResponder so that pushing against the shared edge
        (or any boundary edge when driven by a desktop host) returns the cursor to the host."""
        with self._lock:
            zones: list[dict[str, Any]] = []
            peers = list(self._settings.get("peers", []))
            owner_id = self._responder_owner_id
            used_edges: set[str] = set()
            for peer in peers:
                pid = peer.get("id")
                side = peer.get("side")
                if pid and side in ("left", "right", "top", "bottom"):
                    zones.append({"peer": pid, "kind": "edge", "edge": side})
                    used_edges.add(side)
            # If a desktop host is actively driving this tablet, ensure the entry edge and any
            # unassigned screen edges also return home to the driving host so the cursor is never trapped!
            fallback_peer = owner_id or (peers[0].get("id", "") if peers else "")
            if fallback_peer:
                for edge in ("left", "right", "top", "bottom"):
                    if edge not in used_edges:
                        zones.append({"peer": fallback_peer, "kind": "edge", "edge": edge})
            return zones

    def _on_responder_owner(self, peer_id: str) -> None:
        with self._lock:
            self._responder_owner_id = str(peer_id or "")
            self._return_edge_push = 0.0
            if self._responder_owner_id:
                self._arrival_at = time.monotonic()
                cur_x, cur_y = self._desktop_adapter.cursor_position()
                self._enqueue_remote({
                    "kind": "arrival",
                    "x": cur_x,
                    "y": cur_y,
                    "edge": self._entry_edge or "left",
                })
                self._last_event_summary = "Remote PC cursor active on tablet"
            else:
                self._enqueue_remote({"kind": "cursor_hide"})
                self._last_event_summary = "Cursor returned to PC"

    def _on_responder_arrival(self, edge: str | None, x: int, y: int) -> None:
        with self._lock:
            self._arrival_at = time.monotonic()
            self._return_edge_push = 0.0
            if edge in ("left", "right", "top", "bottom"):
                self._entry_edge = edge
            self._enqueue_remote({
                "kind": "arrival",
                "x": int(x),
                "y": int(y),
                "edge": edge or self._entry_edge or "left",
            })

    def _on_remote_boundary_push(self, dx: int, dy: int, hit_edge: str, hit_offset: float) -> None:
        """Guarantees that pushing the cursor against the tablet screen boundary returns control
        (`SWITCH_HOST` / `switch_v6`) to the Windows host without getting trapped."""
        with self._lock:
            now = time.monotonic()
            # 0.30s grace period right after arrival so entry velocity doesn't immediately bounce back
            if (now - self._arrival_at) < 0.30:
                return
            push_delta = abs(dx) if hit_edge in ("left", "right") else abs(dy)
            self._return_edge_push += max(1.0, float(push_delta))
            # Lower threshold on the entry edge (20px) and standard threshold on any boundary edge (48px)
            threshold = 20.0 if hit_edge == self._entry_edge else min(64.0, max(24.0, float(self._settings.get("resistance_px", 120)) * 0.45))
            if self._return_edge_push >= threshold:
                self._return_edge_push = 0.0
                self._trigger_switch_host_back(hit_edge, hit_offset)

    def _trigger_switch_host_back(self, edge: str = "left", offset: float = 0.5) -> bool:
        """Immediately sends `switch_v6` (`SWITCH_HOST`) back to the driving Windows host."""
        sent = False
        if self._responder is not None:
            try:
                if self._responder.owner is not None:
                    sent = bool(self._responder.send_home())
            except Exception:
                sent = False
        with self._lock:
            self._responder_owner_id = ""
            self._edge_pressure = 0.0
            self._enqueue_remote({
                "kind": "switch_host",
                "edge": edge,
                "offset": float(offset),
            })
            self._enqueue_remote({"kind": "cursor_hide"})
            self._last_event_summary = f"SWITCH_HOST ({edge}) -> Returned cursor to PC"
        return sent

    def set_screen_geometry(self, width: int, height: int, density: float = 1.8125) -> str:
        """Updates the Honor MagicPad 2 / Android display resolution and DPI scaling dynamically."""
        with self._lock:
            self._desktop_adapter.set_geometry(int(width), int(height), float(density))
            return self.get_state_json()

    def on_cursor_edge_hit(self, edge: str, offset: float = 0.5) -> str:
        """Called by BeamerAccessibilityService when the floating cursor hits a physical screen boundary."""
        with self._lock:
            now = time.monotonic()
            if (now - self._arrival_at) >= 0.30:
                self._trigger_switch_host_back(edge, float(offset))
            return self.get_state_json()

    def configure_token_manager(
        self,
        access_token: str = "",
        refresh_token: str = "",
        expires_in: float = 3600.0,
        client_id: str = "",
        client_secret: str = "",
        token_uri: str = token_manager.DEFAULT_GOOGLE_TOKEN_URI,
    ) -> str:
        with self._lock:
            self._token_manager.set_credentials(
                access_token=access_token or None,
                refresh_token=refresh_token or None,
                expires_in=float(expires_in),
                client_id=client_id or None,
                client_secret=client_secret or None,
                token_uri=token_uri or None,
            )
            if self._token_manager.can_refresh():
                self._token_manager.start_background_refresh()
            return json.dumps(self._token_manager.status())

    def get_token_status_json(self) -> str:
        with self._lock:
            return json.dumps(self._token_manager.status())

    def _identity_Bytes(self) -> bytes:
        raw = protocol.read_id(self._settings.get("machine_id", ""))
        if raw is not None and len(raw) == 16 and any(raw):
            return raw
        fresh = os.urandom(16)
        self._settings["machine_id"] = protocol.id_text(fresh)
        return fresh

    def _identity_dict(self) -> dict[str, Any]:
        return {
            "id": self._identity_Bytes(),
            "name": (self._settings.get("name") or self._device_name)[:48],
            "platform": "android",
            "app": "1.5.18",
            "port": int(self._settings.get("port", protocol.DEFAULT_PORT)),
            "caps": ["clipboard", "gestures", "text", "settings", "ping_rtt", "motion_path"],
        }

    def _save_settings_from_book(self, updated: dict[str, Any]) -> None:
        with self._lock:
            self._settings = updated
            self._save_settings(self._settings)

    def _store_paired_entry(self, entry: dict[str, Any], replaced: dict[str, Any] | None = None) -> None:
        with self._book_lock:
            with self._lock:
                peers = list(self._settings.get("peers", []))
                if replaced is not None:
                    rep_id = replaced.get("id")
                    rep_tok = replaced.get("token")
                    peers = [
                        p for p in peers
                        if not ((rep_id and p.get("id") == rep_id) or (rep_tok and p.get("token") == rep_tok))
                    ]
                peers = [p for p in peers if p.get("id") != entry.get("id")]
                if not entry.get("side"):
                    used_sides = {p.get("side") for p in peers}
                    entry["side"] = "right" if "right" not in used_sides else ("left" if "left" not in used_sides else "right")
                entry.setdefault("send", True)
                entry.setdefault("allow_drive", True)
                entry.setdefault("in_use", True)
                entry["connected"] = True
                peers.append(entry)
                self._settings["peers"] = peers
                self._save_settings(self._settings)
                if entry.get("id"):
                    self._owner.link_up(entry["id"], accepts=entry.get("allow_drive", True))
                self._last_event_summary = f"Paired with {entry.get('name', 'Machine')} ({entry.get('host', '')})"
        self._sync_outbound_links()
        if self._responder is not None:
            try:
                self._responder.peers_changed()
            except Exception:
                pass

    def _on_paired_callback(self, entry: dict[str, Any]) -> None:
        with self._lock:
            self._active_pairing_code = ""
            self._active_qr_payload = ""
            self._last_event_summary = f"Paired with {entry.get('name', 'Machine')} ({entry.get('host', '')})"
        self._sync_outbound_links()

    def _start_network_services(self) -> None:
        try:
            self._pairing = pairing.PairingService(
                name=(self._settings.get("name") or self._device_name)[:48],
                identity=self._identity_Bytes(),
                platform="android",
                port_getter=lambda: int(self._settings.get("port", protocol.DEFAULT_PORT)),
                peers=self._peer_book.peers,
                store=self._store_paired_entry,
                on_paired=self._on_paired_callback,
            )
            self._pairing.active_discovery = True
            self._pairing.start()
        except Exception:
            self._pairing = None

        try:
            if self._settings.get("responder_enabled", True):
                self._responder = receiver.LinkResponder(
                    self._peer_book,
                    self._identity_dict,
                    zones=self._zones_for_responder,
                    status_callback=self._on_responder_status,
                    owner_callback=self._on_responder_owner,
                    arrival_callback=self._on_responder_arrival,
                    injector=self._injector_adapter,
                    desktop=self._desktop_adapter,
                    clipboard=self._clipboard_adapter,
                    away=lambda: bool(self._owner.away),
                )
                self._responder.input_scale = self._input_scale
                self._responder.start(int(self._settings.get("port", protocol.DEFAULT_PORT)))
        except Exception:
            self._responder = None

        self._sync_outbound_links()

    def _on_responder_status(self, state: Any, detail: str) -> None:
        with self._lock:
            if detail:
                self._last_event_summary = detail

    def _sync_outbound_links(self) -> None:
        if not self._enable_network:
            return
        with self._lock:
            peers = list(self._settings.get("peers", []))
            wanted_tokens: set[str] = set()
            for peer in peers:
                token = peer.get("token", "")
                if peer.get("send", True) and peer.get("host") and protocol.is_paired_token(token):
                    wanted_tokens.add(token)
                    if token not in self._outbound_links:
                        try:
                            ob = core_link.OutboundLink(
                                token,
                                self._peer_book,
                                self._identity_dict,
                                state=self._on_outbound_state,
                                up=self._on_outbound_up,
                                message=self._on_outbound_message,
                            )
                            self._outbound_links[token] = ob
                            ob.start()
                        except Exception:
                            pass
            for tok in list(self._outbound_links.keys()):
                if tok not in wanted_tokens:
                    old = self._outbound_links.pop(tok, None)
                    if old is not None:
                        try:
                            old.stop(wait=False)
                        except Exception:
                            pass

    def _on_outbound_state(self, link_obj: Any, up: bool, text: str) -> None:
        with self._lock:
            peer_id = getattr(link_obj, "peer_id", "")
            for p in self._settings.get("peers", []):
                if p.get("token") == link_obj.token or (peer_id and p.get("id") == peer_id):
                    p["connected"] = bool(up)
            if not up and peer_id:
                actions = self._owner.link_down(peer_id)
                self._process_owner_actions(actions)

    def _on_outbound_up(self, link_obj: Any, fields: dict[str, Any]) -> None:
        with self._lock:
            peer_id = getattr(link_obj, "peer_id", "")
            if peer_id:
                actions = self._owner.link_up(peer_id, accepts=bool(fields.get("accepts", True)))
                self._process_owner_actions(actions)
                self._last_event_summary = f"Connected to {getattr(link_obj, 'peer_name', peer_id)}"

    def _on_outbound_message(self, link_obj: Any, msg: dict[str, Any]) -> None:
        with self._lock:
            peer_id = getattr(link_obj, "peer_id", "")
            if not peer_id or not isinstance(msg, dict):
                return
            mtype = msg.get("type")
            data = msg.get("data") or {}
            actions: list[Any] = []
            if mtype == protocol.MSG_ACCEPT:
                actions = self._owner.accept(peer_id, data)
            elif mtype == protocol.MSG_REFUSE:
                actions = self._owner.refuse(peer_id, data)
            elif mtype == protocol.MSG_SWITCH:
                actions = self._owner.switch(peer_id, data)
            elif mtype == protocol.MSG_CLIPBOARD:
                actions = self._owner.clipboard(peer_id, data)
            elif mtype == protocol.MSG_ACCEPTS:
                actions = self._owner.accepts(peer_id, bool(data.get("accepts", True)))
            if actions:
                self._process_owner_actions(actions)

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
                    data.setdefault("ui_lang", "ar")
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
            "ui_lang": "ar",
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

    def _outbound_for_peer(self, peer_id: str) -> core_link.OutboundLink | None:
        peer = self._peer_by_id(peer_id)
        if peer is None:
            return None
        tok = peer.get("token", "")
        return self._outbound_links.get(tok)

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
                ob = self._outbound_for_peer(act.peer)
                if ob is not None and ob.live():
                    mtype = act.message.get("type")
                    if mtype in protocol.INPUT_TYPES:
                        ob.send_input(act.message)
                    else:
                        ob.post(act.message)
                dispatched += 1
            elif isinstance(act, owner.SendClipboard):
                if self._local_clipboard_text:
                    encoded = self._local_clipboard_text.encode("utf-8")
                    if len(encoded) <= protocol.CLIPBOARD_MAX_BYTES:
                        clip_msg = {
                            "type": "clipboard",
                            "data": {"text": self._local_clipboard_text},
                        }
                        self._outbound_wire_log.append({
                            "peer": act.peer,
                            "message": clip_msg,
                            "at": time.monotonic(),
                        })
                        ob = self._outbound_for_peer(act.peer)
                        if ob is not None and ob.live():
                            ob.post(clip_msg)
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

            nearby_info: dict[str, Any] = {
                "state": "inactive",
                "seconds_left": 0,
                "sas": "",
                "attempt": "",
                "peer_name": "",
                "peer_address": "",
                "candidates": [],
            }
            discovered_list: list[dict[str, Any]] = []
            local_ip = ""
            try:
                local_ip = pairing.pairing_address() or ""
            except Exception:
                local_ip = ""

            if self._pairing is not None:
                try:
                    if self._pairing.code:
                        self._active_pairing_code = self._pairing.code
                        self._active_qr_payload = self._pairing.qr_text() or self._active_qr_payload
                    raw_nb = self._pairing.nearby_status
                    peer_dict = raw_nb.get("peer") or {}
                    cands = []
                    for c in raw_nb.get("peers") or []:
                        cands.append({
                            "name": str(c.get("name", "Machine")),
                            "address": str(c.get("address", "")),
                            "platform": str(c.get("platform", "desktop")),
                        })
                    nearby_info = {
                        "state": str(raw_nb.get("state", "inactive")),
                        "seconds_left": int(raw_nb.get("seconds_left", 0)),
                        "sas": str(raw_nb.get("sas") or ""),
                        "attempt": str(raw_nb.get("attempt") or ""),
                        "peer_name": str(peer_dict.get("name", "")),
                        "peer_address": str(peer_dict.get("address", "")),
                        "candidates": cands,
                    }
                    paired_ids = {p.get("id") for p in self._settings.get("peers", [])}
                    for m in self._pairing.machines():
                        if m.get("id") not in paired_ids:
                            discovered_list.append({
                                "name": str(m.get("name", "Machine")),
                                "address": str(m.get("address", "")),
                                "port": int(m.get("port", protocol.DEFAULT_PORT)),
                                "platform": str(m.get("platform") or "desktop"),
                                "has_code": bool(m.get("pair_id")),
                                "nearby_active": bool(m.get("nearby")),
                            })
                except Exception:
                    pass
            elif self._fallback_nearby is not None:
                nearby_info = {
                    "state": str(self._fallback_nearby.state),
                    "seconds_left": int(self._fallback_nearby.seconds_left),
                    "sas": str(self._fallback_nearby.sas or ""),
                    "attempt": str(self._fallback_nearby.attempt or ""),
                    "peer_name": "",
                    "peer_address": "",
                    "candidates": [],
                }

            cur_x, cur_y = self._desktop_adapter.cursor_position()
            responder_driven = bool(
                self._responder_owner_id
                or (self._responder is not None and self._responder.owner is not None)
            )
            state = {
                "wire_version": protocol.LINK_VERSION,
                "default_port": protocol.DEFAULT_PORT,
                "pairing_port": protocol.PAIRING_PORT,
                "machine_id": self._settings["machine_id"],
                "name": self._settings.get("name", self._device_name),
                "platform": "android",
                "local_ip": local_ip,
                "ui_lang": self._settings.get("ui_lang", "ar"),
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
                "responder_driven": responder_driven,
                "responder_owner": self._responder_owner_id,
                "screen_width": self._desktop_adapter.width,
                "screen_height": self._desktop_adapter.height,
                "cursor_x": cur_x,
                "cursor_y": cur_y,
                "edge_pressure": round(self._edge_pressure, 3),
                "active_edge": self._active_edge,
                "pairing_code": self._active_pairing_code,
                "qr_payload": self._active_qr_payload,
                "nearby": nearby_info,
                "discovered": discovered_list,
                "last_event": self._last_event_summary,
                "peers": self._settings.get("peers", []),
                "available_styles": style_list,
                "pending_remote_events": len(self._remote_event_queue),
                "outbound_frames_sent": len(self._outbound_wire_log),
                "token_status": self._token_manager.status(),
            }
            return json.dumps(state)

    def set_language(self, lang: str) -> str:
        with self._lock:
            norm = "ar" if str(lang).lower().strip() == "ar" else "en"
            self._settings["ui_lang"] = norm
            self._save_settings(self._settings)
            self._last_event_summary = "تم التبديل إلى العربية" if norm == "ar" else "Switched language to English"
            return self.get_state_json()

    def toggle_language(self) -> str:
        with self._lock:
            current = self._settings.get("ui_lang", "ar")
            return self.set_language("en" if current == "ar" else "ar")

    def begin_nearby(self) -> str:
        """Starts 'Press Nearby' (EasyPairWindow SAS v1) discovery & pairing over UDP 24821."""
        with self._lock:
            if self._pairing is not None:
                try:
                    self._pairing.begin_nearby()
                    self._active_pairing_code = ""
                    self._last_event_summary = "Press Nearby active (60s): press Nearby on the other device"
                    return self.get_state_json()
                except Exception as exc:
                    self._last_event_summary = f"Press Nearby error: {exc}"
                    return self.get_state_json()
            from core.easy_pair import EasyPairWindow
            self._fallback_nearby = EasyPairWindow(
                (self._settings.get("name") or self._device_name)[:48],
                self._identity_Bytes(),
                "android",
                int(self._settings.get("port", protocol.DEFAULT_PORT)),
            )
            self._active_pairing_code = ""
            self._last_event_summary = "Press Nearby active (60s): press Nearby on the other device"
            return self.get_state_json()

    def cancel_nearby(self) -> str:
        with self._lock:
            if self._pairing is not None:
                try:
                    self._pairing.cancel_nearby()
                except Exception:
                    pass
            if self._fallback_nearby is not None:
                self._fallback_nearby.cancel()
            self._last_event_summary = "Press Nearby cancelled"
            return self.get_state_json()

    def choose_nearby(self, address: str) -> str:
        with self._lock:
            if self._pairing is not None:
                try:
                    status = self._pairing.nearby_status
                    for cand in status.get("peers") or []:
                        if cand.get("address") == address:
                            self._pairing.choose_nearby(cand)
                            self._last_event_summary = f"Selected nearby device {cand.get('name', address)}"
                            break
                except Exception as exc:
                    self._last_event_summary = f"Choose nearby error: {exc}"
            return self.get_state_json()

    def confirm_nearby(self) -> str:
        with self._lock:
            if self._pairing is not None:
                try:
                    status = self._pairing.nearby_status
                    sas = status.get("sas")
                    attempt = status.get("attempt")
                    if sas and attempt:
                        self._pairing.confirm_nearby(sas, attempt)
                        self._last_event_summary = f"Approved SAS {sas} — waiting for peer confirmation"
                except Exception as exc:
                    self._last_event_summary = f"Confirm nearby error: {exc}"
            return self.get_state_json()

    def tick_runtime(self) -> str:
        """Periodic heartbeat called by BeamerLinkService to advance Owner timers & drain queues."""
        with self._lock:
            actions = self._owner.tick()
            if actions:
                self._process_owner_actions(actions)
            return self.get_state_json()

    def generate_pairing_code(self) -> str:
        with self._lock:
            if self._pairing is not None:
                try:
                    code = self._pairing.begin_pairing()
                    self._active_pairing_code = code
                    self._active_qr_payload = self._pairing.qr_text() or (
                        f"beamer://pair?v=3&id={self._settings['machine_id']}"
                        f"&port={protocol.PAIRING_PORT}&code={code}&qr={protocol.id_text(os.urandom(16))}"
                    )
                    self._last_event_summary = (
                        f"CPace pairing active on TCP/UDP {protocol.PAIRING_PORT} "
                        f"(Code: {self._active_pairing_code})"
                    )
                    return self.get_state_json()
                except Exception:
                    pass
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

    def pair_by_ip(self, host: str, code: str, port: int = protocol.PAIRING_PORT) -> str:
        """Initiates real CPace v3 pairing over TCP 24821 with a machine showing a 6-digit code."""
        clean_host = (host or "").strip()
        clean_code = "".join(ch for ch in (code or "") if ch.isdigit())
        if len(clean_code) != 6 or not clean_host:
            with self._lock:
                self._last_event_summary = "Enter a valid IPv4 address and 6-digit code"
                return self.get_state_json()

        def _worker() -> None:
            try:
                if self._pairing is not None:
                    # First check if machine was discovered via UDP beacon
                    matched = next(
                        (m for m in self._pairing.machines() if m.get("address") == clean_host and m.get("pair_id")),
                        None,
                    )
                    if matched is not None:
                        entry = self._pairing.pair(matched, clean_code)
                    else:
                        entry = self._pairing.pair_by_address(clean_host, clean_code, int(port))
                    with self._lock:
                        self._last_event_summary = f"Successfully paired with {entry.get('name', clean_host)}!"
            except pairing.AlreadyPaired as ap:
                with self._lock:
                    name = (ap.entry or {}).get("name", clean_host)
                    self._last_event_summary = f"Already paired with {name}"
            except Exception as exc:
                with self._lock:
                    self._last_event_summary = f"Pairing with {clean_host} failed: {exc}"

        threading.Thread(target=_worker, name="Beamer-pair-by-ip", daemon=True).start()
        with self._lock:
            self._last_event_summary = f"Connecting to {clean_host}:{port} with code {clean_code}…"
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
            ob = self._outbound_for_peer(peer_id)
            if (ob is None or not ob.live()) and self._owner._wait is not None:
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
            if self._responder is not None:
                try:
                    self._responder.send_home()
                except Exception:
                    pass
            self._responder_owner_id = ""
            self._edge_pressure = 0.0
            self._enqueue_remote({"kind": "cursor_hide"})
            self._last_event_summary = "Returned input home / Released PC cursor"
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
            self._clipboard_adapter.bump()
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
                self._injector_adapter.inject_mouse_move(dx, dy)
            elif msg_type in ("mousedown", "mouseup"):
                self._injector_adapter.inject_mouse_button(
                    str(data.get("button", "left")),
                    msg_type == "mousedown",
                )
            elif msg_type == "wheel":
                dy, dx = self._input_scale.wheel(float(data.get("dy", 0.0)), float(data.get("dx", 0.0)))
                self._injector_adapter.inject_scroll(dy, dx, str(data.get("mode", "pixel")))
            elif msg_type in ("keydown", "keyup"):
                self._injector_adapter.inject_key(
                    str(data.get("key", "")),
                    msg_type == "keydown",
                    data.get("us"),
                )
            elif msg_type == "text":
                self._injector_adapter.inject_text(str(data.get("text", "")))
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
            if self._responder is not None:
                self._responder.input_scale = self._input_scale
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
        self._sync_outbound_links()
        return self.get_state_json()


    def stop(self) -> None:
        with self._lock:
            pairing_svc, self._pairing = self._pairing, None
            responder_svc, self._responder = self._responder, None
            links = list(self._outbound_links.values())
            self._outbound_links.clear()
        try:
            self._token_manager.stop_background_refresh()
        except Exception:
            pass
        for ob in links:
            try:
                ob.stop(wait=False)
            except Exception:
                pass
        if pairing_svc is not None:
            try:
                pairing_svc.stop()
            except Exception:
                pass
        if responder_svc is not None:
            try:
                responder_svc.stop()
            except Exception:
                pass


_BRIDGE_INSTANCE: BeamerAndroidBridge | None = None


def init_bridge(files_dir: str, device_name: str, enable_network: bool = True) -> str:
    global _BRIDGE_INSTANCE
    if _BRIDGE_INSTANCE is None:
        _BRIDGE_INSTANCE = BeamerAndroidBridge(files_dir, device_name, enable_network=enable_network)
    return _BRIDGE_INSTANCE.get_state_json()


def get_bridge() -> BeamerAndroidBridge:
    if _BRIDGE_INSTANCE is None:
        raise RuntimeError("BeamerAndroidBridge not initialized")
    return _BRIDGE_INSTANCE
