import pytest

import e2e_test

PORTS = ["--coord", "COM31", "--bulb", "COM36", "--switch", "COM7"]


def test_defaults():
    a = e2e_test.parse_args(PORTS)
    assert (a.channel, a.pan, a.epid, a.legacy_tc, a.soak) == (None, None, None, True, 60.0)


def test_fixed_network():
    a = e2e_test.parse_args(PORTS + ["--channel", "20", "--pan", "7A31", "--epid", "00000000000A1B2C",
                                     "--soak", "300"])
    assert (a.channel, a.pan, a.epid, a.soak) == (20, 0x7A31, "00000000000A1B2C", 300.0)


def test_no_product_specific_broadcast_option():
    # The broadcast step uses one generic text; there is no option to mimic a product.
    with pytest.raises(SystemExit):
        e2e_test.parse_args(PORTS + ["--coord-bcast", "text"])
