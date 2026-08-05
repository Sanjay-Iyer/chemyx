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


def test_commercial_runtime_configuration_applies_reviewed_values(commissioned_config):
    commissioned_config["firmware"]["runtime_configurable"] = True
    commissioned_config["driver"]["model"] = "DM542S"
    fake = FakeArduinoTransport(
        device="commercial_needle_controller",
        version="1.0.0",
        runtime_configurable=True,
        driver_model="DM542S",
    )
    with NeedleController(
        fake,
        expected_device="commercial_needle_controller",
        expected_version="1.0.0",
    ) as controller:
        final = controller.configure_runtime(commissioned_config)
    assert final["runtime_configured"] == "true"
    assert final["motion_commissioned"] == "true"
    assert final["limits_commissioned"] == "true"
    assert final["maximum_travel_steps"] == "1000"
    assert any(" CONFIG_IO " in line for line in fake.tx_log)
    assert any(" CONFIG_LIMITS " in line for line in fake.tx_log)
    assert any(line.endswith(" CONFIG_APPLY") for line in fake.tx_log)


def test_matching_runtime_configuration_preserves_homed_enabled_state(commissioned_config):
    commissioned_config["firmware"]["runtime_configurable"] = True
    commissioned_config["driver"]["model"] = "DM542S"
    fake = FakeArduinoTransport(
        device="commercial_needle_controller",
        version="1.0.0",
        runtime_configurable=True,
        homed=True,
        motion_commissioned=True,
        limits_commissioned=True,
        maximum_travel_steps=1000,
        maximum_speed_steps_s=300,
        maximum_acceleration_steps_s2=300,
        home_speed_steps_s=100,
        initial_position_steps=100,
        initially_enabled=True,
        driver_model="DM542S",
    )
    fake.runtime_configured = True
    fake.signal_inverted = True
    with NeedleController(
        fake,
        expected_device="commercial_needle_controller",
        expected_version="1.0.0",
    ) as controller:
        final = controller.configure_runtime(commissioned_config)
    assert final["homed"] == "true"
    assert final["enabled"] == "true"
    assert not any(" CONFIG_" in line for line in fake.tx_log)


def test_runtime_configuration_mismatch_is_blocked_while_enabled(commissioned_config):
    commissioned_config["firmware"]["runtime_configurable"] = True
    fake = FakeArduinoTransport(runtime_configurable=True, initially_enabled=True)
    fake.runtime_configured = True
    with NeedleController(fake) as controller:
        with pytest.raises(MotionInterlockError, match="mismatch"):
            controller.configure_runtime(commissioned_config)
