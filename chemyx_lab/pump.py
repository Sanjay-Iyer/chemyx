"""Reusable Chemyx Fusion 4000X serial wrapper."""

from __future__ import annotations

import re
import time

try:
    import serial
except ImportError:
    class _MissingSerial:
        PARITY_NONE = "N"
        PARITY_EVEN = "E"
        PARITY_ODD = "O"
        FIVEBITS = 5
        SIXBITS = 6
        SEVENBITS = 7
        EIGHTBITS = 8
        STOPBITS_ONE = 1
        STOPBITS_TWO = 2

        class SerialException(Exception):
            pass

        @staticmethod
        def Serial(*args, **kwargs):
            raise _MissingSerial.SerialException(
                "pyserial is required for real pump hardware. Install with "
                "python -m pip install -r requirements.txt."
            )

    serial = _MissingSerial()

from . import config
from .mock_serial import MockChemyxSerial


class PumpError(Exception):
    """Base class for pump errors."""


class PumpConnectionError(PumpError):
    """Raised when the serial port cannot be opened or used."""


class EchoMismatchError(PumpError):
    """Raised when the pump echo does not match the requested value."""


_PARITY = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
}
_BYTESIZE = {
    5: serial.FIVEBITS,
    6: serial.SIXBITS,
    7: serial.SEVENBITS,
    8: serial.EIGHTBITS,
}
_STOPBITS = {
    1: serial.STOPBITS_ONE,
    2: serial.STOPBITS_TWO,
}


class Pump:
    """Controls a Chemyx Fusion 4000X over pyserial."""

    def __init__(
        self,
        port=None,
        baud_rate=None,
        channel=None,
        units=None,
        timeout=None,
        response_delay=None,
        mock=False,
        serial_factory=None,
        verify=True,
    ):
        self.port = port if port is not None else config.PORT
        self.baud_rate = baud_rate if baud_rate is not None else config.BAUD_RATE
        self.channel = channel if channel is not None else config.CHANNEL
        self.units = config.resolve_units(
            units if units is not None else config.DEFAULT_UNITS
        )
        self.timeout = timeout if timeout is not None else config.TIMEOUT
        self.response_delay = (
            response_delay
            if response_delay is not None
            else config.RESPONSE_DELAY
        )
        self.mock = mock
        self._serial_factory = serial_factory
        self.verify = verify
        self.ser = None
        self.diameter = None
        self.rate = None
        self.volume = None

    def connect(self):
        """Open the configured serial port and return ``self``."""
        if not self.mock and self._serial_factory is None and not self.port:
            raise PumpConnectionError(
                "No Chemyx serial port configured. Set CHEMYX_PORT or create "
                "configs/chemyx.local.json from configs/chemyx.local.example.json."
            )

        try:
            kwargs = {
                "port": self.port,
                "baudrate": self.baud_rate,
                "bytesize": _BYTESIZE[config.BYTESIZE],
                "parity": _PARITY[config.PARITY],
                "stopbits": _STOPBITS[config.STOPBITS],
                "timeout": self.timeout,
            }
            if self._serial_factory is not None:
                self.ser = self._serial_factory(**kwargs)
            elif self.mock:
                self.ser = MockChemyxSerial(**kwargs)
            else:
                self.ser = serial.Serial(**kwargs)
        except serial.SerialException as exc:
            raise PumpConnectionError(self._friendly_error(exc)) from exc
        except (OSError, ValueError) as exc:
            raise PumpConnectionError(self._friendly_error(exc)) from exc
        return self

    def disconnect(self):
        """Close the serial port if it is open."""
        if self.ser is not None:
            try:
                if getattr(self.ser, "is_open", False):
                    self.ser.close()
            finally:
                self.ser = None

    @property
    def is_connected(self):
        return self.ser is not None and getattr(self.ser, "is_open", False)

    def _friendly_error(self, exc):
        text = str(exc).lower()
        if "permission" in text or "access is denied" in text or "denied" in text:
            return (
                f"Access denied opening {self.port}. The port is probably open "
                f"in another program. Close the Chemyx GUI, PuTTY, TeraTerm, "
                f"Arduino Serial Monitor, or any other serial monitor. "
                f"Original error: {exc}"
            )
        if (
            "could not open" in text
            or "filenotfound" in text
            or "no such file" in text
            or "cannot find" in text
        ):
            return (
                f"Port {self.port} not found. Check the cable, driver, and "
                f"CHEMYX_PORT/configs/chemyx.local.json setting. "
                f"Original error: {exc}"
            )
        return f"Could not open {self.port}: {exc}"

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def _with_channel(self, command, channel=None):
        ch = self.channel if channel is None else channel
        if ch in (0, None, ""):
            return command
        return f"{ch} {command}"

    def send_command(self, command, channel=None):
        """Send a command plus carriage return and return decoded response."""
        if not self.is_connected:
            raise PumpConnectionError("Not connected; call connect() first.")

        full = self._with_channel(command, channel)
        payload = (full + config.COMMAND_TERMINATOR).encode("ascii")
        try:
            if hasattr(self.ser, "reset_input_buffer"):
                self.ser.reset_input_buffer()
            if hasattr(self.ser, "reset_output_buffer"):
                self.ser.reset_output_buffer()
            self.ser.write(payload)
            if hasattr(self.ser, "flush"):
                self.ser.flush()
            if self.response_delay:
                time.sleep(float(self.response_delay))
        except serial.SerialException as exc:
            raise PumpConnectionError(self._friendly_error(exc)) from exc
        return self._read_response()

    def _read_response(self):
        chunks = []
        if hasattr(self.ser, "read_all"):
            raw = self.ser.read_all()
            if raw:
                chunks.append(raw)

        while True:
            chunk = self.ser.read(256)
            if not chunk:
                break
            chunks.append(chunk)
            if len(chunk) < 256:
                break
        return b"".join(chunks).decode("ascii", errors="replace").strip()

    def _verify_numeric(self, response, keyword, expected, tol=1e-4):
        if not self.verify:
            return
        match = re.search(rf"{keyword}\s*=\s*([-+]?\d*\.?\d+)", response, re.I)
        if match is None:
            raise EchoMismatchError(
                f"No '{keyword} = ...' echo found in pump response: {response!r}"
            )
        got = float(match.group(1))
        if abs(got - float(expected)) > tol:
            raise EchoMismatchError(
                f"{keyword}: sent {expected} but pump reported {got}. "
                f"The value was likely out of range and did not take."
            )

    def set_units(self, units, channel=None):
        code = config.resolve_units(units)
        response = self.send_command(f"set units {code}", channel)
        self.units = code
        if self.verify:
            name = config.UNITS[code].lower()
            match = re.search(r"units\s*=\s*([^\s\r\n>]+)", response, re.I)
            echoed = match.group(1).strip().lower() if match else ""
            if name not in response.lower() and echoed != str(code):
                raise EchoMismatchError(
                    f"units: sent {config.UNITS[code]} but response was {response!r}"
                )
        return response

    def set_diameter(self, diameter, channel=None):
        diameter = float(diameter)
        if not (config.DIAMETER_MIN <= diameter <= config.DIAMETER_MAX):
            raise ValueError(
                f"Diameter {diameter} mm is out of range "
                f"[{config.DIAMETER_MIN}, {config.DIAMETER_MAX}] mm."
            )
        response = self.send_command(f"set diameter {diameter}", channel)
        self._verify_numeric(response, "diameter", diameter)
        self.diameter = diameter
        return response

    def set_rate(self, rate, channel=None):
        rate = float(rate)
        lo, hi = config.rate_limits(self.units)
        if not (lo <= rate <= hi):
            raise ValueError(
                f"Rate {rate} {config.UNITS[self.units]} is out of range "
                f"[{lo}, {hi}] for those units."
            )
        response = self.send_command(f"set rate {rate}", channel)
        self._verify_numeric(response, "rate", rate)
        self.rate = rate
        return response

    def set_volume(self, volume, channel=None):
        volume = float(volume)
        if volume == 0:
            raise ValueError("Volume must be non-zero; sign sets direction.")
        response = self.send_command(f"set volume {volume}", channel)
        self._verify_numeric(response, "volume", volume)
        self.volume = volume
        return response

    def set_delay(self, delay, channel=None):
        return self.send_command(f"set delay {delay}", channel)

    def set_primerate(self, primerate, channel=None):
        return self.send_command(f"set primerate {primerate}", channel)

    def select_channel(self, channel):
        if channel not in (0, 1, 2):
            raise ValueError("Channel must be 0 (default), 1, or 2.")
        self.channel = channel
        return channel

    def start(self, channel=None, delay=None):
        """Start the pump. Pass ``delay=0`` to send the proven ``start 0`` form."""
        if delay is None:
            return self.send_command("start", channel)
        return self.send_command(f"start {delay}", channel)

    def stop(self, channel=None):
        return self.send_command("stop", channel)

    def pause(self, channel=None):
        return self.send_command("pause", channel)

    def infuse(self, volume, rate=None, channel=None, start_delay=None):
        if rate is not None:
            self.set_rate(rate, channel)
        self.set_volume(abs(float(volume)), channel)
        return self.start(channel=channel, delay=start_delay)

    def withdraw(self, volume, rate=None, channel=None, start_delay=None):
        if rate is not None:
            self.set_rate(rate, channel)
        self.set_volume(-abs(float(volume)), channel)
        return self.start(channel=channel, delay=start_delay)

    def echo_on(self):
        return self.send_command("echo on")

    def echo_off(self):
        return self.send_command("echo off")

    def help(self):
        return self.send_command("help")
