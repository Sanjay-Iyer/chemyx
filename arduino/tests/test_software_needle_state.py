"""D3/D4 demo needle tests; all use a fake transport, never physical hardware."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.config import load_arduino_config, test3_missing as missing_for_test3
from arduino.python.controller import NeedleController
from arduino.python.errors import MotionInterlockError, PositionUncertainError
from arduino.python.needle_state import NeedleState, NeedleStateStore, TrackedNeedle


ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing-violation retry")
@pytest.mark.parametrize("permanent", [False, True])
def test_atomic_state_replace_retries_boundedly_and_never_loses_uncertainty(tmp_path, monkeypatch, permanent):
    from arduino.python import needle_state
    from dataclasses import replace
    from datetime import datetime, timezone
    store = NeedleStateStore(tmp_path / "state.json")
    pending = NeedleState(logical_position=0, position_valid=False,
                          last_updated=datetime.now(timezone.utc).isoformat(), reason="motion_pending_to_1")
    store.save(pending)
    completed = replace(pending, logical_position=1, position_valid=True, reason="motion_completed")
    real_replace = needle_state.os.replace
    attempts, sleeps = [], []
    def sharing_violation(source, destination):
        attempts.append(1)
        if permanent or len(attempts) <= 2:
            raise PermissionError("TEST: destination temporarily held open")
        return real_replace(source, destination)
    monkeypatch.setattr(needle_state.os, "replace", sharing_violation)
    monkeypatch.setattr(needle_state.time, "sleep", sleeps.append)
    if permanent:
        with pytest.raises(PermissionError):
            store.save(completed)
        assert store.load() == pending
        assert len(attempts) == 6 and len(sleeps) == 5
    else:
        store.save(completed)
        assert store.load() == completed
        assert len(attempts) == 3 and sleeps == [.05, .1]
    assert not list(tmp_path.glob("*.tmp"))


def make_needle(tmp_path: Path, *, scenario: str = "normal"):
    cfg = load_arduino_config(ROOT / "arduino/configs/arduino.example.yaml")
    cfg["firmware"]["motion_enabled"] = True
    cfg["needle"].update(steps_per_unit=20, up_step_sign=1)
    cfg["motion"].update(maximum_speed_steps_s=100, maximum_acceleration_steps_s2=300)
    fake = FakeArduinoTransport(runtime_configurable=True, scenario=scenario)
    controller = NeedleController(fake, expected_version="1.2.1", allow_motion=True)
    controller.open()
    controller.configure_runtime(cfg)
    needle = TrackedNeedle(controller, cfg, state_path=tmp_path / "needle_state.json", allow_home_assumption=True)
    return needle, fake, cfg


def test_sequence_and_limits(tmp_path):
    needle, fake, _ = make_needle(tmp_path)
    # Demo mode: an unknown position is assumed to be HOME.
    assert needle.state.logical_position == 0
    assert needle.position_certain
    with pytest.raises(MotionInterlockError):
        needle.confirm_home()
    needle.confirm_home(operator_confirmed=True)
    observed = []
    for operation in (needle.needle_up, needle.needle_up, needle.needle_down,
                      needle.needle_down, needle.needle_down, needle.needle_up):
        operation()
        observed.append(needle.state.logical_position)
    assert observed == [1, 2, 1, 0, -1, 0]
    needle.move_to(5)
    before = len(fake.tx_log)
    with pytest.raises(MotionInterlockError, match="Software needle limit"):
        needle.needle_up()
    assert len(fake.tx_log) == before
    with pytest.raises(MotionInterlockError, match="120-second timeout"):
        needle.move_to(-3, speed_steps_s=1)
    assert len(fake.tx_log) == before
    needle.move_to(-3)
    before = len(fake.tx_log)
    with pytest.raises(MotionInterlockError, match="Software needle limit"):
        needle.needle_down()
    assert len(fake.tx_log) == before
    needle.close()


def test_restart_and_return_home(tmp_path):
    needle, fake, cfg = make_needle(tmp_path)
    needle.confirm_home(operator_confirmed=True)
    needle.needle_up()
    needle.needle_up()
    assert json.loads((tmp_path / "needle_state.json").read_text())["logical_position"] == 2
    needle.close()
    fake2 = FakeArduinoTransport(runtime_configurable=True)
    controller2 = NeedleController(fake2, expected_version="1.2.1", allow_motion=True)
    controller2.open()
    controller2.configure_runtime(cfg)
    restarted = TrackedNeedle(controller2, cfg, state_path=tmp_path / "needle_state.json")
    assert restarted.state.logical_position == 2
    restarted.return_to_home()
    assert restarted.state.logical_position == 0
    assert json.loads((tmp_path / "needle_state.json").read_text())["position_valid"] is True
    assert any("JOG -40 " in line for line in fake2.tx_log)
    restarted.close()


def test_automation_requires_saved_home_and_never_redefines_uncertain_position(tmp_path):
    needle, fake, cfg = make_needle(tmp_path)
    # A demo assumption cannot serve as the automated physical reference.
    with pytest.raises(PositionUncertainError, match="confirmed physical reference"):
        TrackedNeedle(needle.controller, cfg, state_path=tmp_path / "needle_state.json", allow_home_assumption=False)
    needle.confirm_home(operator_confirmed=True)
    needle.move_to(1)
    reopened = TrackedNeedle(needle.controller, cfg, state_path=tmp_path / "needle_state.json", allow_home_assumption=False)
    assert reopened.state.logical_position == 1
    reopened.home()
    assert reopened.state.logical_position == 0
    reopened._save(position_valid=False, reason="motion_failed_or_interrupted")
    before = (tmp_path / "needle_state.json").read_bytes()
    with pytest.raises(PositionUncertainError, match="HOME is unconfirmed"):
        TrackedNeedle(needle.controller, cfg, state_path=tmp_path / "needle_state.json", allow_home_assumption=False)
    assert (tmp_path / "needle_state.json").read_bytes() == before
    needle.close()


def test_negative_return_and_state_is_invalid_before_dispatch(tmp_path):
    needle, fake, _ = make_needle(tmp_path)
    needle.confirm_home(operator_confirmed=True)
    needle.controller.enable()
    observed = []
    needle.controller.motion_dispatch_callback = lambda: observed.append(
        NeedleStateStore(tmp_path / "needle_state.json").load().position_valid
    )
    needle.move_to(-2)
    needle.return_to_home()
    assert observed == [False, False]
    assert needle.state.logical_position == 0
    assert any("JOG 40 " in line for line in fake.tx_log)
    needle.close()


@pytest.mark.parametrize("scenario", ["movement_timeout", "missing_done", "serial_disconnection"])
def test_failed_motion_never_advances_position(tmp_path, scenario):
    needle, fake, _ = make_needle(tmp_path)
    needle.confirm_home(operator_confirmed=True)
    original_write = fake.write_line
    def fail_on_jog(line, timeout_s, payload_written_callback=None):
        if " JOG " in line:
            fake.scenario = scenario
        return original_write(line, timeout_s, payload_written_callback)
    fake.write_line = fail_on_jog
    with pytest.raises(Exception):
        needle.needle_up()
    saved = NeedleStateStore(tmp_path / "needle_state.json").load()
    assert saved.logical_position == 0
    assert saved.position_valid is False
    with pytest.raises(PositionUncertainError):
        needle.needle_up()
    needle.close()


def test_missing_corrupt_and_stop(tmp_path):
    needle, fake, cfg = make_needle(tmp_path)
    assert needle.position_certain  # demo mode assumes HOME
    needle.confirm_home(operator_confirmed=True)
    fake.moving = True
    needle.stop()
    assert not NeedleStateStore(tmp_path / "needle_state.json").load().position_valid
    needle.close()
    (tmp_path / "needle_state.json").write_text("{bad json", encoding="utf-8")
    with pytest.raises(PositionUncertainError, match="corrupt"):
        NeedleStateStore(tmp_path / "needle_state.json").load()
    backup = NeedleStateStore(tmp_path / "needle_state.json").quarantine_corrupt()
    assert backup.read_text(encoding="utf-8") == "{bad json"
    assert not (tmp_path / "needle_state.json").exists()


@pytest.mark.parametrize("field,value", [("last_updated", "yesterday"), ("last_updated", "2026-10-14T12:00:00"), ("reason", "motion_pending_to_1")])
def test_valid_flag_cannot_hide_incomplete_reference_evidence(tmp_path, field, value):
    needle, fake, cfg = make_needle(tmp_path)
    needle.confirm_home(operator_confirmed=True)
    path = tmp_path / "needle_state.json"
    record = json.loads(path.read_text())
    record[field] = value
    path.write_text(json.dumps(record))
    before = list(fake.tx_log)
    with pytest.raises(PositionUncertainError):
        TrackedNeedle(needle.controller, cfg, state_path=path, allow_home_assumption=False)
    assert fake.tx_log == before
    assert json.loads(path.read_text())[field] == value
    needle.close()


def test_firmware_wiring_and_no_switch_preflight():
    sketch = (ROOT / "arduino/firmware/needle_controller/needle_controller.ino").read_text()
    assert re.search(r"const uint8_t STEP_PIN = 3;", sketch)
    assert re.search(r"const uint8_t DIR_PIN = 4;", sketch)
    assert "digitalWrite(DIR_PIN, logicalOutput(direction < 0) ? HIGH : LOW);" in sketch
    assert "const unsigned long STEP_HIGH_US = 5000UL;" in sketch
    assert "ENABLE_PIN" not in sketch
    assert "UPPER_LIMIT_PIN" not in sketch
    assert "LOWER_LIMIT_PIN" not in sketch
    assert "pinMode(5" not in sketch and "pinMode(6" not in sketch
    cfg = load_arduino_config(ROOT / "arduino/configs/arduino.example.yaml")
    cfg["firmware"]["motion_enabled"] = True
    cfg["signal_interface"]["wiring_reviewed"] = True
    cfg["motor"]["connected_to_axis_for_test_03"] = True
    cfg["needle"].update(steps_per_unit=20, up_step_sign=1)
    cfg["motion"].update(test_02_steps=20, test_02_speed_steps_s=100,
                          maximum_speed_steps_s=100, maximum_acceleration_steps_s2=300)
    cfg["safety"]["emergency_disconnect_documented"] = True
    assert missing_for_test3(cfg, test2_record_valid=True) == []
