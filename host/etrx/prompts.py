"""Parsers for the prompts of the Telegesis R309 AT command set."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Ok:
    pass


@dataclass(frozen=True)
class Error:
    code: int


@dataclass(frozen=True)
class Seq:
    seq: int


@dataclass(frozen=True)
class Ack:
    seq: int


@dataclass(frozen=True)
class Nack:
    seq: int


@dataclass(frozen=True)
class Jpan:
    channel: int
    pan: int
    epid: str


@dataclass(frozen=True)
class LeftPan:
    pass


@dataclass(frozen=True)
class NewNode:
    nwk: int
    eui: str
    parent: int


@dataclass(frozen=True)
class Announce:
    kind: str  # FFD, ZED, SED, MED or COO
    eui: str
    nwk: int
    rssi: int | None = None
    lqi: int | None = None


@dataclass(frozen=True)
class NodeLeft:
    nwk: int
    eui: str


@dataclass(frozen=True)
class Text:
    kind: str  # UCAST, BCAST or MCAST
    eui: str | None
    data: bytes
    rssi: int | None = None
    lqi: int | None = None


@dataclass(frozen=True)
class Rx:
    eui: str | None
    nwk: int
    profile: int
    dst_ep: int
    src_ep: int
    cluster: int
    payload: bytes
    rssi: int | None = None
    lqi: int | None = None


_H2 = r"([0-9A-F]{2})"
_H4 = r"([0-9A-F]{4})"
_EUI = r"([0-9A-F]{16})"

_SIMPLE = [
    (re.compile(r"^OK$"), lambda m: Ok()),
    (re.compile(rf"^ERROR:{_H2}$"), lambda m: Error(int(m[1], 16))),
    (re.compile(rf"^SEQ:{_H2}$"), lambda m: Seq(int(m[1], 16))),
    (re.compile(rf"^ACK:{_H2}(?:,.*)?$"), lambda m: Ack(int(m[1], 16))),
    (re.compile(rf"^NACK:{_H2}(?:,.*)?$"), lambda m: Nack(int(m[1], 16))),
    (re.compile(rf"^JPAN:(\d+),{_H4},{_EUI}$"), lambda m: Jpan(int(m[1]), int(m[2], 16), m[3])),
    (re.compile(r"^LeftPAN$"), lambda m: LeftPan()),
    (re.compile(rf"^NEWNODE: ?{_H4},{_EUI},{_H4}$"),
     lambda m: NewNode(int(m[1], 16), m[2], int(m[3], 16))),
    (re.compile(rf"^NODELEFT: ?{_H4},{_EUI}$"), lambda m: NodeLeft(int(m[1], 16), m[2])),
    (re.compile(rf"^(FFD|ZED|SED|MED|COO):{_EUI},{_H4}(?:,(-?\d+),(\d+))?$"),
     lambda m: Announce(m[1], m[2], int(m[3], 16), _opt_int(m[4]), _opt_int(m[5]))),
]

_TEXT_HEAD = re.compile(rf"^(UCAST|BCAST|MCAST):(?:{_EUI},)?{_H2}=")
_RX_HEAD = re.compile(rf"^RX:(?:{_EUI},)?{_H4},{_H4},{_H2},{_H2},{_H4},{_H2}:")
_TAIL = re.compile(r"^(?:,(-?\d+),(\d+))?$")


def _opt_int(s: str | None) -> int | None:
    return None if s is None else int(s)


def _tail(rest: str) -> tuple[int | None, int | None] | None:
    m = _TAIL.match(rest)
    if m is None:
        return None
    return _opt_int(m[1]), _opt_int(m[2])


def _parse_text(line: str) -> Text | None:
    m = _TEXT_HEAD.match(line)
    if m is None:
        return None
    n = int(m[3], 16)
    rest = line[m.end():]
    if len(rest) < n:
        return None
    tail = _tail(rest[n:])
    if tail is None:
        return None
    return Text(m[1], m[2], rest[:n].encode("latin-1"), *tail)


def _parse_rx(line: str, rx_hex: bool) -> Rx | None:
    m = _RX_HEAD.match(line)
    if m is None:
        return None
    n = int(m[7], 16)
    rest = line[m.end():]
    width = 2 * n if rx_hex else n
    if len(rest) < width:
        return None
    tail = _tail(rest[width:])
    if tail is None:
        return None
    body = rest[:width]
    try:
        payload = bytes.fromhex(body) if rx_hex else body.encode("latin-1")
    except ValueError:
        return None
    return Rx(m[1], int(m[2], 16), int(m[3], 16), int(m[4], 16), int(m[5], 16), int(m[6], 16),
              payload, *tail)


def parse_prompt(line: str, rx_hex: bool = True):
    """Return the prompt object for one line, or None if the line is not a known prompt."""
    for regex, build in _SIMPLE:
        m = regex.match(line)
        if m:
            return build(m)
    if line.startswith("RX:"):
        return _parse_rx(line, rx_hex)
    return _parse_text(line)
