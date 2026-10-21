"""needle_control.py + scripts 15/16: shared plain-integer needle position, no lockouts (mock only)."""
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python import simple_needle
from arduino.python.controller import NeedleController
from arduino.python.errors import DoneTimeout
from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.workflows import simple_rig
from chemyx_lab.workflows.simple_rig import NeedleError, SimpleRig, SimulatedClock, close_logger, make_logger
from chemyx_lab.workflows.stage2_legacy_nmr import MockLegacyClient

ROOT = Path(__file__).resolve().parents[1]
NEEDLE_CONTROL = ROOT / "arduino/scripts/needle_control.py"
sys.path[:0] = [str(ROOT / "scripts"), str(ROOT / "arduino/scripts")]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage2 = load("simple_stage2_test", ROOT / "scripts/15_stage2_simple.py")
check = load("simple_check_test", ROOT / "scripts/16_3_instrument_check_simple.py")
manual = load("simple_needle_control_test", NEEDLE_CONTROL)
SOURCES = ["arduino/python/simple_needle.py", "arduino/scripts/needle_control.py",
           "chemyx_lab/workflows/simple_rig.py", "scripts/15_stage2_simple.py",
           "scripts/16_3_instrument_check_simple.py"]


# ------------------------------------------------------------------ helpers
@pytest.fixture
def home(tmp_path):
    """One temp folder that holds the mock position files for every tool in a test."""
    cfg = yaml.safe_load((ROOT / "arduino/configs/arduino_real_COM3.yaml").read_text(encoding="utf-8"))
    cfg["results"]["run_root_dir"] = str(tmp_path)
    cfg["needle"]["state_path"] = str(tmp_path / "needle_state.json")  # Legacy path, never read.
    (tmp_path / "cfg.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return tmp_path


def cli(home, *args):
    """needle_control.py in its own Python process."""
    proc = subprocess.run([sys.executable, str(NEEDLE_CONTROL), *args, "--mock", "--config", str(home / "cfg.yaml")],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout + proc.stderr


def in_process(home, *args):
    return manual.main([*args, "--mock", "--config", str(home / "cfg.yaml")])


def saved(home):
    """(counter, motor steps) exactly as stored: one signed integer per file."""
    values = []
    for name in ("simple_needle_position_mock.txt", "simple_needle_steps_mock.txt"):
        text = (home / name).read_text(encoding="utf-8")
        assert re.fullmatch(r"-?\d+\n", text), f"{name} must hold only one integer: {text!r}"
        values.append(int(text))
    return tuple(values)


def rig_cfg(script, home, **experiment):
    cfg = script.load_config()
    cfg["arduino"]["position_file"] = str(home / "simple_needle_position.txt")  # Mock -> *_mock.txt here.
    cfg["experiment"].update(experiment)
    return cfg


@pytest.fixture
def rigs():
    made = []

    def factory(*args, **kwargs):
        made.append(SimpleRig(*args, **kwargs))
        return made[-1]
    factory.made = made
    return factory


@pytest.fixture
def board(monkeypatch):
    """One fake Arduino shared by every session in a test, like the real board's RAM."""
    shared = FakeArduinoTransport(runtime_configurable=True)
    monkeypatch.setattr(simple_needle, "FakeArduinoTransport", lambda **kwargs: shared)
    return shared


def jogs(transport):
    return [int(line.split()[2]) for line in transport.tx_log if line.split()[1] == "JOG"]


def files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def repo_state():
    paths = [p for d in ("runs/arduino", "runtime") if (ROOT / d).exists() for p in (ROOT / d).rglob("*") if p.is_file()]
    return {str(p): p.stat().st_mtime_ns for p in paths}


# ------------------------------------------------------- requested tests 1-7
def test_1_manual_counter_survives_separate_processes(home):
    for args, expected in ((["set-home"], 0), (["up"], 1), (["up"], 2), (["down"], 1), (["down"], 0)):
        code, output = cli(home, *args)
        assert code == 0, output
        assert saved(home)[0] == expected
    code, output = cli(home, "status")
    assert code == 0 and "position 0 (HOME)" in output


def test_2_steps_override_moves_those_steps_and_counts_one(home, board, capsys):
    assert in_process(home, "set-home") == 0
    for args, counter in ((["up", "--steps", "50"], 1), (["up", "--steps", "100"], 2), (["down", "--steps", "50"], 1)):
        assert in_process(home, *args) == 0
        assert saved(home)[0] == counter
    assert jogs(board) == [50, 100, -50] and saved(home) == (1, 100)
    assert in_process(home, "home") == 0
    assert jogs(board)[-1] == -100  # The saved displacement, not counter x 200.
    assert saved(home) == (0, 0)


def test_3_failed_move_or_stop_does_not_lock_later_processes(home, monkeypatch, capsys):
    assert cli(home, "set-home")[0] == 0 and cli(home, "up")[0] == 0
    monkeypatch.setattr(NeedleController, "jog", lambda self, steps, speed: (_ for _ in ()).throw(DoneTimeout("no DONE")))
    assert in_process(home, "up") == 1
    assert "check it by eye" in capsys.readouterr().out
    monkeypatch.setattr(NeedleController, "stop", lambda self: (_ for _ in ()).throw(DoneTimeout("no STOP reply")))
    assert in_process(home, "stop") == 0  # Resent without waiting; nothing saved about it.
    assert saved(home) == (1, 200)  # Unchanged: the failed move was not counted.
    assert files(home) == ["cfg.yaml", "simple_needle_position_mock.txt", "simple_needle_steps_mock.txt"]
    monkeypatch.undo()
    for args, expected in ((["set-home"], 0), (["up"], 1), (["down"], 0)):  # New processes, HOME re-established.
        code, output = cli(home, *args)
        assert code == 0, output
        assert saved(home)[0] == expected


def test_4_check_16_complete_and_ends_at_shared_home(home, rigs):
    assert cli(home, "set-home")[0] == 0 and cli(home, "up")[0] == 0  # Left UP by the manual tool.
    before = repo_state()
    passed, results = check.run(rig_cfg(check, home), mock=True, output_dir=home / "run", rig_factory=rigs)
    assert passed and set(results.values()) == {"PASS"}
    rig = rigs.made[0]
    assert jogs(rig.needle.controller.transport) == [-200, 200, -200]  # To saved HOME, UP, DOWN.
    assert rig.pump.contents_ml == 0 and saved(home) == (0, 0)
    assert files(home) == ["cfg.yaml", "run/log.txt", "run/nmr_raw/check_0001.dx",
                           "simple_needle_position_mock.txt", "simple_needle_steps_mock.txt"]
    assert repo_state() == before


def test_5_stage2_15_full_schedule_eight_scans_ends_at_home(home, rigs):
    cfg = rig_cfg(stage2, home)
    assert stage2.observation_times(cfg) == [i * 15 * 60.0 for i in range(25)]
    status, tally = stage2.run(cfg, mock=True, output_dir=home / "run", rig_factory=rigs)
    assert status == "COMPLETE" and tally == dict(slots=25, saved=25, nmr_failed=0, missed=0)
    starts = [payload for method, path, payload in rigs.made[0].nmr.fake.calls if path.endswith("RunExperiment")]
    assert len(starts) == 25 and {payload["NumberOfScans"] for payload in starts} == {8}
    assert saved(home) == (0, 0) and rigs.made[0].returned_home
    assert files(home) == (["cfg.yaml", "run/log.txt"] + [f"run/nmr_raw/stage_2_{i:04d}.dx" for i in range(1, 26)]
                           + ["simple_needle_position_mock.txt", "simple_needle_steps_mock.txt"])


def test_6_legacy_lockout_files_are_ignored(home):
    legacy = {
        "needle_state.json": json.dumps({"version": 1, "logical_position": 0, "home_position": 0, "position_valid": False,
                                         "last_updated": "2026-10-20T12:00:00+00:00", "reason": "stop_status_unknown",
                                         "steps_per_unit": 200, "up_step_sign": 1}),
        "needle_state.json.home_pending.json": json.dumps({"home_pending": True, "position_valid": False}),
        "needle_home_0001/operation_journal.jsonl": json.dumps({"event": "needle_home_failed"}) + "\n",
    }
    for name, text in legacy.items():
        (home / name).parent.mkdir(parents=True, exist_ok=True)
        (home / name).write_text(text, encoding="utf-8")
    for args in (["status"], ["set-home"], ["up"], ["down"], ["home"]):
        code, output = cli(home, *args)
        assert code == 0, output
    assert check.run(rig_cfg(check, home), mock=True, output_dir=home / "c16")[0]
    assert stage2.run(rig_cfg(stage2, home, duration_minutes=30), mock=True, output_dir=home / "c15")[0] == "COMPLETE"
    assert all((home / name).read_text(encoding="utf-8") == text for name, text in legacy.items())  # Never touched.


def test_7_nmr_failure_then_next_scan_succeeds(home, rigs, monkeypatch):
    class FirstScanReturnsNoData(MockLegacyClient):
        def _request(self, method, path, payload=None, params=None):
            reply = super()._request(method, path, payload, params)
            return {"ResultCode": 0, "JDX_FileContents_TD": ""} if path.endswith("ExperimentStatus") and self.counter == 1 else reply
    monkeypatch.setattr(simple_rig, "MockLegacyClient", FirstScanReturnsNoData)
    status, tally = stage2.run(rig_cfg(stage2, home, duration_minutes=30), mock=True, output_dir=home / "run",
                               rig_factory=rigs)
    assert status == "COMPLETE" and tally["nmr_failed"] == 1 and tally["saved"] == 2
    assert files(home / "run") == ["log.txt", "nmr_raw/stage_2_0002.dx", "nmr_raw/stage_2_0003.dx"]
    assert "NMR_MISSING" in (home / "run/log.txt").read_text(encoding="utf-8")
    assert rigs.made[0].pump.contents_ml == 0  # The failed slot's sample was still returned and washed.


# ------------------------------------------------------------ safety extras
def test_sources_use_shared_needle_and_no_legacy_state():
    for name in SOURCES:
        lines = (ROOT / name).read_text(encoding="utf-8").splitlines()
        imports = " ".join(line for line in lines if line.lstrip().startswith(("import ", "from ")))
        for module in ("needle_state", "home_recovery", "manual_jog", "runtime_journal", "runtime_state",
                       "run_lock", "chemyx_position", "dose_guard", "_common"):
            assert module not in imports, f"{name} imports {module}"
        code = " ".join(lines)
        for call in ("NeedleStateStore(", "OperationJournal(", "PortProcessLock(", "controller_session(", "json.dump"):
            assert call not in code, f"{name} calls {call}"
    assert "SimpleNeedle(" in (ROOT / "arduino/scripts/needle_control.py").read_text(encoding="utf-8")
    assert "SimpleNeedle(" in (ROOT / "chemyx_lab/workflows/simple_rig.py").read_text(encoding="utf-8")


def test_unexpected_pump_stop_wording_is_accepted(home, monkeypatch):
    def odd_stop(self, channel=None):
        self.send_command("stop", channel)
        return "?? stopped-ish"
    monkeypatch.setattr(Pump, "stop", odd_stop)
    assert check.run(rig_cfg(check, home), mock=True, output_dir=home / "run")[0]
    assert "stop -> '?? stopped-ish'" in (home / "run/log.txt").read_text(encoding="utf-8")


def test_pump_error_stops_both_before_closing_and_returns_home(home, rigs, monkeypatch):
    monkeypatch.setattr(Pump, "start", lambda self, channel=None, delay=None: "error: syringe stalled")
    status, tally = stage2.run(rig_cfg(stage2, home, duration_minutes=30), mock=True, output_dir=home / "run",
                               rig_factory=rigs)
    rig = rigs.made[0]
    assert status == "STOPPED" and tally["saved"] == 0 and saved(home) == (0, 0)
    log = (home / "run/log.txt").read_text(encoding="utf-8")
    assert log.count("Chemyx CH1: STOP sent") == 2  # At connect, then the emergency stop.
    assert log.index("Arduino: STOP sent") < log.index("Serial ports closed")  # No STOP after closing.
    assert "STOP" in [line.split()[1] for line in rig.needle.controller.transport.tx_log]


def test_ctrl_c_during_transfer_stops_everything_and_returns_home(home, rigs):
    class InterruptingClock(SimulatedClock):
        def sleep(self, seconds):
            if seconds > 60:  # The first 8 mL withdrawal.
                raise KeyboardInterrupt
            super().sleep(seconds)
    status, _ = stage2.run(rig_cfg(stage2, home, duration_minutes=30), mock=True, output_dir=home / "run",
                           clock=InterruptingClock(), rig_factory=rigs)
    assert status == "INTERRUPTED" and saved(home) == (0, 0) and rigs.made[0].returned_home
    log = (home / "run/log.txt").read_text(encoding="utf-8")
    assert "STOPPING: Ctrl+C" in log and "between 0 and 8 mL" in log


def test_failed_needle_move_in_16_keeps_last_saved_position(home, rigs, monkeypatch):
    monkeypatch.setattr(NeedleController, "jog", lambda self, steps, speed: (_ for _ in ()).throw(DoneTimeout("no DONE")))
    passed, results = check.run(rig_cfg(check, home), mock=True, output_dir=home / "run", rig_factory=rigs)
    assert not passed and results["Needle UP/DOWN"] == "FAIL" and results["Needle HOME + ports closed"] == "FAIL"
    assert saved(home) == (0, 0)  # Not counted, not invented.
    assert "Check the needle by eye" in (home / "run/log.txt").read_text(encoding="utf-8")


def test_leftover_firmware_fault_is_cleared_at_connect(home, board):
    board.fault = "MOTOR_TIMEOUT"
    assert check.run(rig_cfg(check, home), mock=True, output_dir=home / "run")[0]
    assert any(line.endswith("CLEAR_FAULT") for line in board.tx_log)


def test_travel_range_is_checked_before_any_motion(home):
    clock = SimulatedClock()
    log = make_logger(home, clock, "range")
    try:
        with SimpleRig(rig_cfg(check, home), mock=True, clock=clock, log=log) as rig:
            with pytest.raises(NeedleError, match="Travel limit"):
                rig.needle_move("up", 1200)  # +6 increments; the range ends at +5.
            assert jogs(rig.needle.controller.transport) == [] and rig.needle.known
    finally:
        close_logger(log)


def test_check_16_cli_exit_codes(home):
    cfg = rig_cfg(check, home)
    good = home / "c16.yaml"
    good.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    run = lambda *args: subprocess.run([sys.executable, "scripts/16_3_instrument_check_simple.py", *args], cwd=ROOT,
                                       capture_output=True, text=True, timeout=120).returncode
    assert run("--mock", "--config", str(good), "--output-dir", str(home / "run")) == 0
    bad = home / "bad.yaml"
    bad.write_text("check: {}\n", encoding="utf-8")
    assert run("--mock", "--config", str(bad), "--output-dir", str(home / "bad_run")) == 2
