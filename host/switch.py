#!/usr/bin/env python3
"""On/Off switch on a DK running the AT firmware - a Zigbee end device.

This script is the manual procedure automated: everything it does can be typed in a
serial terminal (115200 8N1, commands end with Enter/CR) connected to the DK.

What it does
------------
Joins a network as an end device, exposes endpoint 2 as a Home Automation On/Off
Switch and sends ZCL On/Off/Toggle commands to one bulb, given by its EUI64 (--bulb;
ATI on the bulb shows it, and the coordinator prints it when the bulb joins). Each
command waits for the bulb's ZCL Default Response and prints its status (00 = success).
It also prints text messages it receives and sends text on request.

AT commands sent at start-up
----------------------------
    AT&F                     only with --reset: factory defaults, leave any network
    AT+N                     already an end device in a network? then keep it
    ATS0F=1104               prompts: RX: frames shown, payload in hex
    ATS00=<mask>             channel mask (--channel N -> only channel N, else FFFF = all)
    ATS03=<EPID>             --epid: join only this network (0000000000000000 = any open one)
    ATS0AA=<0|1>:password    --legacy (default): do not ask for a TC link key when joining;
                             required by pre-Zigbee 3.0 Trust Centres such as this firmware's
                             own coordinator
    ATS0AF=1:password        S0A bits F-E = 10: join as (non-sleepy) end device
    ATS0AE=0:password
    AT+JN                    scan and join -> JPAN:<channel>,<PAN>,<EPID>
    ATS48=0104               endpoint 2 profile: Home Automation
    ATS49=0000               endpoint 2 device: On/Off Switch
    ATS4B=0000,0003          endpoint 2 input clusters: Basic, Identify
    ATS4C=0006               endpoint 2 output cluster: On/Off
    ATZ                      only if S48-S4C changed: they take effect after a reset;
                             the module rejoins the same network on its own

Interactive commands (stdin) and the AT command each one sends
--------------------------------------------------------------
    on / off / t             AT+SENDUCASTB:03,<bulb EUI64>,02,02,0104,0006
                             then, after '>', the 3-byte ZCL frame 01 <seq> 01/00/02
                             (On / Off / Toggle); the bulb answers with
                             RX:...,0006,05:18<seq>0B<cmd>00 and the script prints "status 00"
    say <addr> <text>        AT+UCAST:<addr>=<text>    addr: EUI64 or NWK address (0000 = coordinator)
    quit                     leaves the script (the module stays in the network)

The first command to a bulb whose short address the switch does not know yet takes up
to a few seconds: the firmware asks the network for it (ZDO NWK_addr_req).

End devices do not receive broadcasts sent to routers (0xFFFC, what AT+BCAST uses):
this is Zigbee behaviour. They receive unicasts normally.

Network parameters
------------------
--channel only speeds up the scan; --epid picks the network when more than one is open
(the PAN ID is not used to join). Without both, the switch joins the first open network.

Examples
--------
    python switch.py --port COM7 --bulb F4CE36000000B001 --reset
    python switch.py --port COM7 --bulb F4CE36000000B001 --channel 20 --epid 00000000000A1B2C --reset -v

    switch: end device in PAN 7A31 on channel 20, bulb F4CE36000000B001
    commands: on | off | t | say | quit
    status 00

Troubleshooting: -v shows every command and response. "ERROR:06" on on/off/t: the bulb
EUI64 is wrong or the bulb is not in the network. A timeout after the command: the bulb
is not running bulb.py (nothing answers the ZCL command).
"""
from __future__ import annotations

import argparse
import sys
import threading

from etrx import (END_DEVICE, S0F_APP, Etrx, NetworkInfo, NetworkOptions, Rx, add_network_args,
                  configure_endpoint2, ensure_joined, options_from_args, wait_network, zcl)
from etrx.display import describe, repl, split_first
from etrx.net import parse_epid


class Switch:
    """HA On/Off Switch. All switch behaviour lives here."""

    ENDPOINT = 0x02
    DEVICE_ID = 0x0000  # HA On/Off Switch

    def __init__(self, etrx: Etrx, net: NetworkOptions, bulb_eui: str, bulb_endpoint: int = 0x02,
                 out=print):
        self.etrx = etrx
        self.net = net
        self.bulb_eui = bulb_eui.upper()
        self.bulb_endpoint = bulb_endpoint
        self.out = out
        self._seq = 0
        self._lock = threading.Lock()
        etrx.add_listener(self._on_event)

    def start(self) -> NetworkInfo:
        info = ensure_joined(self.etrx, self.net, END_DEVICE, S0F_APP)
        if configure_endpoint2(self.etrx, zcl.PROFILE_HA, self.DEVICE_ID,
                               [zcl.CLUSTER_BASIC, zcl.CLUSTER_IDENTIFY], [zcl.CLUSTER_ON_OFF]):
            info = wait_network(self.etrx)
        self.out(f"switch: end device in PAN {info.pan:04X} on channel {info.channel}, "
                 f"bulb {self.bulb_eui}")
        return info

    def on(self, timeout: float = 5.0) -> int:
        return self._send(zcl.CMD_ON, timeout)

    def off(self, timeout: float = 5.0) -> int:
        return self._send(zcl.CMD_OFF, timeout)

    def toggle(self, timeout: float = 5.0) -> int:
        return self._send(zcl.CMD_TOGGLE, timeout)

    def say(self, addr: str, text: str) -> int:
        return self.etrx.ucast(addr, text)

    def _send(self, command: int, timeout: float) -> int:
        with self._lock:
            self._seq = (self._seq + 1) & 0xFF
            seq = self._seq
        mark = self.etrx.mark()
        self.etrx.senducastb(self.bulb_eui, self.ENDPOINT, self.bulb_endpoint, zcl.PROFILE_HA,
                             zcl.CLUSTER_ON_OFF, zcl.on_off_command(seq, command))
        ev = self.etrx.wait_for(lambda e: self._is_response(e, seq), timeout, since=mark)
        _, status = zcl.parse_default_response(zcl.parse(ev.payload))
        return status

    def _is_response(self, ev, seq: int) -> bool:
        if not (isinstance(ev, Rx) and ev.cluster == zcl.CLUSTER_ON_OFF and ev.dst_ep == self.ENDPOINT):
            return False
        try:
            frame = zcl.parse(ev.payload)
            zcl.parse_default_response(frame)
        except ValueError:
            return False
        return frame.seq == seq

    def _on_event(self, ev):
        if isinstance(ev, Rx) and ev.cluster == zcl.CLUSTER_ON_OFF:
            return  # Default Responses are reported by on()/off()/toggle()
        line = describe(ev)
        if line:
            self.out(line)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    add_network_args(parser, legacy_default=True)
    parser.add_argument("--bulb", required=True, type=parse_epid, metavar="EUI64",
                        help="EUI64 of the bulb (ATI on the bulb shows it)")
    parser.add_argument("-v", "--verbose", action="store_true", help="show the AT traffic")
    args = parser.parse_args(argv)
    etrx = Etrx.open(args.port, name="switch", log=print if args.verbose else None)
    try:
        sw = Switch(etrx, options_from_args(args), args.bulb)
        sw.start()
        repl({
            "on": lambda rest: f"status {sw.on():02X}",
            "off": lambda rest: f"status {sw.off():02X}",
            "t": lambda rest: f"status {sw.toggle():02X}",
            "say": lambda rest: sw.say(*split_first(rest)),
        })
    except KeyboardInterrupt:
        pass
    finally:
        etrx.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
