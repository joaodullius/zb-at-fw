# host/tests/test_port.py
from etrx.port import LineReader


def test_splits_crlf_lines_and_drops_cr():
    r = LineReader()
    assert r.feed(b"\r\nOK\r\n") == ["", "OK"]
    assert r.feed(b"\r\nSEQ:0") == [""]
    assert r.feed(b"1\r\n") == ["SEQ:01"]


def test_binary_prompt_is_reported_without_newline():
    r = LineReader()
    assert r.feed(b"\r\n>") == ["", ">"]


def test_latin1_bytes_survive():
    r = LineReader()
    assert r.feed(b"RX:\xff\x00\n") == ["RX:\xff\x00"]


def test_serial_write_timeout_becomes_timeout_error(monkeypatch):
    # A module that does not read its UART must make commands fail, not hang forever.
    import serial

    from etrx.port import SerialTransport

    class StuckSerial:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        def write(self, data):
            raise serial.SerialTimeoutException("Write timeout")

    monkeypatch.setattr(serial, "Serial", StuckSerial)
    t = SerialTransport("COM99")
    assert t._s.kwargs.get("write_timeout")
    import pytest
    with pytest.raises(TimeoutError):
        t.write(b"AT\r")
