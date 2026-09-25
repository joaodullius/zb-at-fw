# host/tests/test_net.py
import argparse

from etrx.net import (END_DEVICE, ROUTER, S0F_APP, NetworkOptions, add_network_args,
                      configure_endpoint2, ensure_coordinator, ensure_joined, options_from_args,
                      wait_network)


class Recorder:
    """Responder that records commands and answers like the firmware."""

    def __init__(self, network=None, regs=None):
        self.network = network  # "+N=..." line or None
        self.regs = regs or {}
        self.log = []

    def __call__(self, cmd):
        self.log.append(cmd)
        if cmd == "AT+N":
            return [self.network or "+N=NoPAN", "OK"]
        if cmd == "AT+EN":
            self.network = "+N=COO,25,08,7A31,00000000000A1B2C"
            return ["JPAN:25,7A31,00000000000A1B2C", "OK"]
        if cmd in ("AT+JN",) or cmd.startswith("AT+JPAN"):
            self.network = "+N=FFD,25,08,7A31,00000000000A1B2C"
            return ["JPAN:25,7A31,00000000000A1B2C", "OK"]
        if cmd.startswith("ATS") and cmd.endswith("?"):
            return [self.regs.get(cmd[3:5], "0000"), "OK"]
        if cmd.startswith("ATS") and "=" in cmd:
            reg, value = cmd[3:].split("=", 1)
            if len(reg) == 2:
                self.regs[reg] = value.split(":")[0]
            return ["OK"]
        return ["OK"]


def test_options_from_args():
    p = argparse.ArgumentParser()
    add_network_args(p, legacy_default=True)
    o = options_from_args(p.parse_args(["--port", "COM7", "--channel", "25", "--pan", "7A31",
                                        "--epid", "00000000000A1B2C"]))
    assert o == NetworkOptions(25, 0x7A31, "00000000000A1B2C", legacy=True, reset=False)
    o = options_from_args(p.parse_args(["--port", "COM7", "--no-legacy", "--reset"]))
    assert o.legacy is False and o.reset is True and o.channel is None


def test_apply_writes_registers(fake_etrx):
    r = Recorder()
    e, _ = fake_etrx(r)
    NetworkOptions(25, 0x7A31, "00000000000A1B2C", legacy=True).apply(e)
    assert r.log == ["ATS00=4000", "ATS02=7A31", "ATS03=00000000000A1B2C", "ATS0AA=1:password"]
    r.log.clear()
    NetworkOptions().apply(e)
    assert r.log == ["ATS00=FFFF", "ATS02=0000", "ATS03=0000000000000000", "ATS0AA=0:password"]


def test_ensure_coordinator_forms_when_out_of_network(fake_etrx):
    r = Recorder()
    e, _ = fake_etrx(r)
    info = ensure_coordinator(e, NetworkOptions(), prompt_flags=0x1904)
    assert info.role == "COO"
    assert "ATS0F=1904" in r.log and "AT+EN" in r.log


def test_ensure_coordinator_keeps_existing_network(fake_etrx):
    r = Recorder(network="+N=COO,25,08,7A31,00000000000A1B2C")
    e, _ = fake_etrx(r)
    ensure_coordinator(e, NetworkOptions(), prompt_flags=0x1904)
    assert "AT+EN" not in r.log and "AT+DASSL" not in r.log


def test_ensure_joined_leaves_wrong_role_and_joins_as_end_device(fake_etrx):
    r = Recorder(network="+N=COO,25,08,7A31,00000000000A1B2C")
    e, _ = fake_etrx(r)
    ensure_joined(e, NetworkOptions(legacy=True), END_DEVICE, prompt_flags=S0F_APP)
    assert "AT+DASSL" in r.log
    assert "ATS0AF=1:password" in r.log and "ATS0AE=0:password" in r.log
    assert r.log[-2:] == ["AT+JN", "AT+N"]


def test_ensure_joined_with_reset_does_factory_reset(fake_etrx):
    r = Recorder()
    e, _ = fake_etrx(r)
    ensure_joined(e, NetworkOptions(reset=True), ROUTER, prompt_flags=S0F_APP)
    assert "AT&F" in r.log


def test_configure_endpoint2_resets_only_on_change(fake_etrx):
    r = Recorder(regs={"48": "0104", "49": "0100", "4B": "0000,0003,0006", "4C": ""})
    e, _ = fake_etrx(r)
    assert configure_endpoint2(e, 0x0104, 0x0100, [0, 3, 6], []) is False
    assert "ATZ" not in r.log
    assert configure_endpoint2(e, 0x0104, 0x0000, [0, 3], [6]) is True
    assert "ATS49=0000" in r.log and "ATS4C=0006" in r.log and "ATZ" in r.log


def test_wait_network_polls_until_joined(fake_etrx):
    answers = iter(["+N=NoPAN", "+N=NoPAN", "+N=FFD,25,08,7A31,00000000000A1B2C"])
    e, _ = fake_etrx(lambda cmd: [next(answers), "OK"] if cmd == "AT+N" else ["OK"])
    assert wait_network(e, timeout=5).role == "FFD"
