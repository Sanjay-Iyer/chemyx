"""
tests/test_valve.py — Automated validation of the MX Series II valve driver.

These tests run with NO physical hardware. They drive MX_valve against the
MockMXValveSerial board emulator (injected via ``serial_factory``), which
reproduces the documented MX II behaviours that matter:

- busy '*' replies during motion,
- silent ignoring of nonexistent positions,
- silent ignoring of position commands in non-BCD command mode,
- CR ack framing per IDEX doc 2321382G.

Run from the repo root:   pytest
"""

import pytest

from chemyx_lab.testing.mock_serial import MockMXValveSerial
from chemyx_lab.instruments.valve import (
    BCD_MODE,
    MX_valve,
    ValveConnectionError,
    ValveReportedError,
    ValveTimeoutError,
    find_address,
)


# Short timeouts keep the ignored-command paths fast: the driver polls until
# the deadline when the board stays silent. A mock move needs up to four
# 0.2 s status polls, so the motion timeout must stay comfortably above 0.8 s.
FAST = {"timeout": 0.05, "motion_timeout": 2.0}


def make_valve(mock_kwargs=None, **valve_kwargs):
    """Connected MX_valve wired to a MockMXValveSerial board."""
    mock_kwargs = dict(mock_kwargs or {})

    def factory(**kwargs):
        kwargs.pop("timeout", None)  # the mock does not block on reads
        return MockMXValveSerial(**{**kwargs, **mock_kwargs})

    valve_kwargs = {**FAST, **valve_kwargs}
    valve = MX_valve(address="COMTEST", serial_factory=factory, **valve_kwargs)
    return valve.connect()


# =============================================================================
# Defaults and validation
# =============================================================================
def test_default_is_two_positions():
    valve = MX_valve(address="COMX")
    assert valve.ports == 2


def test_change_port_rejects_position_5_with_clear_error():
    valve = make_valve()
    with pytest.raises(ValueError, match="valve has only 2 positions, got 5"):
        valve.change_port(5)


def test_rejected_position_sends_nothing_on_the_wire():
    valve = make_valve()
    with pytest.raises(ValueError):
        valve.change_port(5)
    assert valve.ser.raw_writes == []


def test_change_port_rejects_zero_and_negative_and_bool():
    valve = make_valve()
    for bad in (0, -1, 3):
        with pytest.raises(ValueError):
            valve.change_port(bad)
    with pytest.raises(TypeError):
        valve.change_port(True)
    with pytest.raises(TypeError):
        valve.change_port("2")


def test_ports_parameter_allows_bigger_valves():
    valve = make_valve(mock_kwargs={"positions": 6}, ports=6)
    assert valve.change_port(6) == 6
    assert valve.ser.position == 6


def test_constructor_rejects_silly_ports():
    with pytest.raises(ValueError):
        MX_valve(address="COMX", ports=0)
    with pytest.raises(ValueError):
        MX_valve(address="COMX", ports=13)


# =============================================================================
# Wire framing (must match the proven home transport)
# =============================================================================
def test_change_port_sends_exact_p02_cr_frame():
    valve = make_valve()
    valve.change_port(2)
    assert valve.ser.raw_writes[0] == b"P02\r"


def test_home_sends_exact_m_cr_frame():
    valve = make_valve()
    position = valve.home()
    assert valve.ser.raw_writes[0] == b"M\r"
    assert position == 1


def test_status_reads_position():
    valve = make_valve(mock_kwargs={"start_position": 2})
    assert valve.get_port() == 2
    assert valve.ser.raw_writes == [b"S\r"]


def test_hex_positions_above_nine_decode():
    valve = make_valve(mock_kwargs={"positions": 10, "start_position": 10}, ports=10)
    assert valve.get_port() == 10  # board replies "0A\r"


# =============================================================================
# Motion, busy handling, and readback
# =============================================================================
def test_change_port_waits_through_busy_and_confirms():
    valve = make_valve(mock_kwargs={"busy_polls": 3})
    assert valve.change_port(2) == 2
    assert valve.ser.position == 2


def test_change_port_tolerates_slow_motion_start():
    # The board reports the OLD position briefly before the motor engages;
    # the driver must keep polling instead of failing on first mismatch.
    valve = make_valve(mock_kwargs={"lag_polls": 1, "busy_polls": 2})
    assert valve.change_port(2) == 2


def test_status_error_code_raises_reported_error():
    valve = make_valve(mock_kwargs={"force_status_error": 0x42})
    with pytest.raises(ValveReportedError, match="positioning error"):
        valve.get_port()


# =============================================================================
# The real-world failure: silent ignore -> loud, diagnostic error
# =============================================================================
def test_level_logic_mode_home_works_but_moves_fail_loudly():
    valve = make_valve(mock_kwargs={"command_mode": 0x01})
    assert valve.home() == 1  # home still works, as observed on the bench
    with pytest.raises(ValveTimeoutError) as excinfo:
        valve.change_port(2)
    message = str(excinfo.value)
    assert "No <CR> ack" in message
    assert "BCD" in message


def test_board_that_thinks_valve_is_smaller_fails_loudly():
    # Driver believes 6 positions, board only accepts 2 -> silent ignore.
    valve = make_valve(mock_kwargs={"positions": 2}, ports=6)
    with pytest.raises(ValveTimeoutError):
        valve.change_port(4)


# =============================================================================
# Command mode
# =============================================================================
def test_read_command_mode():
    valve = make_valve(mock_kwargs={"command_mode": 0x01})
    assert valve.get_command_mode() == (0x01, "level logic")


def test_set_command_mode_bcd_sends_f03_and_reads_back():
    valve = make_valve(mock_kwargs={"command_mode": 0x01})
    stored = valve.set_command_mode_bcd()
    assert stored == (BCD_MODE, "BCD logic")
    assert b"F03\r" in valve.ser.raw_writes
    # Stored but NOT active until the board resets, exactly like the manual.
    assert valve.ser.stored_mode == BCD_MODE
    assert valve.ser.active_mode == 0x01
    valve.ser.power_cycle()
    assert valve.change_port(2) == 2


def test_set_command_mode_validates_input():
    valve = make_valve()
    with pytest.raises(ValueError):
        valve.set_command_mode(9)


# =============================================================================
# find_address / FTDI detection
# =============================================================================
class _FakePortInfo:
    def __init__(self, device, vid=None, pid=None, description="", hwid="",
                 serial_number=""):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.description = description
        self.hwid = hwid
        self.serial_number = serial_number


def test_find_address_picks_the_single_ftdi_port():
    ports = [
        _FakePortInfo("FAKE_CH340_PORT", vid=0x1A86, pid=0x7523, description="CH340"),
        _FakePortInfo("FAKE_FTDI_PORT", vid=0x0403, pid=0x6001, description="FT232R"),
    ]
    assert find_address(ports=ports) == "FAKE_FTDI_PORT"


def test_find_address_errors_when_no_ftdi():
    ports = [_FakePortInfo("FAKE_CH340_PORT", vid=0x1A86, pid=0x7523)]
    with pytest.raises(ValveConnectionError, match="0403:6001"):
        find_address(ports=ports)


def test_find_address_errors_on_multiple_ftdi():
    ports = [
        _FakePortInfo("FAKE_FTDI_A", vid=0x0403, pid=0x6001),
        _FakePortInfo("FAKE_FTDI_B", vid=0x0403, pid=0x6001),
    ]
    with pytest.raises(ValveConnectionError, match="Multiple FTDI"):
        find_address(ports=ports)


def test_find_address_matches_identifier_substring():
    ports = [
        _FakePortInfo("FAKE_CHEMYX_PORT", description="Chemyx pump"),
        _FakePortInfo("FAKE_TARGET_PORT", hwid="USB VID:PID=0403:6001 SER=A700ABCD"),
    ]
    assert find_address("a700abcd", ports=ports) == "FAKE_TARGET_PORT"


# =============================================================================
# Connection guards
# =============================================================================
def test_unconfigured_port_gives_actionable_error():
    valve = MX_valve(address="")
    with pytest.raises(ValveConnectionError, match="MXVALVE_PORT"):
        valve.connect()


def test_commands_require_connect_first():
    valve = MX_valve(address="COMX")
    with pytest.raises(ValveConnectionError, match="connect"):
        valve.get_port()
