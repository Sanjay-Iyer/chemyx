from __future__ import annotations

import pytest

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.config import test4_full_missing as full_test4_missing
from arduino.python.controller import MOTION_COMMANDS, NeedleController
from arduino.python.discovery import ensure_distinct_ports
from arduino.python.errors import HardRuntimeExceeded, MotionInterlockError, PortCollisionError
from arduino.python.results import (
    create_run_dir,
    matching_live_result,
    unresolved_live_motion_failure,
    write_result,
)
from arduino.python.workflows import (
    TEST_01_COMMANDS,
    HardDeadline,
    SequentialInstrumentInterlock,
    run_test_01,
)


def test_test1_contains_no_motor_commands():
    assert not ({item.split()[0] for item in TEST_01_COMMANDS} & MOTION_COMMANDS)


def test_test1_runs_without_motor_configuration(base_config):
    fake = FakeArduinoTransport()
    with NeedleController(fake) as controller:
        final = run_test_01(controller, HardDeadline(1))
    assert final["enabled"] == "false"
    assert not ({line.split()[1] for line in fake.tx_log} & MOTION_COMMANDS)


def test_arduino_and_chemyx_port_collision_is_rejected():
    with pytest.raises(PortCollisionError):
        ensure_distinct_ports("COM7", "com7")


def test_mock_result_cannot_unlock_live_prerequisite(base_config, tmp_path):
    run_dir = create_run_dir(tmp_path, "test_02_unloaded_motor")
    write_result(
        run_dir,
        test_name="test_02_unloaded_motor",
        mode="mock",
        cfg=base_config,
        passed=True,
        firmware_version="0.1.0",
        operator_confirmations={},
        final_known_device_state={},
    )
    assert matching_live_result(tmp_path, "test_02_unloaded_motor", base_config) is None


def test_failed_live_motion_requires_new_inspection_clearance(base_config, tmp_path):
    run_dir = create_run_dir(tmp_path, "test_02_unloaded_motor")
    write_result(
        run_dir,
        test_name="test_02_unloaded_motor",
        mode="live",
        cfg=base_config,
        passed=False,
        firmware_version="0.1.0",
        operator_confirmations={},
        final_known_device_state={"needle_position": "uncertain"},
        motion_attempted=True,
    )
    assert unresolved_live_motion_failure(tmp_path, base_config) == run_dir / "result.json"
    later_success = create_run_dir(tmp_path, "test_03_needle_axis")
    write_result(
        later_success,
        test_name="test_03_needle_axis",
        mode="live",
        cfg=base_config,
        passed=True,
        firmware_version="0.1.0",
        operator_confirmations={},
        final_known_device_state={"needle_position": "safe_up"},
        motion_attempted=True,
    )
    assert unresolved_live_motion_failure(tmp_path, base_config) == run_dir / "result.json"
    base_config["safety"]["operator_inspection_clearance"] = "inspection-2026-08-03"
    assert unresolved_live_motion_failure(tmp_path, base_config) is None


def test_full_test4_cannot_start_without_records(base_config):
    missing = full_test4_missing(base_config, {"test_01": False, "test_02": False, "test_03": False, "test_04a": False})
    assert "Matching successful live Test 1, Test 2, Test 3, and Test 4A records" in missing


def test_nmr_cannot_start_while_arduino_reports_movement():
    interlock = SequentialInstrumentInterlock()
    with pytest.raises(MotionInterlockError):
        interlock.assert_nmr_can_start({"moving": "true"})


def test_arduino_motion_cannot_start_during_nmr():
    interlock = SequentialInstrumentInterlock(nmr_active=True)
    with NeedleController(
        FakeArduinoTransport(homed=True), allow_motion=True, motion_guard=interlock.motion_allowed
    ) as controller:
        with pytest.raises(MotionInterlockError):
            controller.move_absolute(100, 100)


def test_hard_runtime_limit_is_enforced():
    times = iter([0.0, 2.0])
    deadline = HardDeadline(1.0, monotonic_fn=lambda: next(times))
    with pytest.raises(HardRuntimeExceeded):
        deadline.check("bounded test")
