"""Serial transport and line splitting for the AT firmware."""
from __future__ import annotations


class LineReader:
    """Splits the byte stream from the module into text lines.

    Lines end with <LF>; <CR> is dropped. A bare '>' (the binary-entry prompt of
    AT+SENDUCASTB) is reported at once because no <CR><LF> follows it. Bytes are
    decoded as latin-1 so raw payload bytes survive unchanged.
    """

    def __init__(self):
        self._buf = bytearray()

    def feed(self, data: bytes) -> list[str]:
        lines = []
        for b in data:
            if b == 0x0A:
                lines.append(self._buf.decode("latin-1"))
                self._buf.clear()
            elif b == 0x0D:
                continue
            else:
                self._buf.append(b)
                if self._buf == b">":
                    lines.append(">")
                    self._buf.clear()
        return lines


class SerialTransport:
    """pyserial wrapper with the small interface Etrx needs."""

    def __init__(self, port: str, baudrate: int = 115200):
        import serial

        self._port = port
        self._s = serial.Serial(port, baudrate, timeout=0.05, write_timeout=2)

    def write(self, data: bytes):
        import serial

        try:
            self._s.write(data)
        except serial.SerialTimeoutException as exc:
            # The module is not reading its UART (e.g. another firmware is running).
            raise TimeoutError(f"write to {self._port} timed out") from exc

    def read(self) -> bytes:
        return self._s.read(self._s.in_waiting or 1)

    def close(self):
        self._s.close()
