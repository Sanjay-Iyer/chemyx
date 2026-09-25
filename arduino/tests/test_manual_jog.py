"""Manual jog tests use only the fake Arduino; never open a physical port."""

from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.config import load_arduino_config
from arduino.python.controller import NeedleController
from arduino.python.errors import PositionUncertainError
from arduino.python.manual_jog import execute_manual_jog, manual_runtime_config, plan_manual_jog
from arduino.python.needle_state import NeedleState, NeedleStateStore


ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "arduino/configs/arduino.example.yaml"


def test_plan_has_exact_historical_signed_pulses_and_bounds():
    up = plan_manual_jog("up", 200)
    down = plan_manual_jog("down", 200)
    assert (up.command, down.command) == ("JOG 200 100", "JOG -200 100")
    assert up.acceleration_steps_s2 == down.acceleration_steps_s2 == 500
    for steps in (0, -200, 200001, True):
        with pytest.raises(ValueError):
            plan_manual_jog("up", steps)
    for speed in (0, 101):
        with pytest.raises(ValueError):
            plan_manual_jog("up", 200, speed)
    with pytest.raises(ValueError):
        plan_manual_jog("up", 200, 100, 0)
    with pytest.raises(ValueError, match="120-second"):
        plan_manual_jog("up", 200, 1)


def test_manual_configuration_is_transient_and_needs_no_axis_calibration():
    cfg = load_arduino_config(EXAMPLE)
    original = deepcopy(cfg)
    manual = manual_runtime_config(cfg, plan_manual_jog("up", 200))
    assert cfg == original
    assert original["firmware"]["motion_enabled"] is False
    assert original["needle"]["steps_per_unit"] is None
    assert manual["firmware"]["motion_enabled"] is True
    assert manual["motion"]["maximum_speed_steps_s"] == 100
    assert manual["motion"]["maximum_acceleration_steps_s2"] == 500
    assert manual["signal_interface"]["signal_inverted"] is False


@pytest.mark.parametrize("direction,signed", [("up", 200), ("down", -200)])
def test_manual_jog_sends_exactly_one_signed_command_without_home(tmp_path, direction, signed):
    cfg = load_arduino_config(EXAMPLE)
    plan = plan_manual_jog(direction, 200)
    fake = FakeArduinoTransport(runtime_configurable=True)
    state_path = tmp_path / "needle_state.json"
    NeedleStateStore(state_path).save(NeedleState(
        logical_position=1, position_valid=True, steps_per_unit=20,
        up_step_sign=1,
    ))
    observed_validity = []
    with NeedleController(
        fake, expected_version="1.2.1", allow_motion=True,
        motion_dispatch_callback=lambda: observed_validity.append(
            NeedleStateStore(state_path).load().position_valid
        ),
    ) as controller:
        controller.configure_runtime(manual_runtime_config(cfg, plan))
        result = execute_manual_jog(controller, plan, state_path=state_path)
    jogs = [line for line in fake.tx_log if " JOG " in line]
    assert len(jogs) == 1
    assert jogs[0].endswith(f"JOG {signed} 100")
    assert not any(" HOME" in line or " MOVE_ABS " in line for line in fake.tx_log)
    assert observed_validity == [False, False]  # ENABLE and JOG both see invalid state.
    assert result.ack_raw == f"ACK {result.sequence} JOG"
    assert result.done_raw.startswith(f"DONE {result.sequence} position_steps={signed} ")
    assert fake.enabled is False
    saved = NeedleStateStore(state_path).load()
    assert saved.logical_position is None
    assert saved.position_valid is False
    assert saved.reason == "manual_jog_untracked"


def test_manual_jog_accepts_missing_or_corrupt_position_state(tmp_path):
    store = NeedleStateStore(tmp_path / "needle_state.json")
    store.invalidate_for_manual_jog()
    assert store.load().position_valid is False
    store.path.write_text("{bad json", encoding="utf-8")
    store.invalidate_for_manual_jog()
    assert store.load().position_valid is False
    backups = list(tmp_path.glob("needle_state.json.corrupt-*.bak"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{bad json"


def test_failed_jog_keeps_position_unknown(tmp_path):
    cfg = load_arduino_config(EXAMPLE)
    plan = plan_manual_jog("up", 200)
    fake = FakeArduinoTransport(runtime_configurable=True, scenario="movement_timeout")
    state_path = tmp_path / "needle_state.json"
    with NeedleController(fake, expected_version="1.2.1", allow_motion=True) as controller:
        controller.configure_runtime(manual_runtime_config(cfg, plan))
        with pytest.raises(PositionUncertainError):
            execute_manual_jog(controller, plan, state_path=state_path)
    assert NeedleStateStore(state_path).load().position_valid is False


@pytest.mark.parametrize("action,signed", [("manual-up", 200), ("manual-down", -200)])
def test_cli_mock_prints_actual_command_and_arduino_replies(tmp_path, action, signed):
    config = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    config["results"]["run_root_dir"] = str(tmp_path)
    config["needle"]["state_path"] = str(tmp_path / "needle_state.json")
    config_path = tmp_path / "arduino.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "arduino/scripts/needle_control.py"),
         action, "--steps", "200", "--config", str(config_path), "--mock"],
        cwd=ROOT, text=True, capture_output=True, timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert f"Signed command: JOG {signed} 100" in proc.stdout
    assert "STEP pulses" in proc.stdout
    assert "Arduino: ACK " in proc.stdout
    assert "Arduino: DONE " in proc.stdout
    state = json.loads((tmp_path / "mock_needle_state.json").read_text(encoding="utf-8"))
    assert state["position_valid"] is False


def test_live_manual_jog_requires_interactive_direction_confirmation_before_port(tmp_path):
    config = yaml.safe_load(EXAMPLE.read_text(encoding="utf-8"))
    config["needle"]["state_path"] = str(tmp_path / "needle_state.json")
    config_path = tmp_path / "arduino.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(ROOT / "arduino/scripts/needle_control.py"),
         "manual-up", "--steps", "200", "--config", str(config_path), "--live"],
        cwd=ROOT, stdin=subprocess.PIPE, text=True, capture_output=True, timeout=15,
    )
    assert proc.returncode != 0
    assert "Interactive confirmation: MANUAL UP 200" in proc.stderr
    assert not (tmp_path / "needle_state.json").exists()
