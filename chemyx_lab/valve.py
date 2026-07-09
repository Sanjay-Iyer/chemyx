"""IDEX/Rheodyne MX Series II valve driver over USB (FTDI virtual COM port).

Written for the valve actually on the bench: an **MXX777-601**, which is a
2-POSITION, 6-port switching valve. It has 6 fluidic ports but only TWO
selectable positions (1 and 2). "6-port" describes the plumbing, not the
number of selectable positions. The MX II driver board SILENTLY IGNORES a
position command for a position the valve does not have (no ack, no error,
no movement), so this driver validates every requested position against
``ports`` before anything touches the wire.

Protocol reference: IDEX doc 2321382G, "UART/USB Communication Protocol for
TitanEX/TitanEZ/TitanHP, TitanHT Driver Boards and MX Series II Modules"
(shipped in this repo at ``.codex-temp/idex-titan-uart/``; the same document
is in IDEX's MX Series II Driver Development Package, File-1418039677).
Summary of the wire protocol:

- 19200 baud default, 8N1, no handshaking, every packet ends with CR (0x0D).
- ``Pxx`` + CR moves the valve; xx is the position as TWO ASCII-hex digits
  (position 2 -> ``P02``). A recognized+executed command is acked with a
  bare CR; an invalid/ignored command gets NO response.
- ``M`` + CR homes the valve. ``S`` + CR returns the current position as two
  hex digits + CR, or an error code, or ``*`` (0x2A) to ANY input while the
  motor is moving.
- ``D`` reads / ``Fxx`` writes the command mode. For USB control the mode
  must be BCD (0x03), NOT level logic (0x01). A new mode written with ``F``
  only becomes active after the board is power-cycled.

The class name and method names (``MX_valve``, ``get_port``, ``change_port``,
``find_address``) intentionally match the linnarsson-lab MXII_valve library
so existing call sites keep working -- but here ``ports`` defaults to 2 and
means *selectable positions*.

The serial transport (port setup, framing, terminator, read loop) is lifted
verbatim from ``scripts/serial_hello_test.py``, which is proven working on
this hardware (home moves the motor).
"""

from __future__ import annotations

import logging
import re
import time

try:
    import serial
    from serial.tools import list_ports as _list_ports
except ImportError:  # keep the module importable for mock-only use
    _list_ports = None

    class _MissingSerial:
        EIGHTBITS = 8
        PARITY_NONE = "N"
        STOPBITS_ONE = 1

        class SerialException(Exception):
            pass

        @staticmethod
        def Serial(*args, **kwargs):
            raise _MissingSerial.SerialException(
                "pyserial is required for real valve hardware. Install with "
                "python -m pip install -r requirements.txt."
            )

    serial = _MissingSerial()

from . import config
from .mock_serial import MockMXValveSerial


LOGGER = logging.getLogger("chemyx_lab.valve")

TERMINATOR = b"\r"
BUSY = b"*"
HEX_RESPONSE = re.compile(rb"^[0-9A-Fa-f]{2}\r$")

# MX II modules use FTDI's own VID/PID (FT232R) -- IDEX has no USB vendor ID.
FTDI_VID = 0x0403
FTDI_PID = 0x6001

# Status codes returned by "S" (IDEX 2321382G). Anything else is a position.
ERROR_CODES = {
    0x63: "valve failure; valve cannot be homed (99 decimal)",
    0x58: "non-volatile memory error (88 decimal)",
    0x4D: "valve configuration error or command mode error (77 decimal)",
    0x42: "valve positioning error (66 decimal)",
    0x37: "data integrity error (55 decimal)",
    0x2C: "data CRC error (44 decimal)",
}

COMMAND_MODES = {
    0x01: "level logic",
    0x02: "single-pulse logic",
    0x03: "BCD logic",
    0x04: "inverted BCD logic",
    0x05: "dual-pulse logic",
}
BCD_MODE = 0x03


class ValveError(Exception):
    """Base class for MX valve errors."""


class ValveConnectionError(ValveError):
    """Raised when the serial port cannot be opened or used."""


class ValveTimeoutError(ValveError):
    """Raised when the valve does not reach the requested state in time."""


class ValveReportedError(ValveError):
    """Raised when the board itself returns one of the documented error codes."""

    def __init__(self, code: int):
        self.code = code
        description = ERROR_CODES.get(code, f"unrecognized error code 0x{code:02X}")
        super().__init__(f"valve reported error 0x{code:02X}: {description}")


def describe_com_port(info) -> str:
    """One-line summary of a pyserial ListPortInfo, including the FTDI id."""
    vid = f"{info.vid:04X}" if info.vid is not None else "----"
    pid = f"{info.pid:04X}" if info.pid is not None else "----"
    serial_no = info.serial_number or "?"
    return (
        f"{info.device:<8} vid:pid={vid}:{pid} serial={serial_no} "
        f"{info.description or ''}"
    )


def find_address(identifier: str | None = None, ports=None) -> str:
    """Return the COM/tty device of the MX II valve.

    With no ``identifier``, looks for exactly one FTDI FT232R device
    (VID:PID 0403:6001 -- the MX II uses FTDI's default ids). With an
    ``identifier``, returns the first port whose device name, description,
    hwid, or serial number contains it (case-insensitive).

    ``ports`` lets tests inject a fake port list.
    """
    if ports is None:
        if _list_ports is None:
            raise ValveConnectionError(
                "pyserial is required to scan serial ports. Install with "
                "python -m pip install -r requirements.txt."
            )
        ports = sorted(_list_ports.comports(), key=lambda item: item.device)

    if identifier:
        needle = identifier.strip().lower()
        for info in ports:
            haystack = " ".join(
                str(field)
                for field in (
                    info.device,
                    info.description,
                    info.hwid,
                    getattr(info, "serial_number", ""),
                )
                if field
            ).lower()
            if needle in haystack:
                return info.device
        raise ValveConnectionError(
            f"No serial port matched {identifier!r}. Visible ports:\n"
            + ("\n".join(describe_com_port(p) for p in ports) or "  (none)")
        )

    ftdi = [p for p in ports if p.vid == FTDI_VID and p.pid == FTDI_PID]
    if len(ftdi) == 1:
        return ftdi[0].device
    if not ftdi:
        raise ValveConnectionError(
            "No FTDI FT232R (vid:pid 0403:6001) serial port found. The MX II "
            "enumerates with FTDI's default ids; check the USB-B cable and the "
            "FTDI VCP driver, or pass the port explicitly. Visible ports:\n"
            + ("\n".join(describe_com_port(p) for p in ports) or "  (none)")
        )
    raise ValveConnectionError(
        "Multiple FTDI devices found; pass the valve's port or an identifier "
        "explicitly:\n" + "\n".join(describe_com_port(p) for p in ftdi)
    )


class MX_valve:
    """Controls an IDEX MX Series II valve. ``ports`` = selectable positions.

    For the MXX777-601 leave ``ports`` at its default of 2: the valve can
    only ever be commanded to position 1 or 2.
    """

    def __init__(
        self,
        address: str | None = None,
        ports: int | None = None,
        name: str = "",
        verbose: bool = False,
        baud: int | None = None,
        timeout: float | None = None,
        motion_timeout: float | None = None,
        mock: bool = False,
        serial_factory=None,
    ):
        self.address = address if address is not None else config.VALVE_PORT
        self.ports = int(ports) if ports is not None else config.VALVE_POSITIONS
        if not 1 <= self.ports <= 12:
            raise ValueError(
                f"ports (selectable positions) must be 1..12, got {self.ports}"
            )
        self.name = name
        self.verbose = verbose
        self.baud = baud if baud is not None else config.VALVE_BAUD
        self.timeout = timeout if timeout is not None else config.VALVE_TIMEOUT
        self.motion_timeout = (
            motion_timeout
            if motion_timeout is not None
            else config.VALVE_MOTION_TIMEOUT
        )
        self.mock = mock
        self._serial_factory = serial_factory
        self.ser = None

    # -- connection ----------------------------------------------------------
    def connect(self) -> "MX_valve":
        if not self.mock and self._serial_factory is None and not self.address:
            raise ValveConnectionError(
                "No valve serial port configured. Set MXVALVE_PORT, create "
                "configs/valve.local.json from configs/valve.local.example.json, "
                "or pass an address (find_address() can auto-detect the FTDI "
                "port)."
            )
        try:
            if self._serial_factory is not None:
                self.ser = self._serial_factory(
                    port=self.address,
                    baudrate=self.baud,
                    timeout=self.timeout,
                )
            elif self.mock:
                self.ser = MockMXValveSerial(
                    port=self.address or "MOCK",
                    baudrate=self.baud,
                    positions=self.ports,
                )
            else:
                # Proven transport from scripts/serial_hello_test.py: set the
                # control lines before opening to avoid a DTR/RTS pulse.
                ser = serial.Serial()
                ser.port = self.address
                ser.baudrate = self.baud
                ser.bytesize = serial.EIGHTBITS
                ser.parity = serial.PARITY_NONE
                ser.stopbits = serial.STOPBITS_ONE
                ser.timeout = min(self.timeout, 0.1)
                ser.write_timeout = self.timeout
                ser.dtr = False
                ser.rts = False
                ser.open()
                self.ser = ser
        except serial.SerialException as exc:
            raise ValveConnectionError(self._friendly_error(exc)) from exc
        except (OSError, ValueError) as exc:
            raise ValveConnectionError(self._friendly_error(exc)) from exc

        time.sleep(0.15)
        self._clear_buffers()
        return self

    def disconnect(self) -> None:
        if self.ser is not None:
            try:
                if getattr(self.ser, "is_open", False):
                    self.ser.close()
            finally:
                self.ser = None

    @property
    def is_connected(self) -> bool:
        return self.ser is not None and getattr(self.ser, "is_open", False)

    def __enter__(self) -> "MX_valve":
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disconnect()
        return False

    def _friendly_error(self, exc) -> str:
        text = str(exc).lower()
        if "permission" in text or "denied" in text:
            return (
                f"Access denied opening {self.address}. The port is probably "
                f"open in another program (IDEX/Rheodyne software, PuTTY, a "
                f"stuck Python). Original error: {exc}"
            )
        if "could not open" in text or "no such file" in text or "cannot find" in text:
            return (
                f"Port {self.address} not found. Check the USB cable, the FTDI "
                f"VCP driver, and MXVALVE_PORT/configs/valve.local.json. "
                f"Original error: {exc}"
            )
        return f"Could not open {self.address}: {exc}"

    # -- transport (identical framing to the proven home command) -------------
    def _label(self) -> str:
        return self.name or f"MX_valve({self.address})"

    def _say(self, message: str) -> None:
        LOGGER.debug("%s: %s", self._label(), message)
        if self.verbose:
            print(f"[{self._label()}] {message}")

    def _clear_buffers(self) -> None:
        if hasattr(self.ser, "reset_input_buffer"):
            self.ser.reset_input_buffer()
        if hasattr(self.ser, "reset_output_buffer"):
            self.ser.reset_output_buffer()

    def _read_packet(self, timeout: float) -> bytes:
        """Read until CR, a lone busy '*', or the deadline. Proven loop."""
        deadline = time.monotonic() + max(0.0, timeout)
        response = bytearray()
        while time.monotonic() < deadline:
            chunk = self.ser.read(1)
            if not chunk:
                # Real ports block up to 0.1 s per read; mocks return
                # instantly, so yield briefly to avoid a hot loop.
                time.sleep(0.001)
                continue
            response.extend(chunk)
            if chunk == TERMINATOR or bytes(response) == BUSY:
                break
        return bytes(response)

    def _send(self, command: str, read_timeout: float | None = None) -> bytes:
        """Send one ASCII command + CR and return the raw response bytes."""
        if not self.is_connected:
            raise ValveConnectionError("Not connected; call connect() first.")
        payload = command.encode("ascii") + TERMINATOR
        try:
            self._clear_buffers()
            self.ser.write(payload)
            if hasattr(self.ser, "flush"):
                self.ser.flush()
        except serial.SerialException as exc:
            raise ValveConnectionError(self._friendly_error(exc)) from exc
        self._say(f"TX raw={payload!r} hex={payload.hex(' ')}")
        response = self._read_packet(
            self.timeout if read_timeout is None else read_timeout
        )
        self._say(
            f"RX raw={response!r} hex={response.hex(' ') or '(empty)'} "
            f"({self._describe(response)})"
        )
        return response

    @staticmethod
    def _decode_hex(response: bytes) -> int | None:
        if not HEX_RESPONSE.fullmatch(response):
            return None
        return int(response[:2], 16)

    def _describe(self, response: bytes) -> str:
        if response == BUSY:
            return "busy: motor is moving"
        if response == TERMINATOR:
            return "command acknowledged"
        if not response:
            return "no response"
        value = self._decode_hex(response)
        if value is None:
            return "unexpected response"
        if value in ERROR_CODES:
            return ERROR_CODES[value]
        return f"value 0x{value:02X} ({value})"

    # -- queries ---------------------------------------------------------------
    def _query_value(self, command: str, what: str) -> int:
        response = self._send(command)
        value = self._decode_hex(response)
        if value is None:
            raise ValveError(
                f"{self._label()}: could not read {what}: raw response "
                f"{response!r} ({self._describe(response)})"
            )
        return value

    def is_busy(self) -> bool:
        """True while the motor is running (board answers '*' to anything)."""
        return self._send("S") == BUSY

    def get_port(self) -> int:
        """Return the current position (1..ports) via the S status query."""
        deadline = time.monotonic() + self.motion_timeout
        while True:
            response = self._send("S")
            if response == BUSY:
                if time.monotonic() >= deadline:
                    raise ValveTimeoutError(
                        f"{self._label()}: still busy after "
                        f"{self.motion_timeout:g} s"
                    )
                time.sleep(0.2)
                continue
            value = self._decode_hex(response)
            if value is None:
                raise ValveError(
                    f"{self._label()}: unreadable status response {response!r}. "
                    f"Check baud rate (MX II default is 19200) and cabling."
                )
            if value in ERROR_CODES:
                raise ValveReportedError(value)
            return value

    def get_firmware(self) -> int:
        return self._query_value("R", "firmware revision (R)")

    def get_profile(self) -> int:
        """Read the factory valve profile byte (Q). Informational."""
        return self._query_value("Q", "valve profile (Q)")

    def get_error(self) -> tuple[int, str]:
        code = self._query_value("E", "last error (E)")
        if code == 0:
            return 0, "no stored error"
        return code, ERROR_CODES.get(code, f"unrecognized error 0x{code:02X}")

    def get_command_mode(self) -> tuple[int, str]:
        code = self._query_value("D", "command mode (D)")
        return code, COMMAND_MODES.get(code, f"unknown mode 0x{code:02X}")

    # -- commands ----------------------------------------------------------------
    def _validate_position(self, position) -> int:
        if isinstance(position, bool) or not isinstance(position, int):
            raise TypeError(
                f"position must be an int, got {type(position).__name__}"
            )
        if not 1 <= position <= self.ports:
            raise ValueError(
                f"valve has only {self.ports} positions, got {position}"
            )
        return position

    def change_port(self, port: int, wait: bool = True) -> int | None:
        """Move to position ``port`` (validated against ``self.ports``).

        Uses the exact framing the proven home command uses: ASCII + CR.
        With ``wait=True`` (default) this blocks until the board reports the
        requested position and returns it; a silent ignore by the board
        becomes a loud ValveTimeoutError instead of doing nothing.
        """
        port = self._validate_position(port)
        command = f"P{port:02X}"
        ack = self._send(command)
        acked = ack in (TERMINATOR, BUSY) or self._decode_hex(ack) is not None
        if not wait:
            return None
        try:
            return self.wait_for_position(port)
        except ValveTimeoutError as exc:
            hint = (
                ""
                if acked
                else (
                    " No <CR> ack was received for the position command, and "
                    "the MX II silently ignores position commands it cannot "
                    "execute."
                )
            )
            raise ValveTimeoutError(
                f"{exc}{hint} Check that (1) the command mode is BCD for "
                f"USB control -- get_command_mode(), fix with "
                f"set_command_mode_bcd() plus a power cycle; (2) the valve "
                f"profile matches a {self.ports}-position valve -- "
                f"get_profile(); (3) the stored error code -- get_error(). "
                f"See docs/valve_mx2_guide.md."
            ) from exc

    def home(self, wait: bool = True) -> int | None:
        """Send the proven home command (M). Returns the settled position."""
        self._send("M")
        if not wait:
            return None
        return self.wait_for_position(None)

    def wait_for_position(
        self, expected: int | None, timeout: float | None = None
    ) -> int:
        """Poll S until the board reports ``expected`` (or, if None, any
        valid position while not busy). Raises on error codes or timeout.

        A mismatching position is NOT an instant failure: right after a move
        command the board may briefly report the old position before the
        motor engages, so keep polling until the deadline.
        """
        timeout = self.motion_timeout if timeout is None else timeout
        deadline = time.monotonic() + max(0.0, timeout)
        last = b""
        while time.monotonic() < deadline:
            time.sleep(0.2)
            response = self._send("S")
            last = response
            if response == BUSY:
                continue
            value = self._decode_hex(response)
            if value is None:
                continue
            if value in ERROR_CODES:
                raise ValveReportedError(value)
            if expected is None or value == expected:
                return value
        target = "a valid position" if expected is None else f"position {expected}"
        raise ValveTimeoutError(
            f"{self._label()}: valve did not reach {target} within "
            f"{timeout:g} s (last status: {last!r} - {self._describe(last)})."
        )

    def set_command_mode(self, mode: int) -> tuple[int, str]:
        """Write the command mode (F). BCD (0x03) is required for USB control.

        IMPORTANT: per IDEX 2321382G the new mode only becomes ACTIVE after
        the driver board is reset (power cycle the 24 V supply). This method
        writes the mode, reads it back with D, and returns (code, name) --
        but you still must power-cycle before position commands obey it.
        """
        if mode not in COMMAND_MODES:
            raise ValueError(
                f"command mode must be one of {sorted(COMMAND_MODES)} "
                f"(BCD is 0x03), got {mode}"
            )
        self._send(f"F{mode:02X}")
        stored = self.get_command_mode()
        self._say(
            f"command mode stored as 0x{stored[0]:02X} ({stored[1]}); "
            f"POWER CYCLE the board to make it active"
        )
        return stored

    def set_command_mode_bcd(self) -> tuple[int, str]:
        """Store BCD command mode (F03); active after a power cycle."""
        return self.set_command_mode(BCD_MODE)
