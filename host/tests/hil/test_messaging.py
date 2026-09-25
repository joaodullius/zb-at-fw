# host/tests/hil/test_messaging.py
import time

import pytest

from etrx import (END_DEVICE, ROUTER, S0F_APP, S0F_COORDINATOR, Ack, EtrxError, Nack,
                  NetworkOptions, Rx, Text, configure_endpoint2, ensure_coordinator,
                  ensure_joined, wait_network)

pytestmark = pytest.mark.hil3


@pytest.fixture(scope="module")
def net(request):
    from etrx.protocol import Etrx

    ports = [request.config.getoption(o) for o in ("--coord-port", "--bulb-port", "--switch-port")]
    if not all(ports):
        pytest.skip("needs three ports")
    c, b, s = (Etrx.open(p, name=n) for p, n in zip(ports, ("coord", "bulb", "switch")))
    info = ensure_coordinator(c, NetworkOptions(channel=25, reset=True), S0F_COORDINATOR)
    joiner = NetworkOptions(channel=info.channel, epid=info.epid, legacy=True, reset=True)
    ensure_joined(b, joiner, ROUTER, S0F_APP)            # bulb first ...
    if configure_endpoint2(b, 0x0104, 0x0100, [0x0000, 0x0003, 0x0006], []):
        wait_network(b)
    ensure_joined(s, joiner, END_DEVICE, S0F_APP)        # ... so the switch never saw its announce
    if configure_endpoint2(s, 0x0104, 0x0000, [0x0000, 0x0003], [0x0006]):
        wait_network(s)
    yield c, b, s, c.info()[2], b.info()[2], s.info()[2]
    for e in (c, b, s):
        e.factory_reset()
        e.close()


def send_and_check(sender, receiver, addr, text, sender_eui):
    mark_s, mark_r = sender.mark(), receiver.mark()
    seq = sender.ucast(addr, text)
    sender.wait_for(lambda ev: isinstance(ev, Ack) and ev.seq == seq, 10, since=mark_s)
    got = receiver.wait_for(lambda ev: isinstance(ev, Text) and ev.data == text.encode(), 10, since=mark_r)
    assert got.kind == "UCAST"
    assert got.eui in (None, sender_eui)


def test_ucast_coordinator_to_bulb_by_eui(net):
    c, b, s, c_eui, b_eui, s_eui = net
    send_and_check(c, b, b_eui, "hello bulb", c_eui)


def test_ucast_bulb_to_coordinator_by_short_address(net):
    c, b, s, c_eui, b_eui, s_eui = net
    send_and_check(b, c, "0000", "hello coordinator", b_eui)


def test_ucast_to_eui_learned_by_discovery(net):
    # Review focus 1: the switch joined after the bulb and never saw its announce.
    c, b, s, c_eui, b_eui, s_eui = net
    send_and_check(s, b, b_eui, "found you", s_eui)


def test_text_with_separators_arrives_intact(net):
    c, b, s, c_eui, b_eui, s_eui = net
    send_and_check(c, b, b_eui, "a,b=c:d,-1", c_eui)


def test_bcast_reaches_router_but_not_end_device(net):
    c, b, s, *_ = net
    mark_b, mark_s = b.mark(), s.mark()
    c.bcast("to all routers")
    got = b.wait_for(lambda ev: isinstance(ev, Text) and ev.data == b"to all routers", 10, since=mark_b)
    assert got.kind == "BCAST"
    with pytest.raises(TimeoutError):
        s.wait_for(lambda ev: isinstance(ev, Text) and ev.data == b"to all routers", 3, since=mark_s)


def test_senducastb_zcl_frame_shows_as_rx_on_endpoint_2(net):
    c, b, s, c_eui, b_eui, s_eui = net
    mark = b.mark()
    s.senducastb(b_eui, 0x02, 0x02, 0x0104, 0x0006, bytes([0x01, 0x07, 0x02]))
    rx = b.wait_for(lambda ev: isinstance(ev, Rx) and ev.cluster == 0x0006, 10, since=mark)
    assert (rx.profile, rx.dst_ep, rx.src_ep, rx.payload) == (0x0104, 2, 2, bytes([1, 7, 2]))


def test_senducast_text_to_endpoint_1(net):
    c, b, s, c_eui, b_eui, s_eui = net
    mark = b.mark()
    c.senducast(b_eui, 0x01, 0x01, 0xC091, 0x0002, "raw text")
    b.wait_for(lambda ev: isinstance(ev, Text) and ev.data == b"raw text", 10, since=mark)


def test_rx_hidden_with_factory_prompt_settings(net):
    c, b, s, c_eui, b_eui, s_eui = net
    b.sreg_set(0x0F, "0006")
    try:
        mark = b.mark()
        s.senducastb(b_eui, 0x02, 0x02, 0x0104, 0x0006, bytes([0x01, 0x08, 0x02]))
        with pytest.raises(TimeoutError):
            b.wait_for(lambda ev: isinstance(ev, Rx), 3, since=mark)
    finally:
        b.sreg_set(0x0F, f"{S0F_APP:04X}")


def test_nack_for_absent_short_address(net):
    c, *_ = net
    mark = c.mark()
    seq = c.ucast("1234", "nobody")
    # ZBOSS gives up on an unknown destination after about 60 s (route discovery + APS retries).
    c.wait_for(lambda ev: isinstance(ev, Nack) and ev.seq == seq, 90, since=mark)


def test_unknown_eui_is_error_06(net):
    c, *_ = net
    t0 = time.monotonic()
    with pytest.raises(EtrxError) as exc:
        c.ucast("0011223344556677", "nobody")
    assert exc.value.code == 0x06
    assert time.monotonic() - t0 >= 4


def test_too_long_is_error_74(net):
    c, b, s, c_eui, b_eui, s_eui = net
    with pytest.raises(EtrxError) as exc:
        c.ucast(b_eui, "x" * 83)
    assert exc.value.code == 0x74
