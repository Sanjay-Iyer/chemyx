"""HOME recovery on the production protocol, using fake serial only."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import sys
import subprocess
from types import SimpleNamespace
import yaml

import pytest

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.config import load_arduino_config
from arduino.python.controller import NeedleController
from arduino.python.errors import LiveExecutionBlocked, MotionInterlockError, PositionUncertainError
from arduino.python.home_recovery import restore_home
from arduino.python.needle_state import NeedleState, NeedleStateStore, TrackedNeedle

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def rig(tmp_path):
    cfg = load_arduino_config(ROOT / "arduino/configs/arduino_real_COM3.yaml")
    cfg["results"]["run_root_dir"] = str(tmp_path / "runs")
    cfg["needle"]["state_path"] = str(tmp_path / "state.json")
    cfg["firmware"]["motion_enabled"] = True
    fake = FakeArduinoTransport(runtime_configurable=True)
    controller = NeedleController(fake, expected_version="1.2.1", command_timeout_s=.05, allow_motion=False)
    controller.open()
    yield cfg, controller, fake, NeedleStateStore(tmp_path / "state.json")
    controller.close()


def state(position=1, valid=True, reason=None):
    return NeedleState(logical_position=position, position_valid=valid,
                       reason=reason or ("motion_completed" if valid else "motion_failed_or_interrupted"),
                       last_updated=datetime.now(timezone.utc).isoformat(), steps_per_unit=200, up_step_sign=1)


def events(cfg):
    paths = list(Path(cfg["results"]["run_root_dir"]).glob("*/operation_journal.jsonl"))
    return [json.loads(line) for path in paths for line in path.read_text().splitlines()]


def assert_no_motion(fake):
    assert not any(line.split()[1] in {"JOG", "MOVE_ABS", "HOME", "ENABLE", "CONFIG_IO", "CONFIG_LIMITS", "CONFIG_APPLY"} for line in fake.tx_log)


@pytest.mark.parametrize("position", [0, 1])
def test_known_home_moves_absolutely_and_persists_across_restart(rig, position, capsys):
    cfg, controller, fake, store = rig
    store.save(state(position))
    prompts = []
    result = restore_home(controller, cfg, store.path, confirm=lambda text: prompts.append(text) or True, mode="mock")
    assert len(prompts) == 1 and "UNKNOWN" not in prompts[0]
    assert result == store.load() and result.logical_position == 0 and result.position_valid
    assert sum(" JOG " in line for line in fake.tx_log) == int(position == 1)
    if position:
        assert any(" JOG -200 " in line for line in fake.tx_log)
    assert not store.home_pending_path.exists()
    reopened = TrackedNeedle(controller, cfg, state_path=store.path)
    assert reopened.state.logical_position == 0 and reopened.position_certain
    text = capsys.readouterr().out
    assert "ARDUINO HOME COMPLETE" in text and "Active position fault: CLEARED" in text
    assert events(cfg)[-1]["recovery_basis"] == "completed_absolute_movement"


@pytest.mark.parametrize("kind", ["missing", "invalid", "corrupt", "demo", "pending", "calibration_changed"])
def test_unknown_home_establishes_confirmed_reference_without_movement(rig, kind, tmp_path):
    cfg, controller, fake, store = rig
    if kind == "corrupt": store.path.write_text("{original corrupt bytes")
    elif kind != "missing":
        prior = state(valid=False)
        if kind == "demo": prior = state(reason="assumed_home_demo")
        if kind == "calibration_changed": prior = replace(state(), steps_per_unit=20)
        if kind == "pending":
            prior = state()
            store.home_pending_path.write_text('{"previous_interrupted_recovery":true}')
        store.save(prior)
    old_journal = tmp_path / "old_experiment_journal.jsonl"
    old_journal.write_text('{"event_type":"needle_fault","fault":"position_unknown"}\n')
    history = old_journal.read_bytes()
    unrelated = tmp_path / "dose_receipt.json"
    unrelated.write_text('{"dose":"RESERVED","pump_uncertain":true}')
    protected = unrelated.read_bytes()
    prompts = []
    restore_home(controller, cfg, store.path, confirm=lambda text: prompts.append(text) or True, mode="mock")
    assert len(prompts) == 1 and "Needle position is UNKNOWN" in prompts[0]
    assert_no_motion(fake)
    result = store.load()
    assert result.logical_position == 0 and result.position_valid and result.reason == "operator_confirmed_home"
    assert not store.home_pending_path.exists()
    assert old_journal.read_bytes() == history and unrelated.read_bytes() == protected
    assert events(cfg)[-1]["recovery_basis"] == "operator_confirmed_reference"
    assert not events(cfg)[-1]["physical_position_measured"]
    assert TrackedNeedle(controller, cfg, state_path=store.path).position_certain
    if kind == "corrupt":
        backup, = store.path.parent.glob("state.json.corrupt-*.bak")
        assert backup.read_text() == "{original corrupt bytes"


@pytest.mark.parametrize("kind", ["known", "unknown", "corrupt"])
def test_declined_confirmation_does_not_move_reset_or_quarantine(rig, kind):
    cfg, controller, fake, store = rig
    if kind == "corrupt": store.path.write_text("{bad")
    else: store.save(state(valid=kind == "known"))
    before = store.path.read_bytes()
    with pytest.raises(LiveExecutionBlocked):
        restore_home(controller, cfg, store.path, confirm=lambda _: False)
    assert store.path.read_bytes() == before and not store.home_pending_path.exists()
    assert_no_motion(fake)
    assert not list(store.path.parent.glob("*.bak"))


@pytest.mark.parametrize("fault", ["MOTOR_TIMEOUT", "LIMIT_UP_ACTIVE", "BOTH_LIMITS_ACTIVE", "STOP_FAILED", "INJECTED"])
def test_unrelated_controller_faults_are_not_cleared(rig, fault):
    cfg, controller, fake, store = rig
    store.save(state(valid=False))
    original = store.path.read_bytes()
    fake.fault = fault
    with pytest.raises(MotionInterlockError):
        restore_home(controller, cfg, store.path, confirm=lambda _: pytest.fail("prompted in unsafe state"))
    assert fake.fault == fault and store.path.read_bytes() == original
    assert not any("CLEAR_FAULT" in line for line in fake.tx_log)
    assert_no_motion(fake)


@pytest.mark.parametrize("problem", ["moving", "limit", "communication"])
def test_active_motion_limit_or_communication_failure_rejects_before_confirmation(rig, monkeypatch, problem):
    cfg, controller, fake, store = rig
    store.save(state(valid=False))
    before = store.path.read_bytes()
    if problem == "moving": fake.moving = True
    if problem == "limit":
        status = controller.status
        monkeypatch.setattr(controller, "status", lambda: dict(status(), limit_up="true"))
    if problem == "communication": fake.scenario = "serial_disconnection"
    with pytest.raises(Exception):
        restore_home(controller, cfg, store.path, confirm=lambda _: pytest.fail("unsafe prompt"))
    assert store.path.read_bytes() == before
    assert_no_motion(fake)


def test_restored_communication_latch_requires_manual_reference_and_is_journaled(rig):
    cfg, controller, fake, store = rig
    store.save(state())  # Firmware loss invalidates even a formerly valid estimate.
    fake.fault = "COMMUNICATION_LOSS"
    prompts = []
    restore_home(controller, cfg, store.path, confirm=lambda text: prompts.append(text) or True)
    assert "UNKNOWN" in prompts[0] and fake.fault == "NONE"
    assert any("CLEAR_FAULT" in line for line in fake.tx_log)
    assert_no_motion(fake)
    assert any(e.get("original_controller_fault") == "COMMUNICATION_LOSS" for e in events(cfg))


@pytest.mark.parametrize("reason", ["stop_status_unknown", "manual_stop_failed", "home_recovery_stop_failed", "manual_stop_during_possible_motion"])
def test_unresolved_stop_interlock_cannot_be_cleared_by_home(rig, reason):
    cfg, controller, fake, store = rig
    store.save(state(valid=False, reason=reason))
    before = store.path.read_bytes()
    with pytest.raises(MotionInterlockError, match="STOP"):
        restore_home(controller, cfg, store.path, confirm=lambda _: pytest.fail("STOP bypass"))
    assert store.path.read_bytes() == before and not fake.tx_log


@pytest.mark.parametrize("failure", ["write", "readback", "final_status", "journal", "invalidation_write"])
def test_failed_verification_never_reports_success_or_allows_startup(rig, monkeypatch, failure, capsys):
    cfg, controller, fake, store = rig
    store.save(state(valid=False))
    original_save, original_load, original_status = NeedleStateStore.save, NeedleStateStore.load, controller.status
    triggered = []
    def save(self, value):
        if failure == "write" and value.position_valid:
            raise OSError("TEST reference write failed")
        if failure == "invalidation_write" and triggered and not value.position_valid:
            raise OSError("TEST invalidation unavailable")
        return original_save(self, value)
    def load(self):
        value = original_load(self)
        if failure == "readback" and value.position_valid and not triggered:
            triggered.append(True)
            return replace(value, logical_position=1)
        return value
    def status():
        if failure in ("final_status", "invalidation_write") and original_load(store).position_valid and not triggered:
            triggered.append(True)
            raise RuntimeError("TEST final status failed")
        return original_status()
    monkeypatch.setattr(NeedleStateStore, "save", save)
    monkeypatch.setattr(NeedleStateStore, "load", load)
    monkeypatch.setattr(controller, "status", status)
    if failure == "journal":
        from arduino.python import home_recovery
        append = home_recovery.OperationJournal.append
        def fail_complete(self, event_type, **fields):
            if event_type == "needle_home_completed": raise OSError("TEST journal failure")
            return append(self, event_type, **fields)
        monkeypatch.setattr(home_recovery.OperationJournal, "append", fail_complete)
    with pytest.raises(Exception):
        restore_home(controller, cfg, store.path, confirm=lambda _: True)
    assert "ARDUINO HOME COMPLETE" not in capsys.readouterr().out
    assert store.home_pending_path.exists()
    with pytest.raises(PositionUncertainError, match="incomplete"):
        TrackedNeedle(controller, cfg, state_path=store.path)
    assert_no_motion(fake)


def load_cli(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "arduino/scripts"))
    common_spec = importlib.util.spec_from_file_location("_common", ROOT / "arduino/scripts/_common.py")
    common = importlib.util.module_from_spec(common_spec)
    monkeypatch.setitem(sys.modules, "_common", common)
    common_spec.loader.exec_module(common)
    spec = importlib.util.spec_from_file_location("needle_home_cli", ROOT / "arduino/scripts/needle_control.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("action", ["home", "set-home", "confirm-home", "return-home"])
@pytest.mark.parametrize("answer", ["y", "n"])
def test_cli_single_confirmation_with_real_fake_protocol_and_no_runtime_setup_on_unknown(rig, monkeypatch, action, answer):
    cfg, controller, fake, store = rig
    store.save(state(valid=False))
    original = store.path.read_bytes()
    module = load_cli(monkeypatch)
    @contextmanager
    def session(*args, **kwargs):
        assert not kwargs["allow_motion"] and not kwargs["apply_runtime_config"]
        yield controller
    monkeypatch.setattr(module, "load_arduino_config", lambda _: cfg)
    monkeypatch.setattr(module, "controller_session", session)
    prompts = []
    monkeypatch.setattr("builtins.input", lambda text: prompts.append(text) or answer)
    if answer == "n":
        with pytest.raises(LiveExecutionBlocked): module.main([action, "--live"])
        assert store.path.read_bytes() == original
    else:
        assert module.main([action, "--live"]) == 0
        assert store.load().position_valid
    assert len(prompts) == 1
    assert_no_motion(fake)


def test_successful_explicit_stop_resolves_only_stop_verification_then_home(rig, monkeypatch):
    cfg, controller, fake, store = rig
    store.save(state(valid=False, reason="manual_stop_failed"))
    module = load_cli(monkeypatch)
    @contextmanager
    def session(*args, **kwargs): yield controller
    monkeypatch.setattr(module, "load_arduino_config", lambda _: cfg)
    monkeypatch.setattr(module, "controller_session", session)
    monkeypatch.setattr("builtins.input", lambda _: pytest.fail("STOP must not prompt"))
    assert module.main(["stop", "--live"]) == 0
    assert not store.load().position_valid and store.load().reason == "stop_verified_position_unknown"
    restore_home(controller, cfg, store.path, confirm=lambda _: True)
    assert store.load().position_valid


def test_home_record_survives_program_restart_and_normal_experiment_startup(rig, tmp_path):
    cfg, controller, fake, store = rig
    mock_store = NeedleStateStore(Path(cfg["results"]["run_root_dir"]) / "mock_needle_state.json")
    mock_store.save(state(valid=False))
    config_path = tmp_path / "arduino.yaml"
    config_path.write_text(yaml.safe_dump({key: value for key, value in cfg.items() if not key.startswith("_")}))
    for action in ("home", "status"):
        result = subprocess.run([sys.executable, "-B", str(ROOT / "arduino/scripts/needle_control.py"), action,
                                 "--mock", "--config", str(config_path)], capture_output=True, text=True, stdin=subprocess.DEVNULL)
        assert result.returncode == 0, result.stdout + result.stderr
    assert mock_store.load().position_valid and mock_store.load().logical_position == 0
    controller.allow_motion = True
    controller.configure_runtime(cfg)
    needle = TrackedNeedle(controller, cfg, state_path=mock_store.path)
    from chemyx_lab.workflows.three_instrument_si6 import home_and_raise
    service = SimpleNamespace(needle=needle, assert_pump_idle=lambda: None, record=lambda *a, **k: None,
                              move_needle=lambda label: needle.move_to(cfg["needle"]["up_position"]))
    home_and_raise(service)
    assert needle.state.logical_position == 1


def test_existing_motion_guard_cannot_be_overridden_by_home(rig):
    cfg, controller, fake, store = rig
    store.save(state())
    controller.motion_guard = lambda: False
    with pytest.raises(MotionInterlockError):
        restore_home(controller, cfg, store.path, confirm=lambda _: True)
    assert not any(" JOG " in line for line in fake.tx_log)
    assert store.home_pending_path.exists()


def test_motion_starting_during_confirmation_rejects_reference_reset(rig):
    cfg, controller, fake, store = rig
    store.save(state(valid=False))
    original = store.path.read_bytes()
    def confirm(_):
        fake.moving = True
        return True
    with pytest.raises(MotionInterlockError, match="active motion"):
        restore_home(controller, cfg, store.path, confirm=confirm)
    assert store.path.read_bytes() == original
    assert_no_motion(fake)
