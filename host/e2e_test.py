#!/usr/bin/env python3
"""End-to-end test of a three-node Zigbee network on three DKs running the AT firmware.

What it does
------------
It builds a network with the three role classes from coordinator.py, bulb.py and
switch.py (so it runs exactly the AT command sequences those scripts use, see their
documentation) and checks what the modules report. It contains no role logic itself.
One PASS/FAIL line is printed per step; the exit code is non-zero if any step failed.

Steps and the AT commands behind them
-------------------------------------
 1. Same firmware on all DKs   AT&F, ATI on each DK (revisions must match)
 2. Coordinator forms network  coordinator.py start: ATS00/ATS02/ATS03, ATS0F=1904, AT+EN
 3. Bulb joins as router       bulb.py start: ATS00/ATS03, ATS0AA, ATS0AF=0, AT+JN, EP2 setup
                               the coordinator must print NEWNODE for the bulb
 4. Switch joins as end device switch.py start: same with ATS0AF=1 (end device)
 5. ZCL On/Off                 switch: AT+SENDUCASTB (On, Off, Toggle) -> bulb answers
                               with a Default Response (status 00) and follows each command
 6. Text unicasts              AT+UCAST:<addr>=<text> in five directions, each with ACK
 7. Broadcast                  coordinator: AT+BCAST:00,<text>; the bulb (router) must get
                               BCAST:, the switch (end device) must not: broadcasts to
                               0xFFFC do not reach end devices (Zigbee specification)
 8. Rejoin after reset         ATZ on bulb and switch; both print JPAN for the same network
                               and ZCL control still works
 9. Stay joined                no LeftPAN / NODELEFT during --soak seconds

Network parameters
------------------
By default the coordinator picks the channel, a random PAN ID and uses its own EUI64 as
extended PAN ID; the bulb and the switch then join that network (they are given its
channel and EPID). Use --channel/--pan/--epid to form a fixed network instead.
--legacy-tc (default on) matches the coordinator of this firmware, which does not hand
out Zigbee 3.0 TC link keys: the joiners set S0A bit A.

Examples
--------
    python e2e_test.py --coord COM31 --bulb COM36 --switch COM7
    python e2e_test.py --coord COM31 --bulb COM36 --switch COM7 --channel 20 --pan 7A31 --epid 00000000000A1B2C --soak 300 -v

The serial ports are the DKs' VCOM ports (nrfutil device list). -v prints the AT traffic.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Callable

from bulb import Bulb
from coordinator import Coordinator
from etrx import Ack, Etrx, Jpan, LeftPan, NetworkOptions, NewNode, NodeLeft, Text
from etrx.net import parse_epid, parse_pan
from switch import Switch


class Abort(Exception):
    """A step failed that the following steps depend on."""


class Steps:
    def __init__(self):
        self.failed = 0

    def run(self, name: str, fn: Callable[[], str | None], critical: bool = False):
        start = time.monotonic()
        try:
            detail = fn()
        except Exception as exc:
            self.failed += 1
            print(f"FAIL  {name}: {type(exc).__name__}: {exc}", flush=True)
            if critical:
                raise Abort(name) from exc
            return
        suffix = f" - {detail}" if detail else ""
        print(f"PASS  {name} ({time.monotonic() - start:.1f} s){suffix}", flush=True)


def check(condition: bool, message: str):
    if not condition:
        raise AssertionError(message)


def is_text(data: str, kind: str | None = None):
    return lambda ev: isinstance(ev, Text) and ev.data == data.encode() and kind in (None, ev.kind)


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--coord", required=True, help="serial port of the coordinator DK")
    p.add_argument("--bulb", required=True, help="serial port of the bulb DK")
    p.add_argument("--switch", required=True, help="serial port of the switch DK")
    p.add_argument("--channel", type=int, choices=range(11, 27), metavar="11-26",
                   help="form on this channel only (default: any)")
    p.add_argument("--pan", type=parse_pan, help="PAN ID for formation (default: random)")
    p.add_argument("--epid", type=parse_epid, help="extended PAN ID (default: random)")
    p.add_argument("--legacy-tc", action=argparse.BooleanOptionalAction, default=True,
                   help="the coordinator is a pre-Zigbee 3.0 Trust Centre, so the bulb and the "
                        "switch skip the TC link key request (default: yes)")
    p.add_argument("--soak", type=float, default=60.0, metavar="SECONDS",
                   help="how long everyone must stay in the network (default: 60)")
    p.add_argument("-v", "--verbose", action="store_true", help="show the AT traffic")
    return p.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    log = (lambda line: print("      " + line)) if args.verbose else None
    quiet = lambda line: None  # noqa: E731
    ports = {"coord": args.coord, "bulb": args.bulb, "switch": args.switch}
    nodes = {name: Etrx.open(port, name=name, log=log) for name, port in ports.items()}
    c_e, b_e, s_e = nodes["coord"], nodes["bulb"], nodes["switch"]
    steps = Steps()
    eui: dict[str, str] = {}

    try:
        def identify():
            revisions = set()
            for name, e in nodes.items():
                e.wait_ready()
                e.factory_reset()
                _, revision, eui[name] = e.info()
                revisions.add(revision)
            check(len(revisions) == 1, f"different firmware revisions: {sorted(revisions)}")
            return ", ".join(f"{n} {eui[n]}" for n in nodes) + f", {revisions.pop()}"

        steps.run("same firmware on all three DKs", identify, critical=True)

        coord = Coordinator(c_e, NetworkOptions(args.channel, args.pan, args.epid), out=quiet)
        net = {}

        def form():
            info = coord.start()
            check(args.channel in (None, info.channel), f"formed on channel {info.channel}")
            check(args.pan in (None, info.pan), f"formed with PAN {info.pan:04X}")
            check(args.epid in (None, info.epid), f"formed with EPID {info.epid}")
            net["info"] = info
            return f"channel {info.channel}, PAN {info.pan:04X}, EPID {info.epid}"

        steps.run("coordinator forms the network", form, critical=True)

        info = net["info"]
        joiner = NetworkOptions(channel=info.channel, epid=info.epid, legacy=args.legacy_tc)
        bulb = Bulb(b_e, joiner, out=quiet)
        switch = Switch(s_e, joiner, bulb_eui=eui["bulb"], out=quiet)

        def join(node, name: str, role: str):
            mark = c_e.mark()
            got = node.start()
            check(got.role == role and got.epid == info.epid, f"joined as {got.role} in {got.epid}")
            c_e.wait_for(lambda ev: isinstance(ev, NewNode) and ev.eui == eui[name], 15, since=mark)

        steps.run("bulb joins as router", lambda: join(bulb, "bulb", "FFD"), critical=True)
        steps.run("switch joins as end device", lambda: join(switch, "switch", "ZED"), critical=True)

        def zcl_on_off():
            for command, expected in ((switch.on, True), (switch.off, False), (switch.toggle, True)):
                status = command()
                check(status == 0, f"{command.__name__}: Default Response status {status:02X}")
                check(bulb.wait_state(expected, 5), f"{command.__name__}: bulb did not follow")
            return "on, off, toggle"

        steps.run("switch controls the bulb (ZCL On/Off)", zcl_on_off)

        def unicasts():
            routes = [
                (s_e, switch.say, c_e, "0000"),
                (b_e, bulb.say, c_e, "0000"),
                (c_e, coord.ucast, b_e, eui["bulb"]),
                (c_e, coord.ucast, s_e, eui["switch"]),
                (b_e, bulb.say, s_e, eui["switch"]),
            ]
            for sender, send, receiver, addr in routes:
                text = f"from {sender.name} to {receiver.name}"
                mark_s, mark_r = sender.mark(), receiver.mark()
                seq = send(addr, text)
                sender.wait_for(lambda ev: isinstance(ev, Ack) and ev.seq == seq, 10, since=mark_s)
                receiver.wait_for(is_text(text, "UCAST"), 10, since=mark_r)
            return f"{len(routes)} routes"

        steps.run("text unicasts in all directions, acknowledged", unicasts)

        def broadcast():
            text = "hello network"
            mark_b, mark_s = b_e.mark(), s_e.mark()
            coord.bcast(text)
            b_e.wait_for(is_text(text, "BCAST"), 10, since=mark_b)
            try:
                s_e.wait_for(is_text(text), 3, since=mark_s)
            except TimeoutError:
                return repr(text)
            raise AssertionError("the end device received a broadcast sent to 0xFFFC")

        steps.run("coordinator broadcast reaches the router only", broadcast)

        def persistence():
            for e in (b_e, s_e):
                mark = e.mark()
                e.cmd("ATZ")
                e.wait_for(lambda ev: isinstance(ev, Jpan) and ev.epid == info.epid, 30, since=mark)
                e.wait_ready()
            check(switch.toggle() == 0, "toggle after the reset failed")
            check(bulb.wait_state(False, 5), "bulb did not follow after the reset")

        steps.run("bulb and switch rejoin after a reset", persistence)

        def soak():
            marks = {e: e.mark() for e in nodes.values()}
            time.sleep(args.soak)
            for e, mark in marks.items():
                try:
                    ev = e.wait_for(lambda ev: isinstance(ev, (LeftPan, NodeLeft)), 0.01, since=mark)
                except TimeoutError:
                    continue
                raise AssertionError(f"{e.name}: {ev}")
            check(switch.toggle() == 0, "toggle after the soak failed")
            return f"{args.soak:.0f} s"

        steps.run("everyone stays in the network", soak)
    except Abort:
        print("stopped: a step that the others depend on failed")
    finally:
        for e in nodes.values():
            e.close()

    print(f"{'FAILED' if steps.failed else 'PASSED'}: {steps.failed} step(s) failed")
    return 1 if steps.failed else 0


def main(argv=None) -> int:
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
