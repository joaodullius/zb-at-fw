#!/usr/bin/env python3
"""On/Off light (bulb) on a DK running the AT firmware - a Zigbee router.

This script is the manual procedure automated: everything it does can be typed in a
serial terminal (115200 8N1, commands end with Enter/CR) connected to the DK.

What it does
------------
Joins a network as a router, exposes endpoint 2 as a Home Automation On/Off Light,
keeps the light state (ON/OFF, printed on every change), answers ZCL On/Off commands
and OnOff attribute reads, prints text messages it receives and sends text on request.

AT commands sent at start-up
----------------------------
    AT&F                     only with --reset: factory defaults, leave any network
    AT+N                     already a router in a network? then keep it
    ATS0F=1104               prompts: RX: frames shown, payload in hex
    ATS00=<mask>             channel mask (--channel N -> only channel N, else FFFF = all)
    ATS03=<EPID>             --epid: join only this network (0000000000000000 = any open one)
    ATS0AA=<0|1>:password    --legacy (default): do not ask for a TC link key when joining;
                             required by pre-Zigbee 3.0 Trust Centres such as this firmware's
                             own coordinator
    ATS0AF=0:password        S0A bits F-E = 00: join as router
    ATS0AE=0:password
    AT+JN                    scan and join -> JPAN:<channel>,<PAN>,<EPID>
    ATS48=0104               endpoint 2 profile: Home Automation
    ATS49=0100               endpoint 2 device: On/Off Light
    ATS4B=0000,0003,0006     endpoint 2 input clusters: Basic, Identify, On/Off
    ATS4C=                   endpoint 2 output clusters: none
    ATZ                      only if S48-S4C changed: they take effect after a reset;
                             the module rejoins the same network on its own

How it answers ZCL (what you would do by hand)
----------------------------------------------
A command from a switch arrives as a prompt, e.g. On (command 01, sequence 05):
    RX:<EUI64>,<NWK>,0104,02,02,0006,03:010501
The bulb switches the light on and answers with a ZCL Default Response (18 05 0B 01 00):
    AT+SENDUCASTB:05,<NWK>,02,02,0104,0006      then, after the '>' prompt, the 5 bytes
    SEQ:XX / OK / ACK:XX

Interactive commands (stdin) and the AT command each one sends
--------------------------------------------------------------
    say <addr> <text>        AT+UCAST:<addr>=<text>    addr: EUI64 or NWK address (0000 = coordinator)
    state                    (no AT command)           prints ON or OFF
    quit                     leaves the script (the module stays in the network)

Text received from other nodes is printed as "ucast/bcast from <EUI64>: <text>".

Network parameters
------------------
--channel only speeds up the scan; --epid picks the network when more than one is open
(the PAN ID is not used to join). Without both, the bulb joins the first open network.

Examples
--------
    python bulb.py --port COM36 --reset
    python bulb.py --port COM36 --channel 20 --epid 00000000000A1B2C --reset -v

    bulb: router in PAN 7A31 on channel 20, light is OFF
    commands: say | state | quit
    bulb: light is ON
    ucast from F4CE36000000B002: hello

Troubleshooting: -v shows every command and response. "ERROR:94"/"ERROR:27" on AT+JN:
no open network found - check the coordinator is up, the channel/EPID, and that S0A
bit A matches the Trust Centre (use --legacy with this firmware's coordinator).
"""
from __future__ import annotations

import argparse
import sys
import threading

from etrx import (ROUTER, S0F_APP, Etrx, NetworkInfo, NetworkOptions, Rx, add_network_args,
                  configure_endpoint2, ensure_joined, options_from_args, wait_network, zcl)
from etrx.display import describe, repl, split_first


class Bulb:
    """HA On/Off Light. All bulb behaviour lives here."""

    ENDPOINT = 0x02
    DEVICE_ID = 0x0100  # HA On/Off Light
    IN_CLUSTERS = [zcl.CLUSTER_BASIC, zcl.CLUSTER_IDENTIFY, zcl.CLUSTER_ON_OFF]

    def __init__(self, etrx: Etrx, net: NetworkOptions, out=print):
        self.etrx = etrx
        self.net = net
        self.out = out
        self._state = False
        self._cond = threading.Condition()
        etrx.add_listener(self._on_event)

    @property
    def state(self) -> bool:
        return self._state

    def start(self) -> NetworkInfo:
        info = ensure_joined(self.etrx, self.net, ROUTER, S0F_APP)
        if configure_endpoint2(self.etrx, zcl.PROFILE_HA, self.DEVICE_ID, self.IN_CLUSTERS, []):
            info = wait_network(self.etrx)
        self.out(f"bulb: router in PAN {info.pan:04X} on channel {info.channel}, light is OFF")
        return info

    def wait_state(self, value: bool, timeout: float = 5.0) -> bool:
        with self._cond:
            return self._cond.wait_for(lambda: self._state == value, timeout)

    def say(self, addr: str, text: str) -> int:
        return self.etrx.ucast(addr, text)

    def _set_state(self, on: bool):
        with self._cond:
            self._state = on
            self._cond.notify_all()
        self.out(f"bulb: light is {'ON' if on else 'OFF'}")

    def _on_event(self, ev):
        if isinstance(ev, Rx) and ev.dst_ep == self.ENDPOINT and ev.profile == zcl.PROFILE_HA:
            self._handle_zcl(ev)
            return
        line = describe(ev)
        if line:
            self.out(line)

    def _handle_zcl(self, ev: Rx):
        if ev.cluster != zcl.CLUSTER_ON_OFF:
            return
        try:
            frame = zcl.parse(ev.payload)
        except ValueError:
            return
        if frame.cluster_specific and frame.command in (zcl.CMD_OFF, zcl.CMD_ON, zcl.CMD_TOGGLE):
            new_state = {zcl.CMD_OFF: False, zcl.CMD_ON: True}.get(frame.command, not self._state)
            self._set_state(new_state)
            if not frame.disable_default_response:
                self._reply(ev, zcl.default_response(frame.seq, frame.command))
        elif not frame.cluster_specific and frame.command == zcl.READ_ATTRIBUTES:
            self._reply(ev, zcl.read_on_off_response(frame.seq, self._state))

    def _reply(self, ev: Rx, payload: bytes):
        self.etrx.senducastb(f"{ev.nwk:04X}", self.ENDPOINT, ev.src_ep, zcl.PROFILE_HA, ev.cluster,
                             payload)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_network_args(parser, legacy_default=True)
    parser.add_argument("-v", "--verbose", action="store_true", help="show the AT traffic")
    args = parser.parse_args(argv)
    etrx = Etrx.open(args.port, name="bulb", log=print if args.verbose else None)
    try:
        bulb = Bulb(etrx, options_from_args(args))
        bulb.start()
        repl({
            "say": lambda rest: bulb.say(*split_first(rest)),
            "state": lambda rest: "ON" if bulb.state else "OFF",
        })
    except KeyboardInterrupt:
        pass
    finally:
        etrx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
