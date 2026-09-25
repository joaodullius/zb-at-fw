import time

import pytest

from etrx import EtrxError

pytestmark = pytest.mark.hil

OK = b"\r\nOK\r\n"


def test_at_answers_ok(dk):
    assert dk.cmd("AT") == []


def test_lower_case_prefix(dk):
    assert dk.cmd("at") == []


def test_ati(dk):
    name, revision, eui = dk.info()
    assert name == "Telegesis nRF54L15"
    assert revision == "R309N"
    assert len(eui) == 16 and int(eui, 16) != 0


@pytest.mark.parametrize("line", ["AT+NOPE", "HELLO", "ATX"])
def test_unknown_command_is_error_02(dk, line):
    with pytest.raises(EtrxError) as exc:
        dk.cmd(line)
    assert exc.value.code == 0x02


def test_too_long_line_is_error_74(dk):
    with pytest.raises(EtrxError) as exc:
        dk.cmd("AT+" + "X" * 250)
    assert exc.value.code == 0x74


def test_atz_reboots_and_answers_again(dk):
    dk.reset()
    assert dk.cmd("AT") == []


@pytest.fixture
def raw(request):
    """The DK's serial port without an Etrx reader, for byte-exact checks."""
    import serial

    port = request.config.getoption("--coord-port")
    if not port:
        pytest.skip("needs --coord-port")
    s = serial.Serial(port, 115200, timeout=0.05, write_timeout=2)
    s.reset_input_buffer()
    yield s
    s.close()


def read_until(port, predicate, timeout=3.0):
    got = bytearray()
    deadline = time.monotonic() + timeout
    while not predicate(got) and time.monotonic() < deadline:
        got += port.read(port.in_waiting or 1)
    return bytes(got)


def test_echo_is_exact(raw):
    # The typed command comes back byte for byte before the response.
    raw.write(b"ATI\r")
    got = read_until(raw, lambda g: g.endswith(OK))
    assert got.startswith(b"ATI\r\r\nTelegesis nRF54L15\r\n")
    assert got.endswith(OK)


def test_back_to_back_commands_in_one_write(raw):
    # Commands sent in one burst are all executed, in order.
    raw.write(b"AT\rATI\rAT\rATI\rAT\rATI\r")
    got = read_until(raw, lambda g: g.count(OK) >= 6)
    assert got.count(OK) == 6
    assert got.count(b"Telegesis nRF54L15") == 3
