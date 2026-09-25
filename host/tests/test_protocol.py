# host/tests/test_protocol.py
import threading
import time

import pytest

from etrx.prompts import Jpan, Text
from etrx.protocol import EtrxError, NetworkInfo


def table(mapping):
    def responder(cmd):
        return mapping.get(cmd, ["ERROR:02"])
    return responder


def test_cmd_returns_lines_before_ok(fake_etrx):
    e, _ = fake_etrx(table({"ATI": ["Telegesis nRF54L15", "R309N", "F4CE36000000ABCD", "OK"]}))
    assert e.cmd("ATI") == ["Telegesis nRF54L15", "R309N", "F4CE36000000ABCD"]
    assert e.info() == ("Telegesis nRF54L15", "R309N", "F4CE36000000ABCD")


def test_cmd_raises_on_error(fake_etrx):
    e, _ = fake_etrx(table({}))
    with pytest.raises(EtrxError) as exc:
        e.cmd("AT+FOO")
    assert exc.value.code == 0x02


def test_cmd_times_out(fake_etrx):
    e, _ = fake_etrx(lambda cmd: [])
    with pytest.raises(TimeoutError):
        e.cmd("AT", timeout=0.2)


def test_echo_is_not_part_of_response(fake_etrx):
    e, m = fake_etrx(table({"ATS00?": ["FFFF", "OK"]}), echo=True)
    assert e.sreg_get(0x00) == "FFFF"
    assert m.written[-1] == b"ATS00?\r"


def test_async_prompt_during_command(fake_etrx):
    # Review focus 3.
    seen = []
    e, m = fake_etrx(table({"AT+N": ["UCAST:02=hi", "+N=COO,25,08,7A31,00000000000A1B2C", "OK"]}))
    e.add_listener(seen.append)
    mark = e.mark()
    assert e.network() == NetworkInfo("COO", 25, 8, 0x7A31, "00000000000A1B2C")
    e.wait_for(lambda ev: isinstance(ev, Text), timeout=1, since=mark)
    deadline = time.monotonic() + 1  # listeners run on the event thread
    while Text("UCAST", None, b"hi") not in seen and time.monotonic() < deadline:
        time.sleep(0.01)
    assert Text("UCAST", None, b"hi") in seen


def test_network_nopan(fake_etrx):
    e, _ = fake_etrx(table({"AT+N": ["+N=NoPAN", "OK"]}))
    assert e.network() is None


def test_form_and_join_return_jpan(fake_etrx):
    e, _ = fake_etrx(table({"AT+EN": ["JPAN:25,7A31,00000000000A1B2C", "OK"],
                            "AT+JN": ["JPAN:25,7A31,00000000000A1B2C", "OK"],
                            "AT+JPAN:25,00000000000A1B2C": ["JPAN:25,7A31,00000000000A1B2C", "OK"]}))
    assert e.form() == Jpan(25, 0x7A31, "00000000000A1B2C")
    assert e.join() == Jpan(25, 0x7A31, "00000000000A1B2C")
    assert e.join(channel=25, pan_or_epid="00000000000A1B2C").pan == 0x7A31


def test_ucast_returns_seq_and_ack_arrives_later(fake_etrx):
    e, m = fake_etrx(table({"AT+UCAST:0000=hello": ["SEQ:07", "OK"]}))
    mark = e.mark()
    assert e.ucast("0000", "hello") == 7
    m.push("ACK:07")
    ack = e.wait_for(lambda ev: getattr(ev, "seq", None) == 7, timeout=1, since=mark)
    assert type(ack).__name__ == "Ack"


def test_senducastb_waits_for_prompt_then_sends_data(fake_etrx):
    def responder(cmd):
        if cmd == "AT+SENDUCASTB:03,1A2B,02,02,0104,0006":
            return [">"]
        if cmd.endswith("<data>"):
            return ["SEQ:01", "OK"]
        return ["ERROR:02"]
    e, m = fake_etrx(responder)
    assert e.senducastb("1A2B", 2, 2, 0x0104, 0x0006, b"\x01\x00\x02") == 1
    assert m.binary_payloads == [b"\x01\x00\x02"]


def test_sreg_writes(fake_etrx):
    e, m = fake_etrx(lambda cmd: ["OK"])
    e.sreg_set(0x0A, "8000", password="password")
    e.sreg_bit_set(0x0A, 0xA, True, password="password")
    e.bcast("ping")
    assert m.written == [b"ATS0A=8000:password\r", b"ATS0AA=1:password\r", b"AT+BCAST:00,ping\r"]


def test_listener_can_send_commands(fake_etrx):
    # Review focus 5: a listener calling cmd() must not deadlock the reader.
    e, m = fake_etrx(table({"ATS00?": ["FFFF", "OK"]}))
    got = []
    done = threading.Event()

    def listener(ev):
        if isinstance(ev, Text):
            got.append(e.sreg_get(0))
            done.set()

    e.add_listener(listener)
    m.push("UCAST:02=hi")
    assert done.wait(2)
    assert got == ["FFFF"]


def test_sreg_get_ignores_prompts_between_value_and_ok(fake_etrx):
    # Review I-4: a rejoining node prints JPAN between the value and OK.
    e, _ = fake_etrx(table({"ATS48?": ["0104", "JPAN:25,7A31,00000000000A1B2C", "OK"],
                            "ATI": ["Telegesis nRF54L15", "JPAN:25,7A31,00000000000A1B2C",
                                    "R309N", "F4CE36000000ABCD", "OK"]}))
    assert e.sreg_get(0x48) == "0104"
    assert e.info() == ("Telegesis nRF54L15", "R309N", "F4CE36000000ABCD")
