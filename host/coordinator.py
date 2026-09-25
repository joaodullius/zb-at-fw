#!/usr/bin/env python3
"""Generic Zigbee coordinator (concentrator) on a DK running the AT firmware.

This script is the manual procedure automated: everything it does can be typed in a
serial terminal (115200 8N1, commands end with Enter/CR) connected to the DK.

What it does
------------
Forms a network as coordinator and Trust Centre (or keeps the network the module
already runs), then prints everything that happens on it - devices joining,
announcing and leaving, text and raw frames received - and sends text typed on stdin.

AT commands sent at start-up
----------------------------
    AT&F                     only with --reset: factory defaults, leave any network
    AT+N                     already a coordinator? then keep that network
    ATS0F=1904               prompts: NODELEFT on, RX: frames shown, payload in hex
    ATS00=<mask>             channel mask (--channel N -> only channel N, else FFFF = all)
    ATS02=<PAN>              --pan, 0000 = random PAN ID
    ATS03=<EPID>             --epid, 0000000000000000 = use this module's EUI64
    ATS0AA=<0|1>:password    --legacy (only matters for joining devices)
    AT+EN                    form the network -> JPAN:<channel>,<PAN>,<EPID>

What it prints (prompts from the module, see README "Prompts")
--------------------------------------------------------------
    new node <EUI64> (<NWK>)          NEWNODE: a device joined through the coordinator
    FFD / ZED announced: <EUI64> ...  FFD:/ZED:/SED: device announce (router / end device)
    node left: <EUI64> (<NWK>)        NODELEFT: a device left the network
    ucast / bcast from <EUI64>: text  UCAST:/BCAST: text received
    RX from <NWK>: ...                RX: any other frame (e.g. ZCL) received

Interactive commands (stdin) and the AT command each one sends
--------------------------------------------------------------
    bcast <text>             AT+BCAST:00,<text>        routers + coordinator (not end devices)
    ucast <addr> <text>      AT+UCAST:<addr>=<text>    addr: EUI64 (16 hex) or NWK address (4 hex)
    info                     AT+N                      network type, channel, power, PAN, EPID
    quit                     leaves the script (the network keeps running on the module)

Network parameters
------------------
PAN ID and extended PAN ID (EPID) are independent: the PAN ID (16 bits) is the network
address on the radio and may change on a conflict; the EPID (64 bits) is the stable
network identity that joining devices filter on. Give --channel/--pan/--epid to always
form the same network (and the same values to bulb.py/switch.py via --channel/--epid).
The coordinator of this firmware is a pre-Zigbee 3.0 Trust Centre: devices that join
it must skip the TC link key request (S0A bit A, --legacy in bulb.py/switch.py).
--legacy has no effect on the coordinator itself.

Examples
--------
    python coordinator.py --port COM31 --reset
    python coordinator.py --port COM31 --channel 20 --pan 7A31 --epid 00000000000A1B2C --reset -v

    coordinator: channel 20, PAN 7A31, EPID 00000000000A1B2C
    commands: bcast | ucast | info | quit
    new node F4CE36000000B001 (6CBF)
    FFD announced: F4CE36000000B001 (6CBF)
    ucast from F4CE36000000B002: hello

Troubleshooting: -v shows every command and response; "ERROR:28" means the module is
already in a network (use --reset); no NEWNODE means the joiner used another channel or
EPID, or did not set S0A bit A.
"""
from __future__ import annotations

import argparse
import sys

from etrx import (S0F_COORDINATOR, Etrx, NetworkInfo, NetworkOptions, add_network_args,
                  ensure_coordinator, options_from_args)
from etrx.display import describe, repl, split_first


class Coordinator:
    """Coordinator and Trust Centre. All coordinator behaviour lives here."""

    def __init__(self, etrx: Etrx, net: NetworkOptions, out=print):
        self.etrx = etrx
        self.net = net
        self.out = out
        etrx.add_listener(self._on_event)

    def start(self) -> NetworkInfo:
        info = ensure_coordinator(self.etrx, self.net, S0F_COORDINATOR)
        self.out(f"coordinator: channel {info.channel}, PAN {info.pan:04X}, EPID {info.epid}")
        return info

    def bcast(self, text: str, hops: int = 0):
        self.etrx.bcast(text, hops)

    def ucast(self, addr: str, text: str) -> int:
        return self.etrx.ucast(addr, text)

    def info(self) -> NetworkInfo | None:
        return self.etrx.network()

    def _on_event(self, ev):
        line = describe(ev)
        if line:
            self.out(line)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_network_args(parser, legacy_default=False)
    parser.add_argument("-v", "--verbose", action="store_true", help="show the AT traffic")
    args = parser.parse_args(argv)
    etrx = Etrx.open(args.port, name="coordinator", log=print if args.verbose else None)
    try:
        coord = Coordinator(etrx, options_from_args(args))
        coord.start()
        repl({
            "bcast": lambda rest: coord.bcast(rest),
            "ucast": lambda rest: coord.ucast(*split_first(rest)),
            "info": lambda rest: coord.info(),
        })
    except KeyboardInterrupt:
        pass
    finally:
        etrx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
