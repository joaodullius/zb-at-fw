# host/tests/test_roles.py
import time

from etrx import NetworkOptions


def eventually(pred, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.02)
    return False


class Firmware:
    """Answers like the AT firmware for the commands the role classes use."""

    def __init__(self, network=None, regs=None):
        self.network = network
        self.regs = regs or {}
        self.log = []
        self.match_answers = ["MatchDesc:6CBF,00,02"]

    def __call__(self, cmd):
        self.log.append(cmd)
        if cmd == "AT+N":
            return [self.network or "+N=NoPAN", "OK"]
        if cmd == "AT+EN":
            self.network = "+N=COO,25,08,7A31,00000000000A1B2C"
            return ["JPAN:25,7A31,00000000000A1B2C", "OK"]
        if cmd == "AT+JN":
            kind = "ZED" if self.regs.get("0AF") == "1" else "FFD"
            self.network = f"+N={kind},25,08,7A31,00000000000A1B2C"
            return ["JPAN:25,7A31,00000000000A1B2C", "OK"]
        if cmd.endswith("<data>"):  # binary payload received after '>'
            return ["SEQ:01", "OK"]
        if cmd.startswith("AT+SENDUCASTB:"):
            return [">"]
        if cmd.startswith("AT+UCAST:"):
            return ["SEQ:02", "OK"]
        if cmd.startswith("AT+MATCHREQ:"):
            return ["OK", *self.match_answers]
        if cmd.startswith("ATS") and cmd.endswith("?"):
            return [self.regs.get(cmd[3:5], ""), "OK"]
        if cmd.startswith("ATS") and "=" in cmd:
            reg, value = cmd[3:].split("=", 1)
            self.regs[reg] = value.split(":")[0]
            return ["OK"]
        return ["OK"]


# --- coordinator ------------------------------------------------------------
from coordinator import Coordinator  # noqa: E402


def test_coordinator_start_forms_network(fake_etrx):
    fw = Firmware()
    e, _ = fake_etrx(fw)
    info = Coordinator(e, NetworkOptions(channel=25), out=lambda s: None).start()
    assert (info.role, info.channel, info.pan) == ("COO", 25, 0x7A31)
    assert "ATS00=4000" in fw.log and "ATS0F=1904" in fw.log and "AT+EN" in fw.log


def test_coordinator_reports_prompts(fake_etrx):
    out = []
    e, m = fake_etrx(Firmware())
    Coordinator(e, NetworkOptions(), out=out.append)
    m.push("NEWNODE:1A2B,000D6F0000012345,FFFF", "UCAST:000D6F0000012345,02=hi")
    assert eventually(lambda: "ucast from 000D6F0000012345: hi" in out)
    assert "new node 000D6F0000012345 (1A2B)" in out


def test_coordinator_sends_text(fake_etrx):
    fw = Firmware()
    e, _ = fake_etrx(fw)
    coord = Coordinator(e, NetworkOptions(), out=lambda s: None)
    assert coord.ucast("1A2B", "hello") == 2
    coord.bcast("ping")
    assert fw.log[-2:] == ["AT+UCAST:1A2B=hello", "AT+BCAST:00,ping"]


# --- bulb -------------------------------------------------------------------
from bulb import Bulb  # noqa: E402


def make_bulb(fake_etrx):
    fw = Firmware()
    e, m = fake_etrx(fw)
    return Bulb(e, NetworkOptions(), out=lambda s: None), fw, m


def test_bulb_follows_on_off_toggle_and_answers(fake_etrx):
    bulb, fw, m = make_bulb(fake_etrx)
    m.push("RX:1A2B,0104,02,02,0006,03:010501")
    assert bulb.wait_state(True, 2)
    assert eventually(lambda: m.binary_payloads == [bytes([0x18, 0x05, 0x0B, 0x01, 0x00])])
    assert "AT+SENDUCASTB:05,1A2B,02,02,0104,0006" in fw.log
    m.push("RX:1A2B,0104,02,02,0006,03:010602")
    assert bulb.wait_state(False, 2)


def test_bulb_respects_disable_default_response(fake_etrx):
    bulb, fw, m = make_bulb(fake_etrx)
    m.push("RX:1A2B,0104,02,02,0006,03:110701")
    assert bulb.wait_state(True, 2)
    time.sleep(0.2)
    assert m.binary_payloads == []


def test_bulb_answers_read_attributes(fake_etrx):
    bulb, fw, m = make_bulb(fake_etrx)
    m.push("RX:1A2B,0104,02,02,0006,05:0009000000")
    assert eventually(lambda: m.binary_payloads ==
                      [bytes([0x18, 0x09, 0x01, 0x00, 0x00, 0x00, 0x10, 0x00])])


def test_bulb_ignores_other_profiles_and_clusters(fake_etrx):
    bulb, fw, m = make_bulb(fake_etrx)
    m.push("RX:1A2B,C091,02,02,0006,03:010501", "RX:1A2B,0104,02,02,0008,03:010501")
    time.sleep(0.3)
    assert bulb.state is False and m.binary_payloads == []


def test_bulb_start_joins_as_router_then_configures_endpoint(fake_etrx):
    fw = Firmware()
    e, _ = fake_etrx(fw)
    info = Bulb(e, NetworkOptions(), out=lambda s: None).start()
    assert info.role == "FFD"
    assert fw.log.index("AT+JN") < fw.log.index("ATS48=0104") < fw.log.index("ATZ")
    assert "ATS4B=0000,0003,0006" in fw.log and "ATS0F=1104" in fw.log


# --- switch -----------------------------------------------------------------
import pytest  # noqa: E402

from switch import Switch  # noqa: E402

BULB_EUI = "000D6F0000012345"


def make_switch(fake_etrx, response=None):
    fw = Firmware()

    def responder(cmd):
        lines = fw(cmd)
        if cmd.endswith("<data>") and response is not None:
            lines = lines + [response]
        return lines

    e, m = fake_etrx(responder)
    return Switch(e, NetworkOptions(), BULB_EUI, out=lambda s: None), fw, m


def test_switch_on_returns_default_response_status(fake_etrx):
    sw, fw, m = make_switch(fake_etrx, "RX:1A2B,0104,02,02,0006,05:18010B0100")
    assert sw.on() == 0x00
    assert m.binary_payloads == [bytes([0x01, 0x01, 0x01])]
    assert f"AT+SENDUCASTB:03,{BULB_EUI},02,02,0104,0006" in fw.log


def test_switch_times_out_without_response(fake_etrx):
    sw, fw, m = make_switch(fake_etrx)
    with pytest.raises(TimeoutError):
        sw.toggle(timeout=0.5)


def test_switch_ignores_response_with_other_sequence_number(fake_etrx):
    sw, fw, m = make_switch(fake_etrx, "RX:1A2B,0104,02,02,0006,05:18090B0100")
    with pytest.raises(TimeoutError):
        sw.off(timeout=0.5)


def test_switch_start_joins_as_end_device(fake_etrx):
    fw = Firmware()
    e, _ = fake_etrx(fw)
    info = Switch(e, NetworkOptions(legacy=True), BULB_EUI, out=lambda s: None).start()
    assert info.role == "ZED"
    assert "ATS0AF=1:password" in fw.log and "ATS0AA=1:password" in fw.log
    assert "ATS4C=0006" in fw.log


def test_switch_without_bulb_finds_it_with_matchreq(fake_etrx):
    fw = Firmware()
    e, m = fake_etrx(fw)
    sw = Switch(e, NetworkOptions(legacy=True), out=lambda s: None)
    sw.start()
    assert "AT+MATCHREQ:0104,01,0006,00" in fw.log
    assert (sw.bulb_addr, sw.bulb_endpoint) == ("6CBF", 0x02)
    with pytest.raises(TimeoutError):
        sw.on(timeout=0.3)  # no Default Response in this double; only the target matters here
    assert "AT+SENDUCASTB:03,6CBF,02,02,0104,0006" in fw.log


def test_switch_manual_binding_skips_discovery(fake_etrx):
    fw = Firmware()
    e, _ = fake_etrx(fw)
    sw = Switch(e, NetworkOptions(legacy=True), BULB_EUI, out=lambda s: None)
    sw.start()
    assert not any(c.startswith("AT+MATCHREQ") for c in fw.log)
    assert sw.bulb_addr == BULB_EUI


def test_switch_without_answer_has_no_target(fake_etrx):
    fw = Firmware()
    fw.match_answers = []
    e, _ = fake_etrx(fw)
    sw = Switch(e, NetworkOptions(legacy=True), out=lambda s: None, find_timeout=0.3)
    sw.start()
    assert sw.bulb_addr is None
    with pytest.raises(ValueError):
        sw.toggle()
    fw.match_answers = ["MatchDesc:1A2B,00,0A"]
    assert [m.nwk for m in sw.find()] == [0x1A2B]
    assert (sw.bulb_addr, sw.bulb_endpoint) == ("1A2B", 0x0A)
