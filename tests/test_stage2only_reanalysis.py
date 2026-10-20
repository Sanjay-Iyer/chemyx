"""Selected-folder Stage 2 recovery analysis; raw inputs are never edited."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from chemyx_lab import config
from chemyx_lab.analysis import stage2only_reanalysis as analysis

FID = (config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx").read_bytes().replace(b"\r\r\n", b"\r\n")


def snapshot(folder):
    return {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in folder.rglob("*") if p.is_file()}


class Processor:
    def __init__(self):
        self.calls = []

    def __call__(self, command, log_path, timeout):
        self.calls.append((command, log_path, timeout))
        log_path.write_text("Stand-in processor")
        return 0


def inputs(command):
    return [Path(p) for p in command[2:command.index("--config")]]


@pytest.fixture
def run(tmp_path):
    root = tmp_path / "SI6_STAGE2ONLY_RECOVERY"
    raw = root / "nmr_raw"
    raw.mkdir(parents=True)
    for name in ("stage_1_0001.dx", "stage_2_0001.dx", "stage_2_0002.rpc.dx", "stage_2_0005.dx"):
        (raw / name).write_bytes(FID)
    (raw / "stage_2_0002.dx").write_text("interrupted export")
    (raw / "stage_2_0004.dx").write_text("unreadable FID")
    (root / "report.json").write_bytes(b'{"broken report')
    observations = [
        {"stage": "stage_2", "observation": 2, "status": "NMR_SAVED", "path": "OLD_DRIVE/stage_2_0002.rpc.dx"},
        {"stage": "stage_2", "observation": 3, "status": "NMR_MISSING", "error": "raw data unavailable"},
        {"stage": "stage_2", "observation": 5, "status": "NMR_FAILED", "error": "old processing error"}]
    (root / "observations.jsonl").write_text("\n".join(json.dumps(r) for r in observations) + '\n{"partial line')
    live = root / "analysis_live"
    live.mkdir()
    (live / "status.json").write_bytes(b'{"broken analysis state')
    return root, raw


def test_recovers_only_stage2_from_raw_even_with_broken_reports_and_prior_failures(run):
    root, raw = run
    before = snapshot(root)
    processor = Processor()
    assert analysis.main([str(root)], runner=processor) == 1  # partial: observation 4 is truly unreadable
    command, _, timeout = processor.calls[0]
    assert [p.name for p in inputs(command)] == ["stage_2_0001.dx", "stage_2_0002.rpc.dx", "stage_2_0005.dx"]
    assert all(p.parent == raw for p in inputs(command)) and timeout == 3600
    assert all(p.read_bytes() == FID for p in inputs(command))
    out = next((root / "analysis_stage2only").iterdir())
    summary = json.loads((out / "analysis_summary.json").read_text())
    assert summary["status"] == "PARTIAL" and summary["eligible_spectra"] == 3
    assert summary["unusable_observations"] == 1 and not summary["instruments_contacted"]
    rows = list(csv.DictReader((out / "observation_inventory.csv").open()))
    assert next(r for r in rows if r["observation"] == "3")["note"] == "raw data unavailable"
    assert next(r for r in rows if r["observation"] == "5")["eligible"] == "True"
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before
    assert "stage_1_0001.dx" not in " ".join(command)


def test_raw_directory_argument_and_repeated_runs_create_fresh_outputs(run):
    root, raw = run
    processor = Processor()
    for _ in range(2):
        analysis.main([str(raw)], runner=processor)
    folders = list((root / "analysis_stage2only").iterdir())
    assert len(folders) == 2 and folders[0] != folders[1]
    assert all((p / "analysis_summary.json").is_file() for p in folders)
    assert len(processor.calls) == 2


def test_raw_folder_name_case_does_not_change_output_parent_or_dataset(tmp_path):
    raw = tmp_path / "ACTUAL_DATASET" / "NMR_RAW"
    raw.mkdir(parents=True)
    assert analysis.source_folders(raw) == (raw.parent, raw)


def test_copied_generic_raw_folder_needs_no_run_reports_and_uses_selected_dataset(tmp_path):
    folder = tmp_path / "copied_stage2_dataset"
    folder.mkdir()
    for name in ("sample_A.dx", "sample_B.DX"):
        (folder / name).write_bytes(FID)
    nested = folder / "old_outputs"
    nested.mkdir()
    (nested / "unrelated_old_input.dx").write_bytes(FID)
    processor = Processor()
    output_root = tmp_path / "separate_output"
    assert analysis.main([str(folder), "--output-dir", str(output_root)], runner=processor) == 0
    command = processor.calls[0][0]
    assert {p.name for p in inputs(command)} == {"sample_A.dx", "sample_B.DX"}
    assert command[command.index("--phase-method") + 1] == "acme"
    assert command[command.index("--dataset-display-name") + 1] == "copied_stage2_dataset Stage 2"
    assert next(output_root.iterdir()).joinpath("acquisition_metadata.csv").is_file()
    assert not (folder / "analysis_stage2only").exists()


def test_folder_picker_is_used_only_without_an_explicit_directory(tmp_path):
    raw = tmp_path / "selected"
    raw.mkdir()
    (raw / "measurement.dx").write_bytes(FID)
    chosen = []
    def chooser():
        chosen.append(True)
        return raw
    processor = Processor()
    assert analysis.main([], chooser=chooser, runner=processor) == 0
    assert chosen == [True]
    def forbidden():
        pytest.fail("Folder picker opened despite an explicit directory")
    assert analysis.main([str(raw)], chooser=forbidden, runner=processor) == 0


def test_cancelled_picker_makes_no_output_and_runs_no_processor(tmp_path):
    processor = Processor()
    assert analysis.main([], chooser=lambda: None, runner=processor) == 0
    assert not processor.calls


def test_stage1_only_or_empty_folder_never_falls_back_to_an_unrelated_dataset(tmp_path):
    raw = tmp_path / "only_phase1"
    raw.mkdir()
    (raw / "stage_1_0001.dx").write_bytes(FID)
    processor = Processor()
    assert analysis.main([str(raw)], runner=processor) == 2 and not processor.calls
    out = next((raw / "analysis_stage2only").iterdir())
    summary = json.loads((out / "analysis_summary.json").read_text())
    assert summary["status"] == "NO_DATA" and summary["eligible_spectra"] == 0


def test_bad_metadata_never_becomes_filename_or_mtime_timing(tmp_path):
    raw = tmp_path / "metadata_missing"
    raw.mkdir()
    (raw / "sample(sequence-1300).dx").write_bytes(b"\n".join(
        line for line in FID.splitlines() if not line.startswith((b"##LONG DATE=", b"##$DATE=", b"##$TIMESTAMP=", b"##TIMESTAMP="))))
    processor = Processor()
    assert analysis.main([str(raw)], runner=processor) == 2 and not processor.calls
    out = next((raw / "analysis_stage2only").iterdir())
    assert "No authoritative JCAMP" in (out / "ANALYSIS_SUMMARY.txt").read_text()


@pytest.mark.parametrize("failure", ["exit", "exception", "timeout"])
def test_processing_failure_is_recorded_without_touching_raw_or_live_results(run, failure):
    root, _ = run
    before = snapshot(root)
    def processor(*_):
        if failure == "exception":
            raise MemoryError("processor failed")
        if failure == "timeout":
            raise subprocess.TimeoutExpired("processor", 3600)
        return 7
    assert analysis.main([str(root)], runner=processor) == 1
    out = next((root / "analysis_stage2only").iterdir())
    assert json.loads((out / "analysis_summary.json").read_text())["status"] == "FAILED"
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before


def test_dataset_label_is_normalized_without_repeating_stage2(tmp_path):
    folder = tmp_path / "data"
    folder.mkdir()
    (folder / "measurement.dx").write_bytes(FID)
    processor = Processor()
    assert analysis.main([str(folder), "--dataset-display-name", "  Actual   Run Stage 2  "], runner=processor) == 0
    command = processor.calls[0][0]
    assert command[command.index("--dataset-display-name") + 1] == "Actual Run Stage 2"


def test_no_instrument_or_experiment_controller_interfaces_are_imported():
    for forbidden in ("controller_session", "PositionLedger", "DoseGuard", "Pump", "PortProcessLock"):
        assert not hasattr(analysis, forbidden)


def test_actual_script_processes_selected_data_and_keeps_source_unchanged(tmp_path):
    root = tmp_path / "STAGE2_RECOVERY_SOURCE"
    raw = root / "nmr_raw"
    raw.mkdir(parents=True)
    (raw / "stage_2_0001.dx").write_bytes(FID)
    (raw / "stage_1_0001.dx").write_text("must not process Phase 1")
    (root / "report.json").write_bytes(b'{"damaged original report')
    before = snapshot(root)
    proc = subprocess.run([sys.executable, str(config.REPO_ROOT / "scripts/07_analyze_stage2only.py"), str(root)],
        cwd=config.REPO_ROOT, capture_output=True, text=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = next((root / "analysis_stage2only").iterdir())
    summary = json.loads((out / "analysis_summary.json").read_text())
    assert summary["eligible_spectra"] == 1 and summary["status"] == "COMPLETE"
    assert summary["dataset_display_name"] == "STAGE2_RECOVERY_SOURCE Stage 2"
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in before} == before
    manifests = list((out / "stage_2").rglob("*target_peak_plot_manifest.csv"))
    assert manifests and list((out / "stage_2").rglob("*.png"))
    rows = list(csv.DictReader(manifests[0].open(encoding="utf-8")))
    assert rows and {r["dataset"] for r in rows} == {summary["dataset_display_name"]}
    assert all(r["visible_title"].startswith(summary["dataset_display_name"] + " ") for r in rows)
    titles = {}
    for row in rows:
        path = Path(row["figure"])
        titles.setdefault(path.with_suffix(""), set()).add(row["visible_title"])
    assert all(len(visible) == 1 for visible in titles.values())
