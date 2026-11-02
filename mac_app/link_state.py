"""What the status module says about the link: a tone, a state word, the LED tag and one sentence.

Pure, so every status the controller can report is tested against a word. The controller's
booleans decide the broad state; its status string only picks the flavour of a fault, and a string
nobody has seen before still lands on a word with the controller's own sentence as the detail.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.i18n import matches_translation, tr

SIGNAL = "signal"
AMBER = "amber"
FAULT = "fault"
INK = "ink"

@dataclass(frozen=True)
class LinkState:
    key: str
    tone: str
    word: str
    tag: str
    detail: str
    led: str
    blink: bool = False


def peer_name(cfg) -> str:
    return cfg.pc_name or cfg.host or tr("the other machine")


def describe(controller) -> LinkState:
    """The controller may name each machine (`peer_label` the first, `on_label` the one input is
    on, `driver_label` the one driving this Mac), which tells two of one name apart; without, the
    first machine's saved name stands in."""
    cfg = controller.cfg
    peer = getattr(controller, "peer_label", None) or peer_name(cfg)
    on = getattr(controller, "on_label", None) or peer
    driver = getattr(controller, "driver_label", None) or peer
    status = controller.connection_status or ""
    tunnel = bool(getattr(controller, "via_tunnel", False))
    kind = getattr(controller, "status_kind", "none")
    if getattr(controller, "receiving", False):
        return LinkState(
            "receiving", SIGNAL, tr("From {name}").format(name=driver), tr("Live"),
            tr("{name}'s keyboard and pointer are on this Mac. Push its pointer back through the edge it arrived by to send them home.").format(name=driver),
            SIGNAL,
        )
    if controller.waking:
        return LinkState("waking", AMBER, tr("Waking"), tr("Waking"), tr("Waking {name}. It connects on its own once it is up.").format(name=peer), AMBER, True)
    if controller.connected:
        linked = tr("Tunnel") if tunnel else tr("Linked")
        if controller.windows_locked:
            return LinkState(
                "unlocking", AMBER, tr("Unlocking"), tr("Unlocking"),
                tr("{name} is at its lock screen and is unlocking itself; input follows once the desktop is back.").format(name=peer),
                AMBER, True,
            )
        if controller.redirecting:
            return LinkState(
                "windows", SIGNAL, tr("On {name}").format(name=on), tr("Live"),
                tr("Keyboard and pointer are on {name}. Do the same again to bring them home.").format(name=on), SIGNAL,
            )
        armed = controller.crossing.armed
        if armed and controller.crossing_paused:
            return LinkState(
                "paused", INK, tr("Paused"), tr("Held"),
                tr("Connected to {name}. Crossing is held, so you can lean on an edge.").format(name=peer), SIGNAL,
            )
        if armed and controller.full_screen_app is not None:
            return LinkState(
                "full_screen", INK, tr("Held"), tr("Held"),
                tr("Held: an app is full screen."), SIGNAL,
            )
        detail = tr("Connected to {name} through the macOS 27 tunnel.").format(name=peer) if tunnel else tr("Connected to {name}.").format(name=peer)
        return LinkState("mac", INK, tr("On Mac"), linked, detail, SIGNAL)
    if not (cfg.host and cfg.auth_token):
        return LinkState("unpaired", AMBER, tr("Not paired"), tr("Setup"), tr("Pair a machine on Overview and Beamer connects on its own."), AMBER)
    # Translators: a machine's address and port, as in "192.0.2.20 port 24820".
    address = tr("{host} port {port}").format(host=cfg.host, port=cfg.port)
    if kind == "forgotten":
        return LinkState("forgotten", FAULT, tr("Pairing removed"), tr("Refused"), tr("{name} no longer has this pairing: remove {name} here and pair the two again.").format(name=peer), FAULT)
    if kind in ("unauthenticated", "wrong_id", "different", "unreadable"):
        return LinkState("token", FAULT, tr("Token mismatch"), tr("Refused"), tr("{name} refused this Mac's token. Remove it, then pair again to write a fresh one.").format(name=peer), FAULT)
    if kind in ("older", "newer", "not_beamer"):
        return LinkState("version", FAULT, tr("Version mismatch"), tr("Refused"), tr("{name} runs a different Beamer from this Mac. Update both to the same version." ).format(name=peer), FAULT)
    if matches_translation(status, "{name} did not wake"):
        return LinkState("not_woken", FAULT, tr("Did not wake"), tr("No reply"), tr("{name} did not answer within a minute of the wake-up packet.").format(name=peer), FAULT)
    if kind == "blocked":
        return LinkState("blocked", FAULT, tr("Blocked"), tr("Tunnel"), tr("macOS 27 blocked direct LAN access. Open Beamer Tunnel from Applications."), FAULT)
    if kind in ("unreachable", "timeout"):
        return LinkState("unreachable", FAULT, tr("Unreachable"), tr("No reply"), tr("{address} is not answering. {name} may be asleep or off this network.").format(address=address, name=peer), FAULT)
    if kind == "refused":
        return LinkState("not_listening", FAULT, tr("Not listening"), tr("No receiver"), tr("{name} answers, but Beamer is not listening on port {port}. Open Beamer on {name}.").format(name=peer, port=cfg.port), FAULT)
    if kind in ("stopped", "closed"):
        return LinkState("stopped", FAULT, tr("No reply"), tr("Dropped"), tr("{name} stopped responding. Beamer keeps trying to reconnect.").format(name=peer), FAULT)
    if matches_translation(status, "Connecting to {host}:{port}", "Waiting to connect", "Settings saved; reconnecting"):
        return LinkState("waiting", AMBER, tr("Waiting"), tr("Looking"), tr("Looking for {name}. It connects on its own once Beamer is open there.").format(name=peer), AMBER, True)
    detail = tr("{status}. Beamer keeps trying to reconnect.").format(status=status.rstrip(".")) if status else tr("Beamer keeps trying to reconnect.")
    return LinkState("dropped", FAULT, tr("Disconnected"), tr("Dropped"), detail, FAULT)
