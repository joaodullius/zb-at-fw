"""The few ZCL frames the host applications need (On/Off cluster)."""
from __future__ import annotations

from dataclasses import dataclass

PROFILE_HA = 0x0104
CLUSTER_BASIC = 0x0000
CLUSTER_IDENTIFY = 0x0003
CLUSTER_ON_OFF = 0x0006

CMD_OFF = 0x00
CMD_ON = 0x01
CMD_TOGGLE = 0x02

READ_ATTRIBUTES = 0x00
READ_ATTRIBUTES_RESPONSE = 0x01
DEFAULT_RESPONSE = 0x0B

ATTR_ON_OFF = 0x0000
TYPE_BOOLEAN = 0x10
STATUS_SUCCESS = 0x00

_FC_CLUSTER_SPECIFIC = 0x01
_FC_MANUFACTURER_SPECIFIC = 0x04
_FC_SERVER_TO_CLIENT = 0x08
_FC_DISABLE_DEFAULT_RESPONSE = 0x10


@dataclass(frozen=True)
class ZclFrame:
    cluster_specific: bool
    server_to_client: bool
    disable_default_response: bool
    seq: int
    command: int
    payload: bytes


def parse(data: bytes) -> ZclFrame:
    if len(data) < 3:
        raise ValueError("ZCL frame shorter than 3 bytes")
    fc = data[0]
    if fc & _FC_MANUFACTURER_SPECIFIC:
        raise ValueError("manufacturer-specific frames are not supported")
    return ZclFrame(bool(fc & _FC_CLUSTER_SPECIFIC), bool(fc & _FC_SERVER_TO_CLIENT),
                    bool(fc & _FC_DISABLE_DEFAULT_RESPONSE), data[1], data[2], bytes(data[3:]))


def on_off_command(seq: int, command: int) -> bytes:
    return bytes([_FC_CLUSTER_SPECIFIC, seq & 0xFF, command])


def default_response(seq: int, command: int, status: int = STATUS_SUCCESS) -> bytes:
    fc = _FC_SERVER_TO_CLIENT | _FC_DISABLE_DEFAULT_RESPONSE
    return bytes([fc, seq & 0xFF, DEFAULT_RESPONSE, command, status])


def read_attributes(seq: int, attr_ids: list[int]) -> bytes:
    body = b"".join(a.to_bytes(2, "little") for a in attr_ids)
    return bytes([0x00, seq & 0xFF, READ_ATTRIBUTES]) + body


def read_on_off_response(seq: int, on: bool) -> bytes:
    fc = _FC_SERVER_TO_CLIENT | _FC_DISABLE_DEFAULT_RESPONSE
    return bytes([fc, seq & 0xFF, READ_ATTRIBUTES_RESPONSE, 0x00, 0x00, STATUS_SUCCESS,
                  TYPE_BOOLEAN, 1 if on else 0])


def parse_default_response(frame: ZclFrame) -> tuple[int, int]:
    if frame.cluster_specific or frame.command != DEFAULT_RESPONSE or len(frame.payload) < 2:
        raise ValueError("not a Default Response")
    return frame.payload[0], frame.payload[1]
