"""Command/response handling for one module running the AT firmware."""
from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass
from typing import Callable

from .port import LineReader, SerialTransport
from .prompts import Error, Jpan, MatchDesc, Ok, Seq, parse_prompt

PASSWORD = "password"

_N_RE = re.compile(r"^\+N=(\w+),(\d+),(-?\d+),([0-9A-F]{4}),([0-9A-F]{16})$")


class EtrxError(Exception):
    def __init__(self, code: int, command: str):
        super().__init__(f"{command}: ERROR:{code:02X}")
        self.code = code
        self.command = command


@dataclass(frozen=True)
class NetworkInfo:
    role: str  # COO, FFD, ZED, SED or MED
    channel: int
    power: int
    pan: int
    epid: str


class Etrx:
    """One module on one serial port.

    A reader thread splits lines; while a command is pending its lines go to the
    command, and every known prompt is also recorded in a history (for wait_for)
    and handed to listeners on a separate event thread, so listeners may call cmd().
    """

    def __init__(self, transport, name: str = "etrx", log: Callable[[str], None] | None = None):
        self.name = name
        self.rx_hex = True
        self._t = transport
        self._log = log or (lambda s: None)
        self._reader = LineReader()
        self._cmd_lock = threading.Lock()
        self._pending: str | None = None
        self._resp: queue.Queue[str] = queue.Queue()
        self._listeners: list[Callable] = []
        self._history: list = []
        self._cond = threading.Condition()
        self._events: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._rx_thread = threading.Thread(target=self._rx_loop, name=f"{name}-rx", daemon=True)
        self._ev_thread = threading.Thread(target=self._event_loop, name=f"{name}-ev", daemon=True)
        self._rx_thread.start()
        self._ev_thread.start()

    @classmethod
    def open(cls, port: str, baudrate: int = 115200, name: str | None = None, log=None) -> "Etrx":
        return cls(SerialTransport(port, baudrate), name or port, log)

    def close(self):
        self._stop.set()
        self._events.put(None)
        self._rx_thread.join(1)
        self._ev_thread.join(1)
        self._t.close()

    # events ----------------------------------------------------------------
    def add_listener(self, fn: Callable):
        self._listeners.append(fn)

    def mark(self) -> int:
        with self._cond:
            return len(self._history)

    def wait_for(self, pred: Callable, timeout: float, since: int | None = None):
        """Return the first prompt (from index `since`, default: now) that matches `pred`."""
        return self._wait_index(pred, timeout, since)[0]

    def _wait_index(self, pred: Callable, timeout: float, since: int | None):
        """Like wait_for, and also return the history index just after the match."""
        deadline = time.monotonic() + timeout
        with self._cond:
            i = len(self._history) if since is None else since
            while True:
                while i < len(self._history):
                    ev = self._history[i]
                    i += 1
                    if pred(ev):
                        return ev, i
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(f"{self.name}: no matching prompt within {timeout} s")
                self._cond.wait(remaining)

    def _rx_loop(self):
        while not self._stop.is_set():
            data = self._t.read()
            if data:
                for line in self._reader.feed(data):
                    self._on_line(line)

    def _on_line(self, line: str):
        if line == "":
            return
        self._log(f"{self.name} < {line}")
        pending = self._pending
        if pending is not None and line.upper() == pending.upper():
            return  # command echo
        prompt = parse_prompt(line, self.rx_hex)
        # Only response lines belong to the pending command. Other prompts (UCAST:, RX:, ACK:,
        # ...) can arrive at any time and must not be taken for a register value.
        if pending is not None and (prompt is None or isinstance(prompt, (Ok, Error, Seq, Jpan))):
            self._resp.put(line)
        if prompt is None or isinstance(prompt, (Ok, Error, Seq)):
            return
        with self._cond:
            self._history.append(prompt)
            self._cond.notify_all()
        self._events.put(prompt)

    def _event_loop(self):
        while True:
            ev = self._events.get()
            if ev is None:
                return
            for fn in list(self._listeners):
                try:
                    fn(ev)
                except Exception as exc:  # a broken listener must not stop the others
                    self._log(f"{self.name} listener error: {exc!r}")

    # commands --------------------------------------------------------------
    def _drain(self):
        while not self._resp.empty():
            self._resp.get_nowait()

    def _next_line(self, command: str, deadline: float) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"{self.name}: no OK/ERROR for {command!r}")
        try:
            return self._resp.get(timeout=remaining)
        except queue.Empty:
            raise TimeoutError(f"{self.name}: no OK/ERROR for {command!r}") from None

    def _collect(self, command: str, deadline: float) -> list[str]:
        lines = []
        while True:
            line = self._next_line(command, deadline)
            if line == "OK":
                return lines
            p = parse_prompt(line)
            if isinstance(p, Error):
                raise EtrxError(p.code, command)
            lines.append(line)

    def cmd(self, command: str, timeout: float = 2.0) -> list[str]:
        """Send one command; return the lines printed before OK. Raises EtrxError / TimeoutError."""
        with self._cmd_lock:
            self._drain()
            self._pending = command
            try:
                self._log(f"{self.name} > {command}")
                self._t.write(command.encode("latin-1") + b"\r")
                return self._collect(command, time.monotonic() + timeout)
            finally:
                self._pending = None

    def cmd_binary(self, command: str, data: bytes, timeout: float = 5.0) -> list[str]:
        """Send a command that answers with '>' and then expects len(data) raw bytes."""
        with self._cmd_lock:
            self._drain()
            self._pending = command
            try:
                self._log(f"{self.name} > {command} [{data.hex()}]")
                self._t.write(command.encode("latin-1") + b"\r")
                deadline = time.monotonic() + timeout
                while True:
                    line = self._next_line(command, deadline)
                    if line == ">":
                        break
                    p = parse_prompt(line)
                    if isinstance(p, Error):
                        raise EtrxError(p.code, command)
                self._t.write(data)
                return self._collect(command, deadline)
            finally:
                self._pending = None

    # helpers ---------------------------------------------------------------
    def wait_ready(self, timeout: float = 10.0):
        """Wait until the module answers 'AT' (after a reset or a silent reboot)."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                self.cmd("AT", timeout=0.5)
                return
            except TimeoutError:
                if time.monotonic() > deadline:
                    raise

    def _values(self, command: str) -> list[str]:
        """Response lines of a command, without prompts that arrived in between (e.g. JPAN)."""
        return [line for line in self.cmd(command) if parse_prompt(line, self.rx_hex) is None]

    def info(self) -> tuple[str, str, str]:
        lines = self._values("ATI")
        return lines[0], lines[1], lines[2]

    def sreg_get(self, reg: int) -> str:
        # An empty register (e.g. an empty cluster list) prints an empty line, which is skipped.
        lines = self._values(f"ATS{reg:02X}?")
        return lines[-1] if lines else ""

    def sreg_set(self, reg: int, value: str, password: str | None = None):
        self.cmd(f"ATS{reg:02X}={value}" + (f":{password}" if password else ""))

    def sreg_bit_set(self, reg: int, bit: int, on: bool, password: str | None = None):
        self.cmd(f"ATS{reg:02X}{bit:X}={1 if on else 0}" + (f":{password}" if password else ""))

    def reset(self, timeout: float = 10.0):
        self.cmd("ATZ")
        time.sleep(0.3)
        self.wait_ready(timeout)

    def factory_reset(self, timeout: float = 10.0):
        self.cmd("AT&F", timeout=timeout)
        time.sleep(0.3)
        self.wait_ready(timeout)

    def network(self) -> NetworkInfo | None:
        for line in self.cmd("AT+N"):
            if line == "+N=NoPAN":
                return None
            m = _N_RE.match(line)
            if m:
                return NetworkInfo(m[1], int(m[2]), int(m[3]), int(m[4], 16), m[5])
        raise EtrxError(0x05, "AT+N")

    @staticmethod
    def _jpan(lines: list[str], command: str) -> Jpan:
        for line in lines:
            p = parse_prompt(line)
            if isinstance(p, Jpan):
                return p
        raise EtrxError(0x05, command)

    def form(self, timeout: float = 25.0) -> Jpan:
        return self._jpan(self.cmd("AT+EN", timeout=timeout), "AT+EN")

    def join(self, timeout: float = 35.0, channel: int | None = None,
             pan_or_epid: str | None = None) -> Jpan:
        command = "AT+JN" if channel is None else f"AT+JPAN:{channel},{pan_or_epid}"
        return self._jpan(self.cmd(command, timeout=timeout), command)

    def leave(self, timeout: float = 10.0):
        self.cmd("AT+DASSL", timeout=timeout)
        time.sleep(0.3)
        self.wait_ready(timeout)

    @staticmethod
    def _seq(lines: list[str]) -> int:
        for line in lines:
            p = parse_prompt(line)
            if isinstance(p, Seq):
                return p.seq
        return -1

    def ucast(self, addr: str, text: str) -> int:
        return self._seq(self.cmd(f"AT+UCAST:{addr}={text}", timeout=8.0))

    def bcast(self, text: str, hops: int = 0):
        self.cmd(f"AT+BCAST:{hops:02d},{text}")

    def match(self, profile: int, in_clusters: list[int], out_clusters: list[int],
              timeout: float = 5.0) -> list[MatchDesc]:
        """AT+MATCHREQ: ask the network which nodes have an endpoint with this profile and
        at least one of these clusters. Returns the successful MatchDesc answers received
        within `timeout` seconds (one per answering node)."""
        def part(clusters: list[int]) -> str:
            return ",".join([f"{len(clusters):02X}", *(f"{c:04X}" for c in clusters)])

        mark = self.mark()
        self.cmd(f"AT+MATCHREQ:{profile:04X},{part(in_clusters)},{part(out_clusters)}")
        found: list[MatchDesc] = []
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return found
            try:
                ev, mark = self._wait_index(lambda e: isinstance(e, MatchDesc), remaining, mark)
            except TimeoutError:
                return found
            if ev.status == 0 and ev.endpoints:
                found.append(ev)

    def senducast(self, addr: str, src_ep: int, dst_ep: int, profile: int, cluster: int, text: str) -> int:
        return self._seq(self.cmd(
            f"AT+SENDUCAST:{addr},{src_ep:02X},{dst_ep:02X},{profile:04X},{cluster:04X},{text}",
            timeout=8.0))

    def senducastb(self, addr: str, src_ep: int, dst_ep: int, profile: int, cluster: int,
                   data: bytes) -> int:
        return self._seq(self.cmd_binary(
            f"AT+SENDUCASTB:{len(data):02X},{addr},{src_ep:02X},{dst_ep:02X},{profile:04X},{cluster:04X}",
            data, timeout=8.0))
