"""
pump.py — A reusable Pump class wrapping a serial connection to a Chemyx
Fusion 4000X (and compatible Chemyx pumps).

Design notes
------------
* All commands are plain ASCII terminated with a carriage return ('\\r').
* The pump ECHOES the value it set (e.g. "diameter = 4.5").  After every
  "set ..." command we read that echo back and verify it matches what we sent.
  A mismatch means the value was out of range and the command did NOT take, so
  we raise EchoMismatchError instead of silently continuing.
* The Fusion 4000X is dual-channel.  Commands can be addressed to a specific
  pump drive by prefixing the channel number ("2 set rate 5.0").  Channel 0
  means "default / both" and sends no prefix.
* Volume sign encodes direction:  POSITIVE volume = INFUSE, NEGATIVE = WITHDRAW.
* Use it as a context manager so the port always closes:

      with Pump() as p:
          p.set_diameter(4.5)
          p.infuse(0.5)

For testing / demos with no hardware, construct with mock=True (uses the
emulator in mock_serial.py) or inject your own serial-like object via
serial_factory.
"""

import re

import serial  # pyserial

import config
from mock_serial import MockChemyxSerial


# --- Exceptions --------------------------------------------------------------
class PumpError(Exception):
    """Base class for all pump errors."""


class PumpConnectionError(PumpError):
    """Raised when the serial port cannot be opened or is not connected."""


class EchoMismatchError(PumpError):
    """Raised when the pump's echoed value differs from what we sent
    (typically because the value was out of range)."""


# Map common pyparity/parity/stopbits config strings to pyserial constants.
_PARITY = {
    "N": serial.PARITY_NONE,
    "E": serial.PARITY_EVEN,
    "O": serial.PARITY_ODD,
}
_BYTESIZE = {
    5: serial.FIVEBITS, 6: serial.SIXBITS,
    7: serial.SEVENBITS, 8: serial.EIGHTBITS,
}
_STOPBITS = {
    1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO,
}


class Pump:
    """Controls a Chemyx Fusion 4000X over a serial connection."""

    def __init__(self, port=None, baud_rate=None, channel=None, units=None,
                 timeout=None, mock=False, serial_factory=None, verify=True):
        self.port = port if port is not None else config.PORT
        self.baud_rate = baud_rate if baud_rate is not None else config.BAUD_RATE
        self.channel = channel if channel is not None else config.CHANNEL
        self.units = config.resolve_units(
            units if units is not None else config.DEFAULT_UNITS
        )
        self.timeout = timeout if timeout is not None else config.TIMEOUT

        # mock=True -> use the built-in emulator.  serial_factory lets tests
        # inject any serial-like object/callable.  Otherwise use real pyserial.
        self.mock = mock
        self._serial_factory = serial_factory

        # verify=True enforces echo verification on "set" commands.
        self.verify = verify

        self.ser = None  # the underlying serial object once connected

        # Last known parameters (updated as we set them).
        self.diameter = None
        self.rate = None
        self.volume = None

    # -- connection management ------------------------------------------------
    def connect(self):
        """Open the serial port.  Returns self so it can be chained."""
        try:
            if self._serial_factory is not None:
                self.ser = self._serial_factory(
                    port=self.port, baudrate=self.baud_rate,
                    bytesize=_BYTESIZE[config.BYTESIZE],
                    parity=_PARITY[config.PARITY],
                    stopbits=_STOPBITS[config.STOPBITS],
                    timeout=self.timeout,
                )
            elif self.mock:
                self.ser = MockChemyxSerial(
                    port=self.port, baudrate=self.baud_rate, timeout=self.timeout
                )
            else:
                self.ser = serial.Serial(
                    port=self.port, baudrate=self.baud_rate,
                    bytesize=_BYTESIZE[config.BYTESIZE],
                    parity=_PARITY[config.PARITY],
                    stopbits=_STOPBITS[config.STOPBITS],
                    timeout=self.timeout,
                )
        except serial.SerialException as exc:
            raise PumpConnectionError(self._friendly_error(exc)) from exc
        except (OSError, ValueError) as exc:
            # OSError covers some platform-specific open failures.
            raise PumpConnectionError(self._friendly_error(exc)) from exc
        return self

    def disconnect(self):
        """Close the serial port if it is open.  Safe to call repeatedly."""
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
                f"Access denied opening {self.port}. The port is probably already "
                f"open in another program (close the Chemyx GUI or any other serial "
                f"monitor) — original error: {exc}"
            )
        if ("could not open" in text or "filenotfound" in text
                or "no such file" in text or "cannot find" in text):
            return (
                f"Port {self.port} not found. Check the cable is plugged in and that "
                f"PORT in config.py matches Device Manager / `ls /dev/tty.*` — "
                f"original error: {exc}"
            )
        return f"Could not open {self.port}: {exc}"

    # -- context manager ------------------------------------------------------
    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False  # never suppress exceptions

    # -- low-level command I/O ------------------------------------------------
    def _with_channel(self, command, channel=None):
        """Prefix the command with a channel address when needed."""
        ch = self.channel if channel is None else channel
        if ch in (0, None):
            return command
        return f"{ch} {command}"

    def send_command(self, command, channel=None):
        """Send one command (a carriage return is appended) and return the
        pump's decoded text response (stripped of whitespace)."""
        if not self.is_connected:
            raise PumpConnectionError("Not connected — call connect() first.")
        full = self._with_channel(command, channel)
        payload = (full + config.COMMAND_TERMINATOR).encode("ascii")
        try:
            self.ser.reset_input_buffer()
            self.ser.write(payload)
            self.ser.flush()
        except serial.SerialException as exc:
            raise PumpConnectionError(self._friendly_error(exc)) from exc
        return self._read_response()

    def _read_response(self):
        """Read whatever the pump sends back within one timeout window."""
        chunks = []
        while True:
            chunk = self.ser.read(256)
            if not chunk:
                break
            chunks.append(chunk)
            if len(chunk) < 256:
                break
        return b"".join(chunks).decode("ascii", errors="replace").strip()

    # -- echo verification ----------------------------------------------------
    def _verify_numeric(self, response, keyword, expected, tol=1e-4):
        """Confirm the pump echoed `keyword = <expected>`.  Raise on mismatch."""
        if not self.verify:
            return
        match = re.search(
            rf"{keyword}\s*=\s*([-+]?\d*\.?\d+)", response, re.IGNORECASE
        )
        if match is None:
            raise EchoMismatchError(
                f"No '{keyword} = ...' echo found in pump response: {response!r}"
            )
        got = float(match.group(1))
        if abs(got - float(expected)) > tol:
            raise EchoMismatchError(
                f"{keyword}: sent {expected} but pump reported {got} "
                f"(value likely out of range; command did not take)."
            )

    # -- settings -------------------------------------------------------------
    def set_units(self, units, channel=None):
        """Set flow-rate units.  Accepts a code (0-3) or a string ('mL/min')."""
        code = config.resolve_units(units)
        response = self.send_command(f"set units {code}", channel)
        self.units = code
        # Units echo is a string ("units = mL/min"); verify the name appears.
        if self.verify:
            name = config.UNITS[code].lower().replace("µ", "u").replace("μ", "u")
            if name not in response.lower().replace("µ", "u").replace("μ", "u"):
                raise EchoMismatchError(
                    f"units: sent {config.UNITS[code]} but pump response was "
                    f"{response!r}"
                )
        return response

    def set_diameter(self, diameter, channel=None):
        """Set syringe inner diameter (mm).  Validated against the 4000X range."""
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
        """Set flow rate in the current units.  Validated against 4000X limits."""
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
        """Set target volume.  POSITIVE = infuse, NEGATIVE = withdraw."""
        volume = float(volume)
        if volume == 0:
            raise ValueError("Volume must be non-zero (sign sets direction).")
        response = self.send_command(f"set volume {volume}", channel)
        self._verify_numeric(response, "volume", volume)
        self.volume = volume
        return response

    def set_delay(self, delay, channel=None):
        """Set start delay (minutes)."""
        return self.send_command(f"set delay {delay}", channel)

    def set_primerate(self, primerate, channel=None):
        """Set prime rate."""
        return self.send_command(f"set primerate {primerate}", channel)

    def select_channel(self, channel):
        """Choose which pump drive subsequent commands address by default.
        0 = default/both, 1 = channel 1, 2 = channel 2."""
        if channel not in (0, 1, 2):
            raise ValueError("Channel must be 0 (default/both), 1, or 2.")
        self.channel = channel
        return channel

    # -- movement -------------------------------------------------------------
    def start(self, channel=None):
        """Begin the programmed move."""
        return self.send_command("start", channel)

    def stop(self, channel=None):
        """Stop the pump immediately."""
        return self.send_command("stop", channel)

    def pause(self, channel=None):
        """Pause the pump (resume with start)."""
        return self.send_command("pause", channel)

    def infuse(self, volume, rate=None, channel=None):
        """Set a POSITIVE volume (and optional rate) then start -> dispenses."""
        if rate is not None:
            self.set_rate(rate, channel)
        self.set_volume(abs(float(volume)), channel)  # positive -> infuse
        return self.start(channel)

    def withdraw(self, volume, rate=None, channel=None):
        """Set a NEGATIVE volume (and optional rate) then start -> aspirates."""
        if rate is not None:
            self.set_rate(rate, channel)
        self.set_volume(-abs(float(volume)), channel)  # negative -> withdraw
        return self.start(channel)

    # -- utility --------------------------------------------------------------
    def echo_on(self):
        return self.send_command("echo on")

    def echo_off(self):
        return self.send_command("echo off")

    def help(self):
        """Return the pump's built-in command help text."""
        return self.send_command("help")
