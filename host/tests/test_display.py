# host/tests/test_display.py
import io

from etrx import Ack, Announce, EtrxError, NewNode, Rx, Text
from etrx.display import describe, repl, split_first


def test_describe():
    assert describe(Text("UCAST", "000D6F0000012345", b"hi")) == "ucast from 000D6F0000012345: hi"
    assert describe(Text("BCAST", None, b"ping")) == "bcast from ?: ping"
    assert describe(NewNode(0x1A2B, "000D6F0000012345", 0xFFFF)) == "new node 000D6F0000012345 (1A2B)"
    assert describe(Announce("ZED", "000D6F0000012345", 0x1A2B)) == "ZED announced: 000D6F0000012345 (1A2B)"
    assert describe(Rx(None, 0x1A2B, 0x0104, 2, 2, 6, b"\x01\x02")) == \
        "RX from 1A2B: profile 0104 cluster 0006 ep 2->2: 0102"
    assert describe(Ack(1)) is None


def test_split_first():
    assert split_first("0000 hello there") == ("0000", "hello there")


def test_repl_dispatches_and_reports_errors():
    out = []

    def fail(rest):
        raise EtrxError(0x28, "AT+EN")

    stream = io.StringIO("echo abc\nfail\nnope\n\nquit\necho never\n")
    repl({"echo": lambda rest: rest.upper(), "fail": fail}, stream=stream, out=out.append)
    assert out[0].startswith("commands:")
    assert out[1:] == ["ABC", "error: AT+EN: ERROR:28", "unknown command 'nope'"]
