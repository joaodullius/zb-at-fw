"""Network security and join control: S09 / S0A bits 8, 4, 3, 5 and 0."""
import pytest

from etrx import PASSWORD, EtrxError, Jpan

pytestmark = pytest.mark.hil3

CH = "4000"  # S00 mask with only channel 25
KEY_A = "00112233445566778899AABBCCDDEEFF"
KEY_B = "FFEEDDCCBBAA99887766554433221100"


@pytest.fixture
def nodes(dks):
    for e in dks:
        e.factory_reset()
    yield dks
    for e in dks:
        e.factory_reset()


def set_s0a_bits(e, **bits):
    for bit, on in bits.items():
        e.sreg_bit_set(0x0A, int(bit[1:], 16), on, PASSWORD)


def form(c, key=None, **bits):
    c.sreg_set(0x00, CH)
    if key:
        c.sreg_set(0x09, key, PASSWORD)
        set_s0a_bits(c, b8=True)
    set_s0a_bits(c, **bits)
    return c.form()


def join(e, jpan, key=None, end_device=False, timeout=45):
    e.sreg_set(0x00, CH)
    e.sreg_set(0x03, jpan.epid)  # only the test network, even if another one is open
    e.sreg_bit_set(0x0A, 0xA, True, PASSWORD)  # the coordinator is a legacy TC
    e.sreg_bit_set(0x0A, 0xF, end_device, PASSWORD)
    if key:
        e.sreg_set(0x09, key, PASSWORD)
        set_s0a_bits(e, b8=True)
    return e.join(timeout=timeout)


def join_fails(e, jpan, **kw):
    with pytest.raises(EtrxError) as exc:
        join(e, jpan, **kw)
    assert exc.value.code in (0x94, 0x27)
    e.factory_reset()  # a failed attempt leaves the stack started; start clean


# --- S09 + S0A bit 8: preconfigured Trust Centre link key --------------------------

def test_same_custom_link_key_on_both_sides_joins(nodes):
    c, b, _ = nodes
    j = form(c, key=KEY_A, b4=True)
    assert join(b, j, key=KEY_A).epid == j.epid


def test_different_link_key_cannot_join_when_network_key_is_encrypted(nodes):
    c, b, _ = nodes
    j = form(c, key=KEY_A, b4=True)
    join_fails(b, j, key=KEY_B)
    join_fails(b, j)  # default ZigBeeAlliance09 key


def test_default_link_key_on_both_sides_joins(nodes):
    c, b, _ = nodes
    j = form(c, b4=True)
    assert join(b, j).epid == j.epid


# --- S0A bit 4: network key sent encrypted with the link key ------------------------

def test_network_key_in_clear_lets_a_node_with_another_link_key_join(nodes):
    # R309 default (bit 4 clear): the network key goes unencrypted, so the joiner's
    # link key does not matter.
    c, b, _ = nodes
    j = form(c, key=KEY_A, b4=False)
    assert join(b, j, key=KEY_B).epid == j.epid


# --- S0A bit 3: unsecured (TC) rejoin not allowed ------------------------------------

def test_secure_rejoin_still_works_with_unsecured_rejoin_blocked(nodes):
    # A TC rejoin cannot be forced from the AT interface; this checks that the setting
    # does not break the normal (secured) rejoin after a reset.
    c, b, _ = nodes
    j = form(c, b3=True, b4=True)
    join(b, j)
    mark = b.mark()
    b.cmd("ATZ")
    b.wait_for(lambda ev: isinstance(ev, Jpan) and ev.epid == j.epid, 30, since=mark)


# --- S0A bits 0 and 5: join control ---------------------------------------------------

def test_coordinator_bit0_blocks_joining_through_it(nodes):
    c, b, _ = nodes
    j = form(c, b0=True, b4=True)
    join_fails(b, j)


def test_router_lets_nodes_join_when_the_coordinator_blocks_direct_joins(nodes):
    c, b, s = nodes
    j = form(c, b4=True)
    join(b, j)                                   # router joins through the coordinator
    set_s0a_bits(c, b0=True)                     # then the coordinator stops accepting
    assert join(s, j, end_device=True).epid == j.epid   # so the switch joins via the router


def test_router_bit0_blocks_joining_through_it(nodes):
    c, b, s = nodes
    j = form(c, b4=True)
    join(b, j)
    set_s0a_bits(c, b0=True)
    set_s0a_bits(b, b0=True)                     # nobody accepts new nodes now
    join_fails(s, j, end_device=True)


def test_tc_bit5_blocks_joining_in_the_whole_network(nodes):
    c, b, s = nodes
    j = form(c, b4=True)
    join(b, j)
    set_s0a_bits(c, b5=True)                     # the router is still open locally
    join_fails(s, j, end_device=True)
