"""Network options shared by the role scripts, and the set-up steps they have in common."""
from __future__ import annotations

import argparse
import time
from dataclasses import dataclass

from .protocol import PASSWORD, Etrx, NetworkInfo

ROUTER = "router"
END_DEVICE = "end_device"

# S0F: bit C hex payload in RX:, bit 8 show unhandled frames, bit 2 hide sink adverts.
S0F_APP = 0x1104
# Coordinator also shows NODELEFT (bit B).
S0F_COORDINATOR = 0x1904

_S0A = 0x0A
_S0A_NO_TCLK_REQUEST = 0xA
_S0A_TYPE_HIGH = 0xF
_S0A_TYPE_LOW = 0xE


@dataclass
class NetworkOptions:
    channel: int | None = None
    pan: int | None = None
    epid: str | None = None
    legacy: bool = False
    reset: bool = False

    def apply(self, etrx: Etrx):
        mask = 0xFFFF if self.channel is None else 1 << (self.channel - 11)
        etrx.sreg_set(0x00, f"{mask:04X}")
        etrx.sreg_set(0x02, f"{self.pan or 0:04X}")
        etrx.sreg_set(0x03, (self.epid or "0" * 16).upper())
        etrx.sreg_bit_set(_S0A, _S0A_NO_TCLK_REQUEST, self.legacy, PASSWORD)


def parse_pan(text: str) -> int:
    value = int(text, 16)
    if not 0 <= value <= 0xFFFF:
        raise argparse.ArgumentTypeError("expected 4 hex digits")
    return value


def parse_epid(text: str) -> str:
    if len(text) != 16:
        raise argparse.ArgumentTypeError("expected 16 hex digits")
    int(text, 16)
    return text.upper()


def add_network_args(parser: argparse.ArgumentParser, legacy_default: bool):
    parser.add_argument("--port", required=True, help="serial port of the DK, e.g. COM7")
    parser.add_argument("--channel", type=int, choices=range(11, 27), metavar="11-26",
                        help="use only this channel (default: all)")
    parser.add_argument("--pan", type=parse_pan,
                        help="PAN ID used when forming a network (coordinator only), "
                             "4 hex digits (default: random)")
    parser.add_argument("--epid", type=parse_epid, help="extended PAN ID, 16 hex digits (default: any)")
    parser.add_argument("--legacy", action=argparse.BooleanOptionalAction, default=legacy_default,
                        help="do not request a TC link key when joining (pre-Zigbee 3.0 TC)")
    parser.add_argument("--reset", action="store_true",
                        help="factory-reset the module first (leaves any network)")


def options_from_args(args: argparse.Namespace) -> NetworkOptions:
    return NetworkOptions(args.channel, args.pan, args.epid, args.legacy, args.reset)


def _set_device_type(etrx: Etrx, device_type: str):
    end_device = device_type == END_DEVICE
    etrx.sreg_bit_set(_S0A, _S0A_TYPE_HIGH, end_device, PASSWORD)
    etrx.sreg_bit_set(_S0A, _S0A_TYPE_LOW, False, PASSWORD)


def _prepare(etrx: Etrx, net: NetworkOptions, want_role: set[str]) -> NetworkInfo | None:
    etrx.wait_ready()
    if net.reset:
        etrx.factory_reset()
    info = etrx.network()
    if info is not None and info.role in want_role:
        return info
    if info is not None:
        etrx.leave()
    return None


def ensure_coordinator(etrx: Etrx, net: NetworkOptions, prompt_flags: int) -> NetworkInfo:
    info = _prepare(etrx, net, {"COO"})
    etrx.sreg_set(0x0F, f"{prompt_flags:04X}")
    if info is not None:
        return info
    net.apply(etrx)
    etrx.form()
    return etrx.network()


def ensure_joined(etrx: Etrx, net: NetworkOptions, device_type: str, prompt_flags: int) -> NetworkInfo:
    roles = {"FFD"} if device_type == ROUTER else {"ZED", "SED", "MED"}
    info = _prepare(etrx, net, roles)
    etrx.sreg_set(0x0F, f"{prompt_flags:04X}")
    if info is not None:
        return info
    net.apply(etrx)
    _set_device_type(etrx, device_type)
    etrx.join()
    return etrx.network()


def configure_endpoint2(etrx: Etrx, profile: int, device_id: int, in_clusters: list[int],
                        out_clusters: list[int]) -> bool:
    """Write S48/S49/S4B/S4C; they take effect after a reset, so reset only if something changed."""
    wanted = {
        0x48: f"{profile:04X}",
        0x49: f"{device_id:04X}",
        0x4B: ",".join(f"{c:04X}" for c in in_clusters),
        0x4C: ",".join(f"{c:04X}" for c in out_clusters),
    }
    changed = False
    for reg, value in wanted.items():
        if etrx.sreg_get(reg) != value:
            etrx.sreg_set(reg, value)
            changed = True
    if changed:
        etrx.reset()
    return changed


def wait_network(etrx: Etrx, timeout: float = 20.0) -> NetworkInfo:
    """Poll AT+N until the node is in a network again (after ATZ it rejoins from NVRAM)."""
    deadline = time.monotonic() + timeout
    while True:
        info = etrx.network()
        if info is not None:
            return info
        if time.monotonic() > deadline:
            raise TimeoutError(f"{etrx.name}: not back in the network after {timeout} s")
        time.sleep(0.5)
