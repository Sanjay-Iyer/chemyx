"""09 folder selection, raw recovery, algorithm dispatch and source preservation."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from chemyx_lab import config
from chemyx_lab.analysis import stage2only_scipy_reanalysis as analysis
from test_stage2only_reanalysis import FID, Processor, inputs, snapshot


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "SCIPY_RECOVERY_TEST"
    raw = root / "nmr_raw"
    raw.mkdir(parents=True)
    (raw / "stage_2_0001.dx").write_bytes(FID)
    (raw / "stage_2_0002.dx").write_text("interrupted primary")
    (raw / "stage_2_0002.rpc.dx").write_bytes(FID)
    (raw / "stage_1_0001.dx").write_text("Phase 1 must not be processed")
    (root / "report.json").write_text("{bad report")
    live = root / "analysis_live"
    live.mkdir()
    (live / "status.json").write_text("{bad prior live analysis")
    return root, raw


def test_recovery_selects_stage2_and_explicit_scipy_algorithm_preserves_all_old_files(run):
    root, raw = run
    before = snapshot(root)
    processor = Processor()
    assert analysis.main([str(root)], runner=processor) == 0
    command = processor.calls[0][0]
    assert Path(command[1]).name == "process_stage2only_scipy.py"
    assert {p.name for p in inputs(command)} == {"stage_2_0001.dx", "stage_2_0002.rpc.dx"}
    assert command[command.index("--profile") + 1].endswith("stage2only_scipy.yaml")
    assert command[command.index("--phase-method") + 1] == "acme"
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before
    assert not (root / "analysis_stage2only").exists()
    assert analysis.main([str(raw)], runner=processor) == 0
    assert len(list((root / "analysis_stage2only_scipy").iterdir())) == 2


def test_picker_cancellation_and_stage1_only_do_not_launch_processing(tmp_path):
    calls = Processor()
    assert analysis.main([], chooser=lambda: None, runner=calls) == 0
    (tmp_path / "stage_1_0001.dx").write_bytes(FID)
    assert analysis.main([str(tmp_path)], runner=calls) == 2
    assert not calls.calls


def test_picker_and_dataset_name_are_forwarded(tmp_path):
    (tmp_path / "one.dx").write_bytes(FID)
    processor = Processor()
    assert analysis.main(["--dataset-display-name", "  New   Run Stage 2 "], chooser=lambda: tmp_path, runner=processor) == 0
    command = processor.calls[0][0]
    assert command[command.index("--dataset-display-name") + 1] == "New Run Stage 2"


def test_partial_numerical_summary_is_not_reported_as_complete(run):
    root, _ = run
    def partial(command, log, timeout):
        parent = Path(command[command.index("--output-dir") + 1]) / "stage_2"
        parent.mkdir()
        (parent / "processing_summary.json").write_text('{"status":"PARTIAL","files_processed":1,"files_failed":1}')
        return 0
    assert analysis.main([str(root)], runner=partial) == 1
    out = next((root / "analysis_stage2only_scipy").iterdir())
    assert json.loads((out / "analysis_summary.json").read_text())["status"] == "PARTIAL"


@pytest.mark.parametrize("failure", ["exit", "timeout"])
def test_optional_processing_failures_preserve_raw_and_live_files(run, failure):
    root, _ = run
    before = snapshot(root)
    def processor(*_):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("processor", 600)
        return 9
    assert analysis.main([str(root)], runner=processor) == 1
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before


def test_actual_09_command_processes_real_fid_with_dynamic_primary_and_titles(tmp_path):
    raw = tmp_path / "selected"
    raw.mkdir()
    (raw / "stage_2_0001.dx").write_bytes(FID)
    before = snapshot(raw)
    result = subprocess.run([sys.executable, str(config.REPO_ROOT / "scripts/09_analyze_stage2only_scipy.py"), str(raw)],
        cwd=config.REPO_ROOT, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    out = next((raw / "analysis_stage2only_scipy").iterdir()) / "stage_2"
    summary = json.loads((out / "processing_summary.json").read_text())
    assert summary["primary_area_method"] == "scipy_width95_prominence8"
    assert summary["minimum_prominence_sigma"] == 8 and summary["rel_height"] == .95
    assert summary["raw_inputs_unchanged"] and not summary["analysis_controls_run"]
    rows = list(csv.DictReader((out / "target_peak_measurements.csv").open()))
    assert rows[0]["primary_area_method"] == "scipy_width95_prominence8"
    assert rows[0]["area"] == rows[0]["area_dynamic"]
    manifest = list(csv.DictReader((out / "plot_manifest.csv").open(encoding="utf-8")))
    assert manifest and {r["dataset"] for r in manifest} == {"selected Stage 2"}
    assert all(r["visible_title"].startswith("selected Stage 2 ") for r in manifest)
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before


def test_09_has_no_instrument_authority():
    for name in ("controller_session", "PositionLedger", "DoseGuard", "Pump", "PortProcessLock"):
        assert not hasattr(analysis, name)
