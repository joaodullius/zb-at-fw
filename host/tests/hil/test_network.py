# host/tests/hil/test_network.py
import pytest

from etrx import PASSWORD, Announce, EtrxError, Jpan, LeftPan, NewNode, NodeLeft

pytestmark = pytest.mark.hil3

CH25 = "4000"  # S00 mask with only channel 25


@pytest.fixture
def nodes(dks):
    for e in dks:
        e.factory_reset()
    yield dks
    for e in dks:
        e.factory_reset()


def form(c, epid=None):
    c.sreg_set(0x00, CH25)
    if epid:
        c.sreg_set(0x03, epid)
    c.sreg_set(0x0F, "0904")  # NODELEFT on
    return c.form()


def prepare_joiner(e, jpan, end_device=False):
    # Join only the network the test coordinator formed, even if another one is open nearby.
    e.sreg_set(0x00, CH25)
    e.sreg_set(0x03, jpan.epid)
    e.sreg_bit_set(0x0A, 0xA, True, PASSWORD)  # coordinator is a legacy TC
    e.sreg_bit_set(0x0A, 0xF, end_device, PASSWORD)


def test_out_of_network_after_factory_reset(nodes):
    for e in nodes:
        assert e.network() is None
        with pytest.raises(EtrxError) as exc:
            e.cmd("AT+DASSL")
        assert exc.value.code == 0x93


def test_form_and_join_router_and_end_device(nodes):
    c, b, s = nodes
    j = form(c)
    assert j.channel == 25
    assert c.network().role == "COO"
    mark = c.mark()

    prepare_joiner(b, j)
    jb = b.join()
    assert (jb.pan, jb.epid) == (j.pan, j.epid)
    assert b.network().role == "FFD"

    prepare_joiner(s, j, end_device=True)
    s.join()
    assert s.network().role == "ZED"

    b_eui, s_eui = b.info()[2], s.info()[2]
    c.wait_for(lambda ev: isinstance(ev, NewNode) and ev.eui == b_eui, 15, since=mark)
    c.wait_for(lambda ev: isinstance(ev, Announce) and ev.eui == b_eui and ev.kind == "FFD", 15, since=mark)
    c.wait_for(lambda ev: isinstance(ev, Announce) and ev.eui == s_eui and ev.kind == "ZED", 15, since=mark)


def test_second_en_or_jn_when_in_network_is_error_28(nodes):
    c, b, _ = nodes
    form(c)
    for cmd in ("AT+EN", "AT+JN"):
        with pytest.raises(EtrxError) as exc:
            c.cmd(cmd)
        assert exc.value.code == 0x28


def test_join_fails_without_network(nodes):
    _, b, _ = nodes
    b.sreg_set(0x00, "0001")  # channel 11 only, nobody there
    with pytest.raises(EtrxError) as exc:
        b.join(timeout=45)
    assert exc.value.code in (0x94, 0x27)


def test_join_retry_after_failure(nodes):
    # Review focus 2: the second attempt goes through a silent reboot.
    c, b, _ = nodes
    b.sreg_set(0x00, "0001")
    with pytest.raises(EtrxError):
        b.join(timeout=45)
    j = form(c)
    prepare_joiner(b, j)
    assert b.join(timeout=60).epid == j.epid


def test_jpan_with_epid(nodes):
    c, b, _ = nodes
    form(c, epid="00000000000ABCDE")
    b.sreg_bit_set(0x0A, 0xA, True, PASSWORD)
    j = b.join(channel=25, pan_or_epid="00000000000ABCDE")
    assert j.epid == "00000000000ABCDE"


def test_network_survives_reset(nodes):
    c, b, s = nodes
    j = form(c)
    prepare_joiner(b, j)
    b.join()
    prepare_joiner(s, j, end_device=True)
    s.join()
    for e in (b, s, c):
        mark = e.mark()
        e.cmd("ATZ")
        e.wait_for(lambda ev: isinstance(ev, Jpan), 30, since=mark)
        e.wait_ready()
        assert e.network().pan == j.pan


def test_dassl_leaves_and_coordinator_sees_it(nodes):
    c, b, _ = nodes
    j = form(c)
    prepare_joiner(b, j)
    b.join()
    b_eui = b.info()[2]
    mark_b, mark_c = b.mark(), c.mark()
    b.cmd("AT+DASSL", timeout=10)
    b.wait_for(lambda ev: isinstance(ev, LeftPan), 5, since=mark_b)
    b.wait_ready()
    assert b.network() is None
    c.wait_for(lambda ev: isinstance(ev, NodeLeft) and ev.eui == b_eui, 15, since=mark_c)


def test_network_info_format(nodes):
    c, _, _ = nodes
    c.sreg_set(0x01, "-4")
    j = form(c)
    info = c.network()
    assert (info.role, info.channel, info.power, info.pan, info.epid) == ("COO", 25, -4, j.pan, j.epid)


def test_messaging_needs_a_network(nodes):
    c, _, _ = nodes
    for cmd in ("AT+UCAST:0000=hi", "AT+BCAST:00,hi"):
        with pytest.raises(EtrxError) as exc:
            c.cmd(cmd)
        assert exc.value.code == 0x93


def test_failed_join_does_not_retry_or_reboot(nodes):
    # Review I-1: after a failed AT+JN the stack must not keep retrying on its own
    # (spec 4.2); a later automatic join/leave used to print LeftPAN and reboot the node.
    import time

    c, b, _ = nodes
    j = form(c)
    b.sreg_set(0x00, CH25)  # no S0A bit A: the legacy TC never answers the TCLK request
    b.sreg_set(0x03, j.epid)
    with pytest.raises(EtrxError):
        b.join(timeout=60)
    mark = b.mark()
    time.sleep(60)
    with pytest.raises(TimeoutError):
        b.wait_for(lambda ev: isinstance(ev, (LeftPan, Jpan)), 0.01, since=mark)
    assert b.network() is None
