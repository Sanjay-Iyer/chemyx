"""Durable relative manual count shares the absolute automation reference."""
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.config import load_arduino_config
from arduino.python.controller import NeedleController
from arduino.python.errors import MotionInterlockError, PositionUncertainError
from arduino.python.home_recovery import restore_home
from arduino.python.manual_jog import tracked_relative_jog
from arduino.python.needle_state import NeedleStateStore, TrackedNeedle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def rig(tmp_path):
    cfg = load_arduino_config(ROOT / "arduino/configs/arduino_real_COM3.yaml")
    cfg["results"]["run_root_dir"] = str(tmp_path / "runs")
    store = NeedleStateStore(tmp_path / "state.json")
    fake = FakeArduinoTransport(runtime_configurable=True)
    with NeedleController(fake, expected_version="1.2.1", allow_motion=True) as controller:
        controller.configure_runtime(cfg)
        yield cfg, store, fake, controller


def test_relative_before_home_count_and_limits_survive_restart(rig):
    cfg, store, fake, controller = rig
    counts = []
    for direction in ("up", "up", "down", "down", "down"):
        state = tracked_relative_jog(controller, cfg, direction, state_path=store.path)
        counts.append(state.logical_position)
        assert NeedleStateStore(store.path).load() == state and not state.position_valid
    assert counts == [1, 2, 1, 0, -1]
    with pytest.raises(PositionUncertainError):
        TrackedNeedle(controller, cfg, state_path=store.path)
    for _ in range(2):
        tracked_relative_jog(controller, cfg, "down", state_path=store.path)
    before = len(fake.tx_log)
    with pytest.raises(MotionInterlockError, match="travel limit"):
        tracked_relative_jog(controller, cfg, "down", state_path=store.path)
    assert len(fake.tx_log) == before and store.load().logical_position == -3


def test_set_home_never_moves_and_automation_remains_absolute(rig):
    cfg, store, fake, controller = rig
    for _ in range(3):
        tracked_relative_jog(controller, cfg, "up", state_path=store.path)
    before = sum(" JOG " in line for line in fake.tx_log)
    restore_home(controller, cfg, store.path, confirm=lambda _: True, force_reference=True, mode="mock")
    assert sum(" JOG " in line for line in fake.tx_log) == before
    assert store.load().logical_position == 0 and store.load().position_valid
    # restore_home deliberately disarms; normal session opens armed.
    controller.allow_motion = True
    controller.configure_runtime(cfg)
    tracked_relative_jog(controller, cfg, "up", state_path=store.path)
    tracked_relative_jog(controller, cfg, "up", state_path=store.path)
    needle = TrackedNeedle(controller, cfg, state_path=store.path)
    needle.move_to(1)
    assert store.load().logical_position == 1
    needle.move_to(0)
    assert store.load().logical_position == 0


def test_failed_movement_is_not_success_or_a_new_origin(rig, monkeypatch):
    cfg, store, fake, controller = rig
    def interrupted(*_):
        raise KeyboardInterrupt()
    monkeypatch.setattr(controller, "jog", interrupted)
    with pytest.raises(KeyboardInterrupt):
        tracked_relative_jog(controller, cfg, "up", state_path=store.path)
    assert store.load().reason == "manual_relative_pending" and not store.load().position_valid
    with pytest.raises(PositionUncertainError, match="movement uncertain"):
        tracked_relative_jog(controller, cfg, "up", state_path=store.path)


def jog_steps(fake):
    """Signed motor steps of every JOG the fake firmware received."""
    return [int(line.split(" JOG ")[1].split()[0]) for line in fake.tx_log if " JOG " in line]


def test_custom_whole_increment_steps_keep_the_count_and_limits(rig):
    cfg, store, fake, controller = rig
    assert tracked_relative_jog(controller, cfg, "up", state_path=store.path, steps=400).logical_position == 2
    state = tracked_relative_jog(controller, cfg, "down", state_path=store.path, steps=1000)
    assert state.logical_position == -3 and not state.position_valid
    assert jog_steps(fake) == [400, -1000]
    before = len(fake.tx_log)
    with pytest.raises(MotionInterlockError, match="travel limit"):
        tracked_relative_jog(controller, cfg, "down", state_path=store.path, steps=200)
    assert len(fake.tx_log) == before and store.load().logical_position == -3


def test_custom_partial_steps_move_exactly_and_reset_the_count(rig):
    cfg, store, fake, controller = rig
    tracked_relative_jog(controller, cfg, "up", state_path=store.path)
    state = tracked_relative_jog(controller, cfg, "down", state_path=store.path, steps=50)
    assert state.logical_position is None and not state.position_valid
    assert state.reason == "manual_relative_completed"
    # Plain UP/DOWN keep working; the count restarts from this spot.
    assert tracked_relative_jog(controller, cfg, "up", state_path=store.path).logical_position == 1
    assert jog_steps(fake) == [200, -50, 200]


def test_custom_partial_steps_clear_a_saved_home_until_set_home(rig):
    cfg, store, fake, controller = rig
    restore_home(controller, cfg, store.path, confirm=lambda _: True, force_reference=True, mode="mock")
    controller.allow_motion = True
    controller.configure_runtime(cfg)
    state = tracked_relative_jog(controller, cfg, "up", state_path=store.path, steps=400)
    assert state.position_valid and state.logical_position == 2
    state = tracked_relative_jog(controller, cfg, "down", state_path=store.path, steps=30)
    assert not state.position_valid and state.logical_position is None
    assert jog_steps(fake) == [400, -30]
    with pytest.raises(PositionUncertainError):
        TrackedNeedle(controller, cfg, state_path=store.path)  # an experiment refuses to start
    restore_home(controller, cfg, store.path, confirm=lambda _: True, force_reference=True, mode="mock")
    assert store.load().position_valid and store.load().logical_position == 0


@pytest.mark.parametrize("steps", [0, -5, 1601, True, 2.5])
def test_invalid_or_oversized_custom_steps_are_refused_before_any_command(rig, steps):
    cfg, store, fake, controller = rig
    before = len(fake.tx_log)
    with pytest.raises((ValueError, MotionInterlockError)):
        tracked_relative_jog(controller, cfg, "up", state_path=store.path, steps=steps)
    assert len(fake.tx_log) == before and not store.path.exists()
