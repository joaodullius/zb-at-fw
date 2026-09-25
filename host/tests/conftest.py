# host/tests/conftest.py
import os
import queue
import threading
import time

import pytest


class FakeModule:
    """Transport double that behaves like the AT firmware on the other end of the UART."""

    def __init__(self, responder, echo=True):
        self._responder = responder
        self._echo = echo
        self._out = queue.Queue()
        self._line = bytearray()
        self._binary_need = 0
        self._binary_cmd = None
        self._binary_data = bytearray()
        self.written = []
        self.binary_payloads = []

    # transport API -------------------------------------------------------
    def write(self, data: bytes):
        self.written.append(bytes(data))
        for b in data:
            if self._binary_need:
                self._binary_data.append(b)
                self._binary_need -= 1
                if self._binary_need == 0:
                    self.binary_payloads.append(bytes(self._binary_data))
                    self._emit(self._responder(self._binary_cmd + "<data>"))
                continue
            if b == 0x0D:
                cmd = self._line.decode("latin-1")
                self._line.clear()
                if self._echo:
                    self._out.put(cmd.encode("latin-1") + b"\r")
                lines = self._responder(cmd)
                if lines and lines[0] == ">":
                    self._binary_cmd = cmd
                    self._binary_need = int(cmd.split(":")[1].split(",")[0], 16)
                    self._binary_data = bytearray()
                    self._out.put(b"\r\n>")
                else:
                    self._emit(lines)
            else:
                self._line.append(b)

    def read(self) -> bytes:
        try:
            return self._out.get(timeout=0.02)
        except queue.Empty:
            return b""

    def close(self):
        pass

    # test helpers --------------------------------------------------------
    def _emit(self, lines):
        for line in lines or []:
            self._out.put(b"\r\n" + line.encode("latin-1") + b"\r\n")

    def push(self, *lines):
        self._emit(list(lines))


@pytest.fixture
def fake_etrx():
    """Build an Etrx on a FakeModule; returns (etrx, module). Closed after the test."""
    from etrx.protocol import Etrx

    created = []

    def make(responder, echo=True):
        module = FakeModule(responder, echo=echo)
        etrx = Etrx(module, name="fake")
        created.append(etrx)
        return etrx, module

    yield make
    for e in created:
        e.close()


def pytest_addoption(parser):
    parser.addoption("--coord-port", default=os.environ.get("ZB_AT_COORD"))
    parser.addoption("--bulb-port", default=os.environ.get("ZB_AT_BULB"))
    parser.addoption("--switch-port", default=os.environ.get("ZB_AT_SWITCH"))


def _open(port, name):
    from etrx.protocol import Etrx

    e = Etrx.open(port, name=name, log=lambda s: print(s))
    e.wait_ready()
    return e


@pytest.fixture
def dk(request):
    port = request.config.getoption("--coord-port")
    if not port:
        pytest.skip("needs --coord-port")
    e = _open(port, "dk")
    yield e
    e.close()


@pytest.fixture
def dks(request):
    ports = [request.config.getoption(o) for o in ("--coord-port", "--bulb-port", "--switch-port")]
    if not all(ports):
        pytest.skip("needs --coord-port, --bulb-port and --switch-port")
    nodes = [_open(p, n) for p, n in zip(ports, ("coord", "bulb", "switch"))]
    yield nodes
    for e in nodes:
        e.close()
