# host/tests/test_prompts.py
from etrx.prompts import (Ack, Announce, Error, Jpan, LeftPan, Nack, NewNode, NodeLeft, Ok, Rx,
                          Seq, Text, parse_prompt)


def test_ok_error_seq_ack_nack():
    assert parse_prompt("OK") == Ok()
    assert parse_prompt("ERROR:28") == Error(0x28)
    assert parse_prompt("SEQ:0A") == Seq(0x0A)
    assert parse_prompt("ACK:0A") == Ack(0x0A)
    assert parse_prompt("NACK:FF") == Nack(0xFF)


def test_jpan_and_leftpan():
    assert parse_prompt("JPAN:25,7A31,00000000000A1B2C") == Jpan(25, 0x7A31, "00000000000A1B2C")
    assert parse_prompt("LeftPAN") == LeftPan()


def test_newnode_announce_nodeleft():
    assert parse_prompt("NEWNODE:1A2B,000D6F0000012345,FFFF") == NewNode(0x1A2B, "000D6F0000012345", 0xFFFF)
    assert parse_prompt("NEWNODE: 1A2B,000D6F0000012345,0000") == NewNode(0x1A2B, "000D6F0000012345", 0)
    assert parse_prompt("FFD:000D6F0000012345,1A2B") == Announce("FFD", "000D6F0000012345", 0x1A2B, None, None)
    assert parse_prompt("ZED:000D6F0000012345,1A2B,-60,200") == Announce("ZED", "000D6F0000012345", 0x1A2B, -60, 200)
    assert parse_prompt("NODELEFT:1A2B,000D6F0000012345") == NodeLeft(0x1A2B, "000D6F0000012345")


def test_text_prompts():
    assert parse_prompt("UCAST:000D6F0000012345,05=Hello") == Text("UCAST", "000D6F0000012345", b"Hello", None, None)
    assert parse_prompt("BCAST:04=ping") == Text("BCAST", None, b"ping", None, None)
    assert parse_prompt("BCAST:04=ping,-55,180") == Text("BCAST", None, b"ping", -55, 180)


def test_text_with_separators():
    # Review focus 4: the length field decides where the data ends.
    assert parse_prompt("UCAST:0A=a,b=c:d,-1").data == b"a,b=c:d,-1"
    p = parse_prompt("UCAST:07=x,y=z:w,-40,99")
    assert p == Text("UCAST", None, b"x,y=z:w", -40, 99)


def test_rx_hex_and_raw():
    p = parse_prompt("RX:1A2B,0104,02,02,0006,03:010002")
    assert p == Rx(None, 0x1A2B, 0x0104, 2, 2, 0x0006, bytes([1, 0, 2]), None, None)
    p = parse_prompt("RX:000D6F0000012345,1A2B,0104,02,02,0006,03:010002,-70,150")
    assert p == Rx("000D6F0000012345", 0x1A2B, 0x0104, 2, 2, 0x0006, bytes([1, 0, 2]), -70, 150)
    p = parse_prompt("RX:1A2B,C091,01,01,0003,02:\x01\x02", rx_hex=False)
    assert p.payload == b"\x01\x02"


def test_unknown_lines_are_none():
    assert parse_prompt("+N=COO,25,08,7A31,00000000000A1B2C") is None
    assert parse_prompt("Telegesis nRF54L15") is None
    assert parse_prompt("") is None
