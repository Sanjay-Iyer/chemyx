"""14: both full clocks, boundary dose, existing reference and 12's NMR path."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("full_legacy_test", ROOT / "scripts/14_full_stage1_stage2.py")
full = importlib.util.module_from_spec(spec)
spec.loader.exec_module(full)


@pytest.fixture
def cfg():
    cfg = full.load_config()
    cfg["analysis"]["enabled"] = False
    cfg["output"]["terminal_logging"] = False
    return cfg


def test_full_default_mock_clocks_raw_files_and_single_boundary_dose(cfg, tmp_path):
    clock, captured = full.demo.SimulatedClock(), {}
    @contextmanager
    def factory(*args, **kwargs):
        with full.demo.setup_instruments(*args, **kwargs) as rig:
            original_prepare = rig.prepare
            def prepare():
                captured["prepare_count"] = captured.get("prepare_count", 0) + 1
                original_prepare()
            rig.prepare = prepare
            yield rig
            captured.update(pump=list(rig.pump.ser.tx_log), needle=list(rig.controller.transport.tx_log),
                            retained=rig.retained.copy())
    result = full.run_experiment(cfg, mock=True, clock=clock, output_dir=tmp_path / "run", rig_factory=factory)
    first, second = result["stages"]
    assert result["status"] == "COMPLETE" and result["channel_2_status"] == "COMPLETED"
    assert [s["nmr_saved"] for s in result["stages"]] == [24, 13]
    assert [r["scheduled_seconds"] for r in first["observations"]] == list(range(0, 172800, 7200))
    assert [r["scheduled_seconds"] for r in second["observations"]] == list(range(0, 21601, 1800))
    assert first["elapsed_seconds"] == 172800
    assert result["dose_started_elapsed_seconds"] == first["completed_elapsed_seconds"]
    assert second["started_elapsed_seconds"] == result["dose_completed_elapsed_seconds"]
    assert second["elapsed_seconds"] == 21600 + 452 + 80
    assert captured["prepare_count"] == 1
    assert captured["retained"] == pytest.approx({1: 0, 2: 3.14})
    assert [line.strip() for line in captured["pump"] if line.startswith("2 start")] == ["2 start 0"]
    assert all(r["physical_cleanup"] == "COMPLETE" and r["method"] == "legacy_simple" for r in result["observations"])
    assert [r["stage"] for r in result["observations"]] == ["stage_1"] * 24 + ["stage_2"] * 13
    for stage, expected in [("stage_1", 24), ("stage_2", 13)]:
        files = list((tmp_path / "run" / stage / "nmr_raw").glob(f"{stage}_*.dx"))
        assert len(files) == expected
        assert all("##END=" in path.read_text(encoding="utf-8") for path in files)
    assert "set-home" not in "\n".join(captured["needle"]).lower()


@pytest.mark.parametrize("failure", ["nmr", "analysis", "bad_analysis_config", "relay"])
def test_review_and_nmr_errors_do_not_gate_either_clock_or_boundary_dose(cfg, tmp_path, failure):
    cfg["analysis"]["enabled"] = True
    calls = []
    def launch(run, snapshot, raw, emit):
        calls.append((run, yaml.safe_load(snapshot.read_text(encoding="utf-8"))))
        if failure == "analysis":
            raise ImportError("No numerical dependencies")
        (run / "analysis").mkdir()
        (run / "analysis/analysis_summary.json").write_text('{bad')
        return object()  # Even a broken optional progress-relay object is harmless.
    if failure == "bad_analysis_config":
        cfg["analysis"]["target_peak"] = {"not_a_valid_parameter": 1}
    def acquire(*args, **kwargs):
        raise RuntimeError("NMR unreachable")
    result = full.run_experiment(cfg, mock=True, output_dir=tmp_path / "run", analysis_launcher=launch,
                                 **({"acquisition": acquire} if failure == "nmr" else {}))
    assert result["status"] == ("COMPLETE_NO_SPECTRA" if failure == "nmr" else "COMPLETE")
    assert result["channel_2_status"] == "COMPLETED"
    assert [stage["attempted_observations"] for stage in result["stages"]] == [24, 13]
    assert all(stage["elapsed_seconds"] >= stage["duration_seconds"] for stage in result["stages"])
    assert len(calls) == 2
    assert calls[0][1]["analysis"]["dataset_display_name"].endswith("stage_1")
    assert calls[1][1]["analysis"]["dataset_display_name"].endswith("stage_2")


def test_late_nmr_recovery_crosses_boundary_in_one_session(cfg, tmp_path):
    first = None
    def acquire(nmr, path, *, clock, emit):
        nonlocal first
        if first is None:
            first = path
            return dict(status="NMR_FAILED", unresolved_start=True)
        result = dict(status="NMR_MISSING")
        if path.name == "stage_2_0001.dx":
            first.parent.mkdir(parents=True, exist_ok=True)
            first.write_bytes(full.demo.FIXTURE.read_bytes())
            result["recovered_previous"] = dict(original_path=str(first), path=str(first))
        return result
    result = full.run_experiment(cfg, mock=True, output_dir=tmp_path / "run", acquisition=acquire)
    assert result["status"] == "COMPLETE"
    assert result["stages"][0]["nmr_saved"] == 1
    assert result["observations"][0]["status"] == "NMR_SAVED"
    saved = json.loads((tmp_path / "run/stage_1/summary.json").read_text())
    assert saved["observations"][0]["status"] == "NMR_SAVED"


def test_physical_failure_at_dose_prevents_stage2_and_never_retries(cfg, tmp_path):
    doses = []
    @contextmanager
    def factory(*args, **kwargs):
        with full.demo.setup_instruments(*args, **kwargs) as rig:
            def dose():
                doses.append(True)
                raise RuntimeError("Pump failed")
            rig.dose = dose
            yield rig
    result = full.run_experiment(cfg, mock=True, output_dir=tmp_path / "run", rig_factory=factory)
    assert result["status"] == "FAILED" and len(doses) == 1
    assert result["channel_2_status"] == "ATTEMPTED_UNCERTAIN"
    assert result["stages"][1]["attempted_observations"] == 0


def test_old_results_are_not_admission_and_existing_output_is_preserved(cfg, tmp_path):
    (tmp_path / "RESERVED.json").write_text('{"status":"RESERVED"}')
    output = tmp_path / "run"
    full.run_experiment(cfg, mock=True, output_dir=output)
    with pytest.raises(FileExistsError):
        full.run_experiment(cfg, mock=True, output_dir=output)
    assert full.run_experiment(cfg, mock=True, output_dir=tmp_path / "rerun")["status"] == "COMPLETE"


@pytest.mark.parametrize("change", ["missing_stage", "wrong_order", "disabled_dose", "overflow"])
def test_invalid_full_schedule_is_rejected(cfg, tmp_path, change):
    if change == "missing_stage":
        cfg["stages"].pop()
    elif change == "wrong_order":
        cfg["stages"].reverse()
    elif change == "disabled_dose":
        cfg["channel_2"]["enabled"] = False
    else:
        cfg["stages"][0]["duration_minutes"] = 1e308
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(cfg))
    with pytest.raises(ValueError):
        full.load_config(path)


def test_mock_cli_has_no_network_or_numerical_imports(cfg, tmp_path):
    config = tmp_path / "config.yaml"
    config.write_text(yaml.safe_dump(cfg))
    code = '''
import importlib.abc, runpy, socket, sys
class BlockScience(importlib.abc.MetaPathFinder):
    def find_spec(self, name, *args):
        if name.split('.')[0] in {'numpy', 'scipy', 'matplotlib', 'nmrglue'}:
            raise AssertionError('Instrument process imported ' + name)
sys.meta_path.insert(0, BlockScience())
def blocked(*args, **kwargs):
    raise AssertionError('Mock attempted real network')
socket.create_connection = blocked
sys.path.insert(0, 'scripts')
sys.argv = ['scripts/14_full_stage1_stage2.py', '--mock', '--config', sys.argv[1], '--output-dir', sys.argv[2]]
runpy.run_path('scripts/14_full_stage1_stage2.py', run_name='__main__')
'''
    result = subprocess.run([sys.executable, "-c", code, str(config), str(tmp_path / "cli")], cwd=ROOT,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
