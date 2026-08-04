"""Finite-time transport abstraction and pyserial implementation."""

from __future__ import annotations

import time
from typing import Callable, Protocol

from .errors import TransportError


class LineTransport(Protocol):
    @property
    def is_open(self) -> bool: ...
    def open(self) -> None: ...
    def close(self) -> None: ...
    def write_line(
        self,
        line: str,
        timeout_s: float,
        payload_written_callback: Callable[[], None] | None = None,
    ) -> None: ...
    def read_line(self, timeout_s: float) -> str | None: ...


class SerialTransport:
    """Newline serial I/O with bounded buffers and monotonic deadlines."""

    def __init__(
        self,
        port: str,
        baud_rate: int = 115200,
        *,
        read_timeout_s: float = 0.1,
        write_timeout_s: float = 1.0,
        max_line_bytes: int = 512,
        serial_factory=None,
    ) -> None:
        self.port = str(port)
        self.baud_rate = int(baud_rate)
        self.read_timeout_s = max(0.01, float(read_timeout_s))
        self.write_timeout_s = max(0.01, float(write_timeout_s))
        self.max_line_bytes = int(max_line_bytes)
        self.serial_factory = serial_factory
        self._serial = None
        self._buffer = bytearray()

    @property
    def is_open(self) -> bool:
        return self._serial is not None and bool(getattr(self._serial, "is_open", False))

    def open(self) -> None:
        if self.is_open:
            return
        try:
            if self.serial_factory is None:
                import serial

                factory = serial.Serial
            else:
                factory = self.serial_factory
            self._serial = factory(
                port=self.port,
                baudrate=self.baud_rate,
                timeout=self.read_timeout_s,
                write_timeout=self.write_timeout_s,
            )
        except Exception as exc:
            self._serial = None
            raise TransportError(f"Could not open Arduino port {self.port}: {exc}") from exc

    def close(self) -> None:
        serial_obj, self._serial = self._serial, None
        self._buffer.clear()
        if serial_obj is not None:
            try:
                serial_obj.close()
            except Exception as exc:
                raise TransportError(f"Could not close Arduino port {self.port}: {exc}") from exc

    def write_line(
        self,
        line: str,
        timeout_s: float,
        payload_written_callback: Callable[[], None] | None = None,
    ) -> None:
        if not self.is_open:
            raise TransportError("Arduino transport is not open")
        if "\n" in line or "\r" in line:
            raise TransportError("write_line accepts one unterminated ASCII line")
        try:
            payload = (line + "\n").encode("ascii")
        except UnicodeEncodeError as exc:
            raise TransportError("Arduino commands must be ASCII") from exc
        if len(payload) > self.max_line_bytes:
            raise TransportError("Arduino command exceeds bounded transport size")
        deadline = time.monotonic() + max(0.01, float(timeout_s))
        offset = 0
        while offset < len(payload):
            if time.monotonic() >= deadline:
                raise TransportError("Timed out writing Arduino command")
            try:
                written = self._serial.write(payload[offset:])
            except Exception as exc:
                raise TransportError(f"Arduino serial write failed: {exc}") from exc
            if not written:
                continue
            offset += int(written)
        # The newline-terminated command can execute once all bytes are queued;
        # mark it dispatched before the still-fallible flush audit step.
        if payload_written_callback is not None:
            payload_written_callback()
        try:
            self._serial.flush()
        except Exception as exc:
            raise TransportError(f"Arduino serial flush failed: {exc}") from exc

    def read_line(self, timeout_s: float) -> str | None:
        if not self.is_open:
            raise TransportError("Arduino transport is not open")
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            newline = self._buffer.find(b"\n")
            if newline >= 0:
                raw = bytes(self._buffer[: newline + 1])
                del self._buffer[: newline + 1]
                try:
                    return raw.decode("ascii").strip("\r\n")
                except UnicodeDecodeError as exc:
                    raise TransportError("Firmware returned non-ASCII bytes") from exc
            if len(self._buffer) >= self.max_line_bytes:
                self._buffer.clear()
                raise TransportError("Firmware line exceeded bounded transport size")
            if time.monotonic() >= deadline:
                return None
            try:
                chunk = self._serial.read(min(64, self.max_line_bytes - len(self._buffer)))
            except Exception as exc:
                raise TransportError(f"Arduino serial read failed: {exc}") from exc
            if chunk:
                self._buffer.extend(chunk)
