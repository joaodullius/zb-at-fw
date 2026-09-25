"""Console output and the small command loop shared by the role scripts."""
from __future__ import annotations

import sys
from typing import Callable

from .prompts import Ack, Announce, Jpan, LeftPan, Nack, NewNode, NodeLeft, Rx, Text
from .protocol import EtrxError


def describe(ev) -> str | None:
    """One readable line for a prompt, or None for prompts not worth showing."""
    if isinstance(ev, Text):
        return f"{ev.kind.lower()} from {ev.eui or '?'}: {ev.data.decode('utf-8', 'replace')}"
    if isinstance(ev, NewNode):
        return f"new node {ev.eui} ({ev.nwk:04X})"
    if isinstance(ev, Announce):
        return f"{ev.kind} announced: {ev.eui} ({ev.nwk:04X})"
    if isinstance(ev, NodeLeft):
        return f"node left: {ev.eui} ({ev.nwk:04X})"
    if isinstance(ev, LeftPan):
        return "left the network"
    if isinstance(ev, Jpan):
        return f"in network: channel {ev.channel}, PAN {ev.pan:04X}, EPID {ev.epid}"
    if isinstance(ev, Nack):
        return f"message {ev.seq:02X} was not acknowledged"
    if isinstance(ev, Rx):
        return (f"RX from {ev.nwk:04X}: profile {ev.profile:04X} cluster {ev.cluster:04X} "
                f"ep {ev.src_ep}->{ev.dst_ep}: {ev.payload.hex()}")
    if isinstance(ev, Ack):
        return None
    return None


def split_first(rest: str) -> tuple[str, str]:
    """Split '<addr> <text>'."""
    addr, sep, text = rest.partition(" ")
    if not sep or not addr:
        raise ValueError("expected: <address> <text>")
    return addr, text


def repl(commands: dict[str, Callable[[str], object]], stream=None, out=print):
    """Read '<command> [rest]' lines until 'quit' or end of input."""
    stream = stream or sys.stdin
    out("commands: " + " | ".join([*commands, "quit"]))
    for raw in stream:
        line = raw.strip()
        if not line:
            continue
        name, _, rest = line.partition(" ")
        if name == "quit":
            return
        fn = commands.get(name)
        if fn is None:
            out(f"unknown command {name!r}")
            continue
        try:
            result = fn(rest)
        except (EtrxError, TimeoutError, ValueError) as exc:
            out(f"error: {exc}")
            continue
        if result is not None:
            out(str(result))
