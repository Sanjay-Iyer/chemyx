"""
tests/test_chemyx.py - automated validation of the Chemyx Pump class.

These tests run with NO physical hardware.  They drive the Pump class against
fake serial objects (recording fakes injected via `serial_factory`, plus
monkeypatching `serial.Serial` for the connection-error cases).

Run from the repo root:   pytest
"""

import pytest

from chemyx_lab import config
from chemyx_lab.instruments import chemyx as pump_module
from chemyx_lab.instruments.chemyx import EchoMismatchError, Pump, PumpConnectionError

serial = pump_module.serial


# =============================================================================
# Fake serial ports
# =============================================================================
class _BaseFake:
    """Common serial-like plumbing: buffers, read, close, etc."""

    def __init__(self, port=None, baudrate=9600, **kwargs):
        self.port = port
        self.baudrate = baudrate
        self.is_open = True
        self.raw_writes = []  # every raw bytes object written, in order
        self._rx = bytearray()  # pump -> host bytes waiting to be read

    # subclasses implement the pump's reply behaviour
    def _on_command(self, line):
        raise NotImplementedError

    def write(self, data):
        self.raw_writes.append(bytes(data))
        text = data.decode("ascii", errors="replace")
        for line in text.replace("\n", "\r").split("\r"):
            line = line.strip()
            if line:
                self._on_command(line)
        return len(data)

    def _reply(self, text):
        self._rx.extend((text + "\r\n").encode("ascii"))

    def read(self, size=1):
        if not self._rx:
            return b""
        chunk = bytes(self._rx[:size])
        del self._rx[:size]
        return chunk

    @property
    def in_waiting(self):
        return len(self._rx)

    def reset_input_buffer(self):
        self._rx.clear()

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    # convenience for assertions
    @property
    def commands(self):
        """Decoded list of everything written, e.g. ['set diameter 4.5\\r']."""
        return [w.decode("ascii") for w in self.raw_writes]


class GoodSerial(_BaseFake):
    """Replies with a correct Chemyx-style echo so verification passes."""

    def _on_command(self, line):
        parts = line.split()
        # strip optional leading single-digit channel address
        if parts and len(parts[0]) == 1 and parts[0] in "1234":
            parts = parts[1:]
        if len(parts) >= 3 and parts[0] == "set":
            key, val = parts[1], parts[2]
            if key == "units":
                self._reply(f"units = {config.UNITS[int(val)]}")
            else:
                self._reply(f"{key} = {val}")
        elif parts and parts[0] in ("start", "stop", "pause"):
            self._reply(f"pump {parts[0]}")
        else:
            self._reply(line)


class BadEchoSerial(_BaseFake):
    """Always echoes the WRONG numeric value -> should trip EchoMismatchError."""

    def _on_command(self, line):
        parts = line.split()
        if "diameter" in parts:
            self._reply("diameter = 9.9")  # never what we asked for
        elif "rate" in parts:
            self._reply("rate = 999.0")
        elif "volume" in parts:
            self._reply("volume = 999.0")
        else:
            self._reply(line)


class NumericUnitsSerial(GoodSerial):
    """Some real Chemyx firmware echoes units as a code instead of a label."""

    def _on_command(self, line):
        parts = line.split()
        if parts and len(parts[0]) == 1 and parts[0] in "1234":
            parts = parts[1:]
        if len(parts) >= 3 and parts[0] == "set" and parts[1] == "units":
            self._reply(f"units = {parts[2]}")
        else:
            super()._on_command(line)


class SilentSerial(_BaseFake):
    """Never replies -> no echo found -> EchoMismatchError."""

    def _on_command(self, line):
        pass  # say nothing


def make_pump(fake_cls=GoodSerial, **kwargs):
    """Build and connect a Pump backed by the given fake serial class."""
    p = Pump(serial_factory=lambda **kw: fake_cls(**kw), **kwargs)
    p.connect()
    return p


# =============================================================================
# Command formatting & carriage-return termination
# =============================================================================
def test_send_command_appends_carriage_return():
    p = make_pump()
    p.send_command("help")
    assert p.ser.raw_writes[-1] == b"help\r"


def test_set_diameter_command_bytes():
    p = make_pump()
    p.set_diameter(4.5)
    assert p.ser.raw_writes[-1] == b"set diameter 4.5\r"


def test_set_rate_command_bytes():
    p = make_pump()
    p.set_rate(2.5)
    assert p.ser.raw_writes[-1] == b"set rate 2.5\r"


def test_set_units_command_bytes_and_echo():
    p = make_pump()
    resp = p.set_units("mL/min")  # accepts a human string
    assert p.ser.raw_writes[-1] == b"set units 0\r"
    assert "ml/min" in resp.lower()


def test_set_units_accepts_numeric_echo_seen_on_real_pump():
    p = make_pump(NumericUnitsSerial)
    resp = p.set_units("mL/min")
    assert p.ser.raw_writes[-1] == b"set units 0\r"
    assert "units = 0" in resp.lower()


def test_every_command_is_cr_terminated():
    p = make_pump()
    p.set_units(0)
    p.set_diameter(4.5)
    p.set_rate(1.0)
    p.infuse(0.5)
    p.stop()
    assert p.ser.raw_writes, "no commands were sent"
    for raw in p.ser.raw_writes:
        assert raw.endswith(b"\r"), f"command not CR-terminated: {raw!r}"
        assert b"\n" not in raw, f"command should not contain LF: {raw!r}"


# =============================================================================
# Direction encoding: infuse = positive volume, withdraw = negative volume
# =============================================================================
def test_infuse_sends_positive_volume_then_start():
    p = make_pump()
    p.infuse(0.5)
    cmds = p.ser.commands
    assert "set volume 0.5\r" in cmds
    assert "start\r" in cmds
    # the volume command must come before start
    assert cmds.index("set volume 0.5\r") < cmds.index("start\r")


def test_infuse_can_send_start_zero():
    p = make_pump(channel=1)
    p.infuse(0.5, start_delay=0)
    cmds = p.ser.commands
    assert "1 set volume 0.5\r" in cmds
    assert "1 start 0\r" in cmds
    assert cmds.index("1 set volume 0.5\r") < cmds.index("1 start 0\r")


def test_withdraw_sends_negative_volume_then_start():
    p = make_pump()
    p.withdraw(0.5)
    cmds = p.ser.commands
    assert "set volume -0.5\r" in cmds
    assert "start\r" in cmds
    assert cmds.index("set volume -0.5\r") < cmds.index("start\r")


def test_infuse_forces_positive_even_for_negative_input():
    p = make_pump()
    p.infuse(-0.5)  # caller passed a negative by mistake
    assert "set volume 0.5\r" in p.ser.commands


def test_withdraw_forces_negative_even_for_positive_input():
    p = make_pump()
    p.withdraw(0.5)
    assert "set volume -0.5\r" in p.ser.commands


# =============================================================================
# Dual-channel addressing (Fusion 4000X)
# =============================================================================
def test_channel_zero_has_no_prefix():
    p = make_pump(channel=0)
    p.set_rate(5.0)
    assert p.ser.raw_writes[-1] == b"set rate 5.0\r"


def test_channel_two_prefixes_command():
    p = make_pump(channel=2)
    p.set_rate(5.0)
    assert p.ser.raw_writes[-1] == b"2 set rate 5.0\r"


def test_per_call_channel_override():
    p = make_pump(channel=0)
    p.start(channel=1)
    assert p.ser.raw_writes[-1] == b"1 start\r"


def test_start_delay_zero_matches_validated_script():
    p = make_pump(channel=1)
    p.start(delay=0)
    assert p.ser.raw_writes[-1] == b"1 start 0\r"


def test_select_channel_changes_default():
    p = make_pump(channel=0)
    p.select_channel(2)
    p.stop()
    assert p.ser.raw_writes[-1] == b"2 stop\r"


def test_select_channel_rejects_bad_channel():
    p = make_pump()
    with pytest.raises(ValueError):
        p.select_channel(5)


# =============================================================================
# Range validation
# =============================================================================
def test_set_diameter_rejects_too_large():
    p = make_pump()
    with pytest.raises(ValueError):
        p.set_diameter(50.0)  # > 40.000 mm
    assert p.ser.raw_writes == []  # nothing was sent to the pump


def test_set_diameter_rejects_too_small():
    p = make_pump()
    with pytest.raises(ValueError):
        p.set_diameter(0.05)  # < 0.103 mm


def test_set_diameter_accepts_boundaries():
    p = make_pump()
    p.set_diameter(config.DIAMETER_MIN)
    p.set_diameter(config.DIAMETER_MAX)  # should not raise


def test_set_rate_rejects_out_of_range_for_units():
    p = make_pump()  # default units = mL/min (max 170.5)
    with pytest.raises(ValueError):
        p.set_rate(200.0)
    with pytest.raises(ValueError):
        p.set_rate(-5.0)


def test_set_rate_range_depends_on_units():
    p = make_pump()
    p.set_units(2)  # uL/min -> much larger ceiling
    p.set_rate(200.0)  # now perfectly valid
    assert p.ser.raw_writes[-1] == b"set rate 200.0\r"


def test_set_volume_rejects_zero():
    p = make_pump()
    with pytest.raises(ValueError):
        p.set_volume(0)


# =============================================================================
# Echo verification
# =============================================================================
def test_echo_mismatch_is_detected():
    p = make_pump(BadEchoSerial)
    with pytest.raises(EchoMismatchError):
        p.set_diameter(4.5)  # pump echoes 9.9 -> mismatch


def test_missing_echo_is_detected():
    p = make_pump(SilentSerial)
    with pytest.raises(EchoMismatchError):
        p.set_diameter(4.5)  # no echo at all


def test_verify_false_skips_echo_check():
    p = make_pump(BadEchoSerial, verify=False)
    # Should NOT raise even though the echo is wrong, because verify is off.
    p.set_diameter(4.5)
    assert p.ser.raw_writes[-1] == b"set diameter 4.5\r"


# =============================================================================
# Connection / error handling
# =============================================================================
def test_port_not_found_is_friendly(monkeypatch):
    def raiser(*args, **kwargs):
        raise serial.SerialException(
            "could not open port 'FAKE_MISSING_PORT': FileNotFoundError(2, ...)"
        )

    monkeypatch.setattr(pump_module.serial, "Serial", raiser)
    p = Pump(port="FAKE_MISSING_PORT", mock=False)
    with pytest.raises(PumpConnectionError) as exc:
        p.connect()
    assert "not found" in str(exc.value).lower()


def test_access_denied_is_friendly(monkeypatch):
    def raiser(*args, **kwargs):
        raise serial.SerialException(
            "could not open port 'FAKE_BUSY_PORT': PermissionError(13, 'Access is denied.')"
        )

    monkeypatch.setattr(pump_module.serial, "Serial", raiser)
    p = Pump(port="FAKE_BUSY_PORT", mock=False)
    with pytest.raises(PumpConnectionError) as exc:
        p.connect()
    assert "access denied" in str(exc.value).lower()


def test_send_command_without_connection_raises():
    p = Pump(mock=True)  # built but never connected
    with pytest.raises(PumpConnectionError):
        p.send_command("help")


# =============================================================================
# Context manager always closes the port
# =============================================================================
def test_context_manager_opens_and_closes():
    p = Pump(serial_factory=lambda **kw: GoodSerial(**kw))
    with p as ctx:
        assert ctx is p
        assert p.is_connected
        ser = p.ser
        p.set_diameter(4.5)
    assert not p.is_connected
    assert ser.is_open is False  # underlying port really closed


def test_context_manager_closes_on_exception():
    p = Pump(serial_factory=lambda **kw: GoodSerial(**kw))
    with pytest.raises(ValueError):
        with p:
            ser = p.ser
            p.set_diameter(99.0)  # out of range -> ValueError
    assert ser.is_open is False  # still closed despite the error


# =============================================================================
# Units helpers
# =============================================================================
def test_resolve_units_accepts_codes_and_names():
    assert config.resolve_units(0) == 0
    assert config.resolve_units("mL/min") == 0
    assert config.resolve_units("uL/hr") == 3
    with pytest.raises(ValueError):
        config.resolve_units("furlongs/fortnight")


# =============================================================================
# The built-in emulator (mock_serial) also works end-to-end
# =============================================================================
def test_mock_serial_round_trip():
    with Pump(mock=True) as p:
        assert p.is_connected
        p.set_units(0)
        p.set_diameter(4.5)
        p.set_rate(1.0)
        p.infuse(0.5)
        p.stop()
        # tx_log on the emulator captures the CR-terminated commands
        assert "set diameter 4.5\r" in p.ser.tx_log
        assert "set volume 0.5\r" in p.ser.tx_log
    assert not p.is_connected
