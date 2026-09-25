# host/tests/test_zcl.py
import pytest

from etrx import zcl


def test_on_off_command_bytes():
    assert zcl.on_off_command(5, zcl.CMD_TOGGLE) == bytes([0x01, 0x05, 0x02])


def test_parse_on_off_command():
    f = zcl.parse(bytes([0x01, 0x05, 0x01]))
    assert f.cluster_specific and not f.server_to_client and not f.disable_default_response
    assert (f.seq, f.command, f.payload) == (5, zcl.CMD_ON, b"")


def test_default_response_round_trip():
    data = zcl.default_response(5, zcl.CMD_ON)
    assert data == bytes([0x18, 0x05, 0x0B, 0x01, 0x00])
    f = zcl.parse(data)
    assert not f.cluster_specific and f.server_to_client
    assert zcl.parse_default_response(f) == (zcl.CMD_ON, zcl.STATUS_SUCCESS)


def test_read_attributes_and_response():
    assert zcl.read_attributes(9, [zcl.ATTR_ON_OFF]) == bytes([0x00, 0x09, 0x00, 0x00, 0x00])
    assert zcl.read_on_off_response(9, True) == bytes([0x18, 0x09, 0x01, 0x00, 0x00, 0x00, 0x10, 0x01])


def test_parse_rejects_short_and_manufacturer_specific():
    with pytest.raises(ValueError):
        zcl.parse(b"\x01\x05")
    with pytest.raises(ValueError):
        zcl.parse(bytes([0x05, 0x34, 0x12, 0x01, 0x00]))
    with pytest.raises(ValueError):
        zcl.parse_default_response(zcl.parse(bytes([0x18, 0x01, 0x0B, 0x01])))
