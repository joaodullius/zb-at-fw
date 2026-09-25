# host/tests/hil/test_sreg.py
import pytest

from etrx import PASSWORD, EtrxError

pytestmark = pytest.mark.hil


@pytest.fixture(autouse=True)
def factory(dk):
    dk.factory_reset()
    yield
    dk.factory_reset()


def err(fn, *args):
    with pytest.raises(EtrxError) as exc:
        fn(*args)
    return exc.value.code


def test_factory_defaults(dk):
    assert dk.sreg_get(0x00) == "FFFF"
    assert dk.sreg_get(0x02) == "0000"
    assert dk.sreg_get(0x03) == "0000000000000000"
    assert dk.sreg_get(0x0A) == "0000"
    assert dk.sreg_get(0x0F) == "0006"
    assert dk.sreg_get(0x12) == "0C00"
    assert dk.sreg_get(0x41) == "0101"
    assert dk.sreg_get(0x45) == "C091"
    assert dk.sreg_get(0x4B) == ""


def test_write_and_read_back(dk):
    dk.sreg_set(0x02, "1234")
    assert dk.sreg_get(0x02) == "1234"
    dk.sreg_set(0x02, "ab")
    assert dk.sreg_get(0x02) == "00AB"
    dk.sreg_set(0x03, "00000000000A1B2C")
    assert dk.sreg_get(0x03) == "00000000000A1B2C"
    dk.sreg_set(0x01, "-4")
    assert dk.sreg_get(0x01) == "-04"


def test_single_bit_access(dk):
    dk.sreg_bit_set(0x0F, 0xC, True)
    assert dk.sreg_get(0x0F) == "1006"
    assert dk.cmd("ATS0FC?") == ["1"]
    dk.sreg_bit_set(0x0F, 0xC, False)
    assert dk.cmd("ATS0FC?") == ["0"]


def test_password_protected_register(dk):
    assert err(dk.sreg_set, 0x0A, "8000") == 0x20
    assert err(dk.sreg_set, 0x0A, "8000", "wrong") == 0x20
    dk.sreg_set(0x0A, "8000", PASSWORD)
    assert dk.sreg_get(0x0A) == "8000"
    dk.cmd("ATS0A=0400;password")  # ';' separator also accepted
    assert dk.sreg_get(0x0A) == "0400"


def test_keys_are_write_only(dk):
    dk.sreg_set(0x08, "00112233445566778899AABBCCDDEEFF", PASSWORD)
    assert dk.sreg_get(0x08) == ""


def test_read_only_and_invalid(dk):
    assert err(dk.sreg_set, 0x04, "0000000000000000") == 0x19
    assert err(dk.cmd, "ATS77?") == 0x04
    assert err(dk.sreg_set, 0x02, "12345") == 0x05
    assert err(dk.sreg_set, 0x03, "1234") == 0x05
    assert err(dk.sreg_set, 0x4B, "0006,XYZ") == 0x05
    assert err(dk.sreg_set, 0x12, "0500") == 0x05


def test_cluster_list(dk):
    dk.sreg_set(0x4B, "0000,0003,0006")
    assert dk.sreg_get(0x4B) == "0000,0003,0006"
    dk.sreg_set(0x4B, "")
    assert dk.sreg_get(0x4B) == ""
    assert err(dk.sreg_set, 0x4B, ",".join(["0006"] * 9)) == 0x05


def test_eui_register_matches_ati(dk):
    assert dk.sreg_get(0x04) == dk.info()[2]
    assert dk.sreg_get(0x05) == "FFFE"


def test_non_volatile_survives_reset_volatile_does_not(dk):
    dk.sreg_set(0x02, "4321")
    dk.sreg_set(0x40, "0202")
    dk.reset()
    assert dk.sreg_get(0x02) == "4321"
    assert dk.sreg_get(0x40) == "0101"


def test_factory_reset_restores_defaults(dk):
    dk.sreg_set(0x02, "4321")
    dk.factory_reset()
    assert dk.sreg_get(0x02) == "0000"


def test_tokdump(dk):
    lines = dk.cmd("AT+TOKDUMP")
    assert "S00:FFFF" in lines and "S0F:0006" in lines
