"""10 demo: accelerated full recipe, raw exports, clock, dose and isolation."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("stage2_demo_entry", ROOT / "scripts/10_stage2only.py")
demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)
PROTECTED = [ROOT / p for p in ("scripts/06_si6_stage2only_run.py", "scripts/08_si6_stage2only_scipy_run.py",
    "chemyx_lab/workflows/si6_stage2only.py", "chemyx_lab/workflows/si6_stage2only_scipy.py",
    "configs/experiments/si6_stage2only_6h.yaml", "configs/experiments/si6_stage2only_scipy_6h.yaml")]
BEFORE = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in PROTECTED}


@pytest.fixture
def cfg():
    value = demo.load_config()
    value["analysis"]["enabled"] = False
    return value


def run(cfg, tmp_path, **kwargs):
    return demo.run_experiment(cfg, mock=True, output_dir=tmp_path / "run", **kwargs)


def test_default_six_hour_25_observations_real_mock_command_sequence(cfg, tmp_path, capsys):
    captures = {}
    @contextmanager
    def rig_factory(*args, **kwargs):
        with demo.setup_instruments(*args, **kwargs) as rig:
            serial, controller = rig.pump.ser, rig.controller.transport
            yield rig
            captures.update(pump=list(serial.tx_log), needle=list(controller.tx_log), retained=dict(rig.retained))
    result = run(cfg, tmp_path, rig_factory=rig_factory)
    assert result["status"] == "COMPLETE"
    assert result["scheduled_observations"] == result["attempted_observations"] == result["nmr_saved"] == 25
    assert [row["scheduled_seconds"] for row in result["observations"]] == list(range(0, 21601, 900))
    assert result["elapsed_seconds"] == 21600 + 452 + 80
    assert all(row["physical_cleanup"] == "COMPLETE" for row in result["observations"])
    files = list((tmp_path / "run/nmr_raw").glob("stage_2_*.dx"))
    assert len(files) == 25 and all(p.read_bytes() == demo.FIXTURE.read_bytes() for p in files)
    assert not any(line.startswith("2 ") for line in captures["pump"])
    assert [s.strip() for s in captures["pump"] if "set volume" in s] == ["1 set volume -8.0", "1 set volume -5.0", "1 set volume 13.0", "1 set volume -5.0", "1 set volume 5.0"] * 25
    assert captures["retained"] == {1: 0, 2: 5}
    output = capsys.readouterr().out
    assert output.index("EXPERIMENT STARTED") < output.index("OBSERVATION 1 / 25") < output.index("NMR: Simulated acquisition") < output.index("Observation 1 COMPLETE")
    assert "[06:00:00] OBSERVATION 25 / 25" in output
    assert "waiting" in output and "NMR saved 25" in output
    assert (tmp_path / "run/experiment.log").read_text() == output
    assert (tmp_path / "run/config_used.yaml").is_file()


def test_channel_two_once_after_entire_time_zero_cleanup_plus_180_seconds(cfg, tmp_path):
    cfg["channel_2"]["enabled"] = True
    times, tx = {}, []
    clock = demo.SimulatedClock()
    @contextmanager
    def factory(*args, **kwargs):
        with demo.setup_instruments(*args, **kwargs) as rig:
            sample, dose = rig.sample, rig.dose
            serial = rig.pump.ser
            def sampled(callback):
                result = sample(callback)
                times.setdefault("time_zero_cleanup", clock.now())
                return result
            def dosed():
                times["dose_start"] = clock.now()
                dose()
            rig.sample, rig.dose = sampled, dosed
            yield rig
            tx.extend(serial.tx_log)
    result = run(cfg, tmp_path, clock=clock, rig_factory=factory)
    assert result["status"] == "COMPLETE" and result["channel_2_status"] == "COMPLETED"
    assert times["dose_start"] == times["time_zero_cleanup"] + 180
    assert [line.strip() for line in tx if line.startswith("2 ") and "set volume" in line] == ["2 set volume 1.86"]
    assert [line.strip() for line in tx if line.startswith("2 start")] == ["2 start 0"]
    assert result["attempted_observations"] == 25


@pytest.mark.parametrize("failure", ["busy", "timeout", "unknown", "lost_start"])
def test_failed_nmr_continues_and_later_measurement_recovers(cfg, tmp_path, failure):
    calls = []
    def acquire(nmr, path, *, clock, emit):
        calls.append(path)
        if len(calls) == 1:
            raise TimeoutError(failure)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(demo.FIXTURE.read_bytes())
        return {"status": "NMR_SAVED", "path": str(path)}
    result = run(cfg, tmp_path, acquisition=acquire)
    assert len(calls) == 25 and result["nmr_failed"] == 1 and result["nmr_saved"] == 24
    assert result["status"] == "COMPLETE" and result["elapsed_seconds"] >= 21600


def test_zero_spectra_are_not_unqualified_success(cfg, tmp_path):
    result = run(cfg, tmp_path, acquisition=lambda *_, **__: {"status": "NMR_MISSING", "reason": "no raw"})
    assert result["status"] == "COMPLETE_NO_SPECTRA" and result["nmr_missing"] == 25
    assert result["elapsed_seconds"] >= 21600


def test_live_runner_retains_one_in_memory_pending_condition_for_all_slots(cfg, tmp_path, monkeypatch):
    sessions = []
    def acquire(nmr, path, *, state, **kwargs):
        sessions.append(state)
        if len(sessions) == 1:
            state["pending"] = {"path": str(path)}
        else:
            assert state.get("pending")
        return {"status": "NMR_FAILED", "reason": "prior START unresolved", "unresolved_start": True}
    @contextmanager
    def mock_rig(*args, **kwargs):
        kwargs["mock"] = True
        with demo.setup_instruments(*args, **kwargs) as rig:
            yield rig
    monkeypatch.setattr(demo, "acquire_nmr", acquire)
    result = demo.run_experiment(cfg, mock=False, output_dir=tmp_path / "run",
                                 clock=demo.SimulatedClock(), rig_factory=mock_rig)
    assert len(sessions) == 25 and all(session is sessions[0] for session in sessions)
    assert result["attempted_observations"] == result["nmr_failed"] == 25
    assert result["elapsed_seconds"] >= 21600 and result["status"] == "COMPLETE_NO_SPECTRA"


def test_later_completion_updates_original_observation_without_double_counting(cfg, tmp_path):
    calls = []
    def acquire(nmr, path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            return {"status": "NMR_FAILED", "requested": True, "unresolved_start": True}
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(demo.FIXTURE.read_bytes())
        result = {"status": "NMR_SAVED", "path": str(path)}
        if len(calls) == 2:
            calls[0].write_bytes(demo.FIXTURE.read_bytes())
            result["recovered_previous"] = {"status": "NMR_SAVED", "path": str(calls[0]), "original_path": str(calls[0])}
        return result
    result = run(cfg, tmp_path, acquisition=acquire)
    assert result["nmr_saved"] == result["attempted_observations"] == 25 and result["nmr_failed"] == 0
    first, second = result["observations"][:2]
    assert first["status"] == "NMR_SAVED" and first["unresolved_start"] is False
    assert first["path"] != second["path"] and first["observation"] == 1 and second["observation"] == 2


@pytest.mark.parametrize("failure", ["launch", "crash", "hung", "bad_json", "bad_counts"])
def test_analysis_has_no_control_of_schedule_or_dose(cfg, tmp_path, failure):
    cfg["analysis"]["enabled"] = True
    cfg["channel_2"]["enabled"] = True
    def launch(run_dir, *_):
        if failure == "launch":
            raise ImportError("NumPy unavailable")
        (run_dir / "analysis").mkdir()
        (run_dir / "analysis/analysis_summary.json").write_text("{bad" if failure == "bad_json" else json.dumps({"status": failure, "stop_run": True,
            "successful_analyses": "wrong" if failure == "bad_counts" else 0, "failed_analyses": 0}))
        return threading.Event()
    result = run(cfg, tmp_path, analysis_launcher=launch)
    assert result["status"] == "COMPLETE" and result["nmr_saved"] == 25
    assert result["channel_2_status"] == "COMPLETED" and result["elapsed_seconds"] >= 21600


def test_dose_not_attempted_after_time_zero_physical_failure(cfg, tmp_path):
    cfg["channel_2"]["enabled"] = True
    class Rig:
        def prepare(self): pass
        def sample(self, fn): raise RuntimeError("needle movement failed")
        def dose(self): pytest.fail("Dose after physical failure")
        def stop(self): pass
    result = run(cfg, tmp_path, rig_factory=lambda *_, **__: __import__("contextlib").nullcontext(Rig()))
    assert result["status"] == "FAILED" and result["channel_2_status"] == "NOT_ATTEMPTED"
    assert result["attempted_observations"] == 1


def test_uncertain_dose_never_repeated(cfg, tmp_path):
    cfg["channel_2"]["enabled"] = True
    doses = []
    @contextmanager
    def factory(*args, **kwargs):
        with demo.setup_instruments(*args, **kwargs) as rig:
            def dose():
                doses.append(1)
                raise RuntimeError("Lost infusion acknowledgment")
            rig.dose = dose
            yield rig
    result = run(cfg, tmp_path, rig_factory=factory)
    assert result["status"] == "FAILED" and doses == [1]
    assert result["channel_2_status"] == "ATTEMPTED_UNCERTAIN" and result["attempted_observations"] == 1


def test_configurable_duration_interval_and_excluded_endpoint(cfg, tmp_path):
    cfg["experiment"].update(duration_minutes=60, interval_minutes=20, include_final_observation=False)
    result = run(cfg, tmp_path)
    assert [r["scheduled_seconds"] for r in result["observations"]] == [0, 1200, 2400]
    assert result["elapsed_seconds"] == 3600


def test_slow_cycle_continues_at_next_valid_scheduled_slot(cfg, tmp_path):
    def slow(nmr, path, *, clock, emit):
        clock.sleep(1600)
        return {"status": "NMR_FAILED"}
    result = run(cfg, tmp_path, acquisition=slow)
    assert result["status"] == "COMPLETE_NO_SPECTRA" and result["skipped_observations"] > 0
    assert result["observations"][-1]["scheduled_seconds"] == 21600
    assert result["attempted_observations"] + result["skipped_observations"] == 25


def test_overrun_past_endpoint_does_not_force_a_late_catchup(cfg, tmp_path):
    def slow(nmr, path, *, clock, emit):
        clock.sleep(22000)
        return {"status": "NMR_FAILED"}
    result = run(cfg, tmp_path, acquisition=slow)
    assert result["status"] == "COMPLETE_NO_SPECTRA"
    assert result["attempted_observations"] == 1 and result["skipped_observations"] == 24
    assert result["elapsed_seconds"] > 21600
    assert [row["scheduled_seconds"] for row in result["observations"]] == [0]


@pytest.mark.parametrize("custom", [None, "  My  New Dataset  "])
def test_snapshot_has_actual_dataset_name_without_altering_source_config(cfg, tmp_path, custom):
    if custom is not None:
        cfg["analysis"]["dataset_display_name"] = custom
    before = deepcopy(cfg)
    run(cfg, tmp_path)
    saved = yaml.safe_load((tmp_path / "run/config_used.yaml").read_text())
    assert saved["analysis"]["dataset_display_name"] == (custom or "stage2only_demo run")
    assert cfg == before


def test_clock_rechecked_after_early_sleep(cfg, tmp_path):
    clock = demo.SimulatedClock()
    sleep = clock.sleep
    clock.sleep = lambda seconds: sleep(min(seconds, 100))
    result = run(cfg, tmp_path, clock=clock)
    assert result["observations"][-1]["acquired_elapsed_seconds"] >= 21600


def test_optional_log_and_config_snapshot_do_not_gate_acquisition(cfg, tmp_path, monkeypatch):
    open_original, write_original = Path.open, Path.write_text
    def opened(path, *args, **kwargs):
        if path.name == "experiment.log": raise PermissionError("log unavailable")
        return open_original(path, *args, **kwargs)
    def written(path, *args, **kwargs):
        if path.name == "config_used.yaml": raise PermissionError("snapshot unavailable")
        return write_original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", opened)
    monkeypatch.setattr(Path, "write_text", written)
    result = run(cfg, tmp_path)
    assert result["status"] == "COMPLETE" and result["nmr_saved"] == 25


def test_preserves_old_results_and_06_08_sources(cfg, tmp_path):
    prior = tmp_path / "old.dx"
    prior.write_bytes(b"historical")
    result = run(cfg, tmp_path)
    assert prior.read_bytes() == b"historical"
    assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in PROTECTED} == BEFORE
    with pytest.raises(FileExistsError):
        run(cfg, tmp_path)


def test_mock_cli_cannot_import_science_or_open_network_or_real_serial(cfg, tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg))
    code = """import sys, socket
class NoScience:
 def find_spec(self, name, *args):
  if name.split('.')[0] in {'numpy','scipy','matplotlib','nmrglue'}: raise ModuleNotFoundError(name)
sys.meta_path.insert(0,NoScience())
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('real network forbidden'))
import serial
serial.Serial=lambda *a,**k: (_ for _ in ()).throw(AssertionError('real serial forbidden'))
import runpy
sys.path.insert(0,'scripts')
sys.argv=['10_stage2only.py','--config',sys.argv[1],'--mock','--output-dir',sys.argv[2]]
runpy.run_path('scripts/10_stage2only.py',run_name='__main__')
"""
    proc = subprocess.run([sys.executable, "-c", code, str(path), str(tmp_path / "cli")], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    result = json.loads((tmp_path / "cli/summary.json").read_text())
    assert result["nmr_saved"] == 25
