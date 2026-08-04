import pytest

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.controller import NeedleController
from arduino.python.errors import (
    CommandNotDispatched,
    IdentityMismatch,
    MotionInterlockError,
    ProtocolError,
    SequenceMismatch,
)


def test_context_manager_validates_ready_and_closes():
    fake = FakeArduinoTransport()
    with NeedleController(fake) as controller:
        assert controller.identity["version"] == "0.1.0"
        controller.ping()
    assert not fake.is_open


def test_ready_identity_mismatch_fails_closed():
    fake = FakeArduinoTransport(board="wrong_board")
    with pytest.raises(IdentityMismatch):
        NeedleController(fake).open()
    assert not fake.is_open


def test_motion_is_disabled_by_default():
    with NeedleController(FakeArduinoTransport()) as controller:
        with pytest.raises(MotionInterlockError):
            controller.jog(10, 100)


def test_sequence_mismatch_is_rejected():
    with NeedleController(FakeArduinoTransport(scenario="sequence_mismatch")) as controller:
        with pytest.raises(SequenceMismatch):
            controller.ping()


def test_malformed_response_is_rejected():
    with NeedleController(FakeArduinoTransport(scenario="malformed_response")) as controller:
        with pytest.raises(ProtocolError):
            controller.ping()


def test_ack_verb_must_match_dispatched_command():
    fake = FakeArduinoTransport(
        scenario="wrong_ack_verb", motion_commissioned=True
    )
    fake.enabled = True
    with NeedleController(fake, allow_motion=True) as controller:
        with pytest.raises(ProtocolError, match="ACK command mismatch"):
            controller.command("JOG 10 100")


def test_expired_deadline_does_not_mark_motion_dispatched():
    dispatched = []
    fake = FakeArduinoTransport(
        homed=True,
        motion_commissioned=True,
        maximum_travel_steps=1000,
        maximum_speed_steps_s=300,
        initially_enabled=True,
    )
    with NeedleController(
        fake,
        allow_motion=True,
        overall_timeout_s=1,
        motion_dispatch_callback=lambda: dispatched.append(True),
    ) as controller:
        controller.status()
        fake.tx_log.clear()
        controller._opened_at = 0.0
        with pytest.raises(CommandNotDispatched):
            controller.jog(10, 100)
        assert controller.position_certain is True
    assert dispatched == []
    assert fake.tx_log == []
