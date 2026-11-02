"""One row of the Overview's list of machines: its name, where it is, what its link is doing.

Pure, so every state a row can be in is tested against a word. The faults take their sentence from
the link itself (core/link.py words them, naming the machine), so a version 5 machine that closes
the connection reads "Update Beamer on <name> to 1.5.0..." here and nowhere else is it reworded.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import protocol
from core import peerlist
from core.i18n import N_, tr
from link_state import AMBER, FAULT, INK, SIGNAL, LinkState

PLATFORMS = {"windows": N_("Windows"), "macos": N_("Mac"), "linux": N_("Linux"), "ios": N_("iPhone"), "android": N_("Android")}
TOKEN_KINDS = ("unauthenticated", "wrong_id", "different", "unreadable", "forgotten")
VERSION_KINDS = ("older", "newer", "not_beamer")


@dataclass(frozen=True)
class Row:
    token: str
    label: str
    platform: str
    address: str
    state: LinkState
    in_use: bool
    left_out: bool
    drives: bool
    driven: bool
    phone: bool = False


def platform_name(platform: str) -> str:
    return tr(PLATFORMS.get(platform, N_("Other")))


def row(entry: dict, label: str, live: bool, kind: str, status: str, here: bool, driving: bool, inbound: bool,
        has_link: bool) -> Row:
    """`live`, `kind` and `status` are the outbound link's, and `has_link` whether this Mac dials
    the machine at all; `inbound` is whether the machine has a link up to this Mac; `here` and
    `driving` are where input is."""
    host, port = entry.get("host"), entry.get("port")
    address = tr("{address} · port {port}").format(address=host, port=port) if host and port else tr("No address")
    return Row(
        entry["token"], label, platform_name(entry.get("platform", "")), address,
        _state(entry, label, live, kind, status, here, driving, inbound, has_link), entry.get("in_use", True) is True,
        entry.get("left_out") is True,
        entry.get("send") is True, entry.get("allow_drive") is True, port == 0,
    )


def _state(entry, label, live, kind, status, here, driving, inbound, has_link) -> LinkState:
    reason = peerlist.dormant_reason(entry)
    if reason:
        return LinkState("dormant", AMBER, N_("Needs re-pair"), N_("Needs re-pair"), reason, AMBER)
    if entry.get("in_use", True) is not True:
        return LinkState("not_in_use", AMBER, N_("NOT IN USE"), N_("NOT IN USE"), tr("{name} is not in use on this Mac.").format(name=label), AMBER)
    if not protocol.linkable(entry):
        return LinkState(
            "pair_again", AMBER, N_("Pair again"), N_("Pair again"),
            tr("{name} was paired on Beamer 1.4. Pair the two again to link them on 1.5.0.").format(name=label), AMBER,
        )
    if driving:
        return LinkState(
            "driving", SIGNAL, N_("Driving this Mac"), N_("Live"),
            tr("{name}'s keyboard and pointer are on this Mac. Push the pointer back through the edge it arrived by to send them home.").format(name=label),
            SIGNAL,
        )
    if here:
        return LinkState(
            "here", SIGNAL, N_("Input here"), N_("Live"),
            tr("This Mac's keyboard and pointer are on {name}. Do the same again to bring them home.").format(name=label), SIGNAL,
        )
    if live or inbound:
        return LinkState("linked", INK, N_("Linked"), N_("Linked"), tr("Connected to {name}.").format(name=label), SIGNAL)
    if not has_link:
        return LinkState("waiting", AMBER, N_("Waiting"), N_("Waiting"), tr("Waiting for {name} to connect.").format(name=label), AMBER)
    if kind in TOKEN_KINDS:
        return LinkState("token", FAULT, N_("Refused"), N_("Refused"), status, FAULT)
    if kind in VERSION_KINDS or (kind == "closed" and not entry.get("linked")):
        return LinkState("version", FAULT, N_("Version"), N_("Refused"), status, FAULT)
    if kind in ("unreachable", "timeout"):
        return LinkState("unreachable", FAULT, N_("Unreachable"), N_("No reply"), status, FAULT)
    if kind == "refused":
        return LinkState("not_listening", FAULT, N_("Not listening"), N_("No receiver"), status, FAULT)
    if kind == "blocked":
        return LinkState("blocked", FAULT, N_("Blocked"), N_("Tunnel"), status, FAULT)
    if kind in ("stopped", "closed", "failed") and status:
        return LinkState("dropped", FAULT, N_("Dropped"), N_("Dropped"), tr("{status}. Beamer keeps trying to reconnect.").format(status=status.rstrip(".")), FAULT)
    return LinkState("waiting", AMBER, N_("Waiting"), N_("Looking"), tr("Looking for {name}. It connects on its own once Beamer is open there.").format(name=label), AMBER, True)
