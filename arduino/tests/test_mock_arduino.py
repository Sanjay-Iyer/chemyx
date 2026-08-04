import pytest

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.controller import NeedleController
from arduino.python.errors import PositionUncertainError


def controller(fake):
    fake.motion_commissioned = True
    fake.limits_commissioned = True
    fake.enabled = True
    fake.maximum_travel_steps = 1000
    fake.maximum_speed_steps_s = 300
    fake.maximum_acceleration_steps_s2 = 300
    fake.home_speed_steps_s = 100
    return NeedleController(fake, allow_motion=True, command_timeout_s=0.02)


def test_led_and_pong_state():
    with controller(FakeArduinoTransport()) as item:
        item.ping()
        item.led_on()
        assert item.status()["led"] == "on"
        item.led_off()
        assert item.status()["led"] == "off"


def test_absolute_move_rejected_before_homing():
    with controller(FakeArduinoTransport()) as item:
        with pytest.raises(PositionUncertainError, match="NOT_HOMED"):
            item.move_absolute(100, 100)
        assert item.position_certain is False


def test_movement_toward_active_limit_is_rejected():
    fake = FakeArduinoTransport()
    fake.limit_up = True
    with controller(fake) as item:
        with pytest.raises(PositionUncertainError, match="LIMIT_UP_ACTIVE"):
            item.jog(-10, 100)


def test_both_limits_active_faults():
    fake = FakeArduinoTransport()
    fake.limit_up = fake.limit_down = True
    with controller(fake) as item:
        with pytest.raises(PositionUncertainError, match="BOTH_LIMITS_ACTIVE"):
            item.home()
        assert fake.fault == "BOTH_LIMITS_ACTIVE"


@pytest.mark.parametrize("scenario", ["movement_timeout", "homing_timeout", "firmware_fault"])
def test_motion_failures_mark_position_uncertain(scenario):
    with controller(FakeArduinoTransport(scenario=scenario)) as item:
        with pytest.raises(PositionUncertainError):
            item.home() if scenario == "homing_timeout" else item.jog(10, 100)
        assert item.position_certain is False


@pytest.mark.parametrize("scenario", ["missing_ack", "missing_done", "serial_disconnection"])
def test_transport_failure_scenarios_are_finite(scenario):
    with controller(FakeArduinoTransport(scenario=scenario)) as item:
        with pytest.raises(Exception):
            item.ping()
