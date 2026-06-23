"""
python_functions/pump.py
========================
THIS FILE = the Python wrapper layer for a Chemyx Fusion 4000X syringe pump.
It uses pyserial to send plain ASCII commands (terminated with '\\r') and reads
the pump's echo back to verify each value was accepted.

Every method's comment shows the EXACT plain-text command it puts on the wire,
so this directory lines up 1:1 with ../plain_text_commands/ (the same commands
with no Python around them).

Dual channel: the Fusion 4000X has two pump drives. A command can be addressed
to one drive by prefixing the channel number, e.g.  "2 set rate 5.0".
CHANNEL = 0 means default/both and sends no prefix.

Run an example with no hardware by passing mock=True (see the example_*.py
scripts in this folder), or talk to a real pump by editing the config below.
"""

# =============================================================================
#  CONFIG  —  EDIT THESE FOR YOUR SETUP
# =============================================================================
PORT = "COM3"          # Windows: "COM3"  |  Mac: "/dev/tty.usbserial-XXXX"  |  Linux: "/dev/ttyUSB0"
BAUD_RATE = 9600       # MUST match the baud rate shown on the pump's screen (often 9600 or 38400)
CHANNEL = 0            # 0 = default/both, 1 = channel 1, 2 = channel 2 (4000X is dual-channel)
TIMEOUT = 1.0          # seconds to wait for a reply
# =============================================================================

import re

import serial  # pyserial:  pip install pyserial


# --- Flow-rate units ---------------------------------------------------------
# Chemyx "set units [x]" takes an integer code:
UNITS = {0: "mL/min", 1: "mL/hr", 2: "uL/min", 3: "uL/hr"}
_UNITS_BY_NAME = {
    "ml/min": 0, "ml/hr": 1, "ul/min": 2, "ul/hr": 3,
    "µl/min": 2, "µl/hr": 3, "μl/min": 2, "μl/hr": 3,
}

# --- Hardware limits (Fusion 4000X) ------------------------------------------
DIAMETER_MIN, DIAMETER_MAX = 0.103, 40.000          # mm
RATE_MAX = {0: 170.5, 1: 10230.0, 2: 170500.0, 3: 10230000.0}  # per units code


# --- Exceptions --------------------------------------------------------------
class PumpConnectionError(Exception):
    """Port could not be opened / is not connected (with a friendly message)."""


class EchoMismatchError(Exception):
    """The pump echoed a different value than we sent -> it was out of range."""


def _resolve_units(units):
    """Accept a units code (0-3) or a string ('mL/min') and return the code."""
    if isinstance(units, int):
        if units not in UNITS:
            raise ValueError(f"Bad units code {units}; valid: {list(UNITS)}")
        return units
    key = str(units).strip().lower()
    if key not in _UNITS_BY_NAME:
        raise ValueError(f"Bad units '{units}'; try {sorted(set(UNITS.values()))}")
    return _UNITS_BY_NAME[key]


class Pump:
    """A reusable wrapper around the serial link to a Chemyx Fusion 4000X."""

    def __init__(self, port=PORT, baud_rate=BAUD_RATE, channel=CHANNEL,
                 units=0, timeout=TIMEOUT, mock=False, verify=True):
        self.port = port
        self.baud_rate = baud_rate
        self.channel = channel
        self.units = _resolve_units(units)
        self.timeout = timeout
        self.mock = mock          # True -> use the built-in emulator (no hardware)
        self.verify = verify      # True -> check the echo after every "set"
        self.ser = None

    # -- connection -----------------------------------------------------------
    def connect(self):
        """Open the serial port (8 data bits, no parity, 1 stop bit)."""
        try:
            if self.mock:
                self.ser = _MockSerial(self.port, self.baud_rate, self.timeout)
            else:
                self.ser = serial.Serial(
                    port=self.port, baudrate=self.baud_rate,
                    bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE, timeout=self.timeout,
                )
        except (serial.SerialException, OSError) as exc:
            raise PumpConnectionError(self._friendly(exc)) from exc
        return self

    def disconnect(self):
        """Close the port if open. Safe to call more than once."""
        if self.ser is not None:
            try:
                if getattr(self.ser, "is_open", False):
                    self.ser.close()
            finally:
                self.ser = None

    @property
    def is_connected(self):
        return self.ser is not None and getattr(self.ser, "is_open", False)

    def _friendly(self, exc):
        t = str(exc).lower()
        if "permission" in t or "access is denied" in t or "denied" in t:
            return (f"Access denied opening {self.port} — it's probably already open "
                    f"in another program (close the Chemyx GUI / PuTTY). ({exc})")
        if "could not open" in t or "filenotfound" in t or "no such file" in t:
            return (f"Port {self.port} not found — check the cable and that PORT "
                    f"matches Device Manager / `ls /dev/tty.*`. ({exc})")
        return f"Could not open {self.port}: {exc}"

    # -- context manager: the port ALWAYS closes ------------------------------
    def __enter__(self):
        return self.connect()

    def __exit__(self, *exc):
        self.disconnect()
        return False

    # -- low-level send -------------------------------------------------------
    def _with_channel(self, command, channel=None):
        ch = self.channel if channel is None else channel
        return command if ch in (0, None) else f"{ch} {command}"

    def send_command(self, command, channel=None):
        """Append '\\r', write the command, then read & return the pump's reply.

        Plain-text equivalent:  <command>\\r
        """
        if not self.is_connected:
            raise PumpConnectionError("Not connected — call connect() first.")
        full = self._with_channel(command, channel)
        self.ser.reset_input_buffer()
        self.ser.write((full + "\r").encode("ascii"))   # <-- the '\r' terminator
        self.ser.flush()
        # read whatever comes back within one timeout window
        chunks = []
        while True:
            chunk = self.ser.read(256)
            if not chunk:
                break
            chunks.append(chunk)
            if len(chunk) < 256:
                break
        return b"".join(chunks).decode("ascii", errors="replace").strip()

    def _verify(self, response, keyword, expected, tol=1e-4):
        """Raise EchoMismatchError unless the pump echoed `keyword = expected`."""
        if not self.verify:
            return
        m = re.search(rf"{keyword}\s*=\s*([-+]?\d*\.?\d+)", response, re.IGNORECASE)
        if m is None:
            raise EchoMismatchError(f"No '{keyword} = ...' echo in: {response!r}")
        if abs(float(m.group(1)) - float(expected)) > tol:
            raise EchoMismatchError(
                f"{keyword}: sent {expected} but pump reported {m.group(1)} "
                f"(out of range; command did NOT take)."
            )

    # -- settings -------------------------------------------------------------
    def set_units(self, units, channel=None):
        """Plain-text:  set units [x]   (0=mL/min 1=mL/hr 2=uL/min 3=uL/hr)
        e.g.  set units 0      echo ->  units = mL/min
        """
        code = _resolve_units(units)
        resp = self.send_command(f"set units {code}", channel)
        self.units = code
        if self.verify and UNITS[code].lower() not in resp.lower():
            raise EchoMismatchError(f"units: sent {UNITS[code]} but got {resp!r}")
        return resp

    def set_diameter(self, diameter, channel=None):
        """Plain-text:  set diameter [x.x]
        e.g.  set diameter 4.5     echo ->  diameter = 4.5     (range 0.103-40.000 mm)
        """
        diameter = float(diameter)
        if not (DIAMETER_MIN <= diameter <= DIAMETER_MAX):
            raise ValueError(f"Diameter {diameter} out of range "
                             f"[{DIAMETER_MIN}, {DIAMETER_MAX}] mm")
        resp = self.send_command(f"set diameter {diameter}", channel)
        self._verify(resp, "diameter", diameter)
        return resp

    def set_rate(self, rate, channel=None):
        """Plain-text:  set rate [x.x]
        e.g.  set rate 1.0     echo ->  rate = 1.0      (up to ~170.5 mL/min)
        """
        rate = float(rate)
        if not (0 < rate <= RATE_MAX[self.units]):
            raise ValueError(f"Rate {rate} {UNITS[self.units]} out of range "
                             f"(0, {RATE_MAX[self.units]}]")
        resp = self.send_command(f"set rate {rate}", channel)
        self._verify(resp, "rate", rate)
        return resp

    def set_volume(self, volume, channel=None):
        """Plain-text:  set volume [x.x]   POSITIVE = infuse, NEGATIVE = withdraw
        e.g.  set volume 0.5    echo ->  volume = 0.5    (infuse)
              set volume -0.5   echo ->  volume = -0.5   (withdraw)
        """
        volume = float(volume)
        if volume == 0:
            raise ValueError("Volume must be non-zero (its sign sets direction).")
        resp = self.send_command(f"set volume {volume}", channel)
        self._verify(resp, "volume", volume)
        return resp

    # -- movement -------------------------------------------------------------
    def start(self, channel=None):
        """Plain-text:  start"""
        return self.send_command("start", channel)

    def stop(self, channel=None):
        """Plain-text:  stop"""
        return self.send_command("stop", channel)

    def pause(self, channel=None):
        """Plain-text:  pause"""
        return self.send_command("pause", channel)

    def infuse(self, volume, rate=None, diameter=None, units=None, channel=None):
        """Full INFUSE sequence -> POSITIVE volume then start.

        Plain-text equivalent (with the optional set-up lines):
            set units 0
            set diameter 4.5
            set rate 1.0
            set volume 0.5        <-- positive
            start
        """
        if units is not None:
            self.set_units(units, channel)
        if diameter is not None:
            self.set_diameter(diameter, channel)
        if rate is not None:
            self.set_rate(rate, channel)
        self.set_volume(abs(float(volume)), channel)    # positive -> infuse
        return self.start(channel)

    def withdraw(self, volume, rate=None, diameter=None, units=None, channel=None):
        """Full WITHDRAW sequence -> NEGATIVE volume then start.

        Plain-text equivalent (with the optional set-up lines):
            set units 0
            set diameter 4.5
            set rate 1.0
            set volume -0.5       <-- negative
            start
        """
        if units is not None:
            self.set_units(units, channel)
        if diameter is not None:
            self.set_diameter(diameter, channel)
        if rate is not None:
            self.set_rate(rate, channel)
        self.set_volume(-abs(float(volume)), channel)   # negative -> withdraw
        return self.start(channel)

    # -- utility --------------------------------------------------------------
    def help(self):
        """Plain-text:  help   -> returns the pump's built-in command list."""
        return self.send_command("help")


# =============================================================================
#  Built-in emulator so the example_*.py scripts run with NO hardware.
#  (This is a Python helper for dry-runs; it is NOT a pump command.)
# =============================================================================
class _MockSerial:
    """Minimal fake that echoes values back the way a real 4000X does."""

    def __init__(self, port=None, baudrate=9600, timeout=1.0):
        self.port, self.baudrate, self.timeout = port, baudrate, timeout
        self.is_open = True
        self._rx = bytearray()

    def write(self, data):
        for line in data.decode("ascii", "replace").replace("\n", "\r").split("\r"):
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if parts and len(parts[0]) == 1 and parts[0] in "1234":
                parts = parts[1:]           # drop channel prefix
            if len(parts) >= 3 and parts[0] == "set":
                key, val = parts[1], parts[2]
                if key == "units":
                    self._reply(f"units = {UNITS.get(int(val), val)}")
                else:
                    self._reply(f"{key} = {val}")
            elif parts and parts[0] in ("start", "stop", "pause"):
                self._reply(f"pump {parts[0]}")
            elif parts and parts[0] == "help":
                self._reply("Commands: start | stop | pause | set units [x] | "
                            "set diameter [x.x] | set rate [x.x] | set volume [x.x] "
                            "| set delay [xxx] | set primerate [x.x] | echo on | "
                            "echo off | help")
            else:
                self._reply(" ".join(parts))
        return len(data)

    def _reply(self, text):
        self._rx.extend((text + "\r\n").encode("ascii"))

    def read(self, size=1):
        chunk = bytes(self._rx[:size])
        del self._rx[:size]
        return chunk

    def reset_input_buffer(self):
        self._rx.clear()

    def flush(self):
        pass

    def close(self):
        self.is_open = False
