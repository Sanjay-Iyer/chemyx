"""Offline Si6 filesystem, provenance, stage isolation and repeat admission."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import csv
import hashlib
import json
import shutil

import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.experiment_paths import allocate_experiment, resolve_output_root
from chemyx_lab.recovery import inspect_run, RecoveryClassification
from chemyx_lab.analysis.final_nmr_summary import summarize_run
from chemyx_lab.workflows import three_instrument_si6 as si6
from chemyx_lab.workflows.dose_guard import DoseReplayBlocked

START = datetime(2026, 10, 15, 23, 59, tzinfo=timezone(timedelta(hours=-4)))


def test_dates_ids_suffixes_and_no_overwrite(tmp_path):
    first = allocate_experiment(tmp_path, "exp1", now=START)
    sentinel = first.root / "keep.txt"
    sentinel.write_bytes(b"original")
    actual = [first, allocate_experiment(tmp_path, "exp1", now=START), allocate_experiment(tmp_path, "exp1", now=START),
              allocate_experiment(tmp_path, "exp2", now=START), allocate_experiment(tmp_path, "exp1", now=START + timedelta(days=1))]
    assert [p.root.name for p in actual] == ["20261015_exp1", "20261015_exp1_1", "20261015_exp1_2", "20261015_exp2", "20261016_exp1"]
    assert [p.suffix for p in actual] == [0, 1, 2, 0, 0]
    assert sentinel.read_bytes() == b"original"
    assert first.started_at_local == START and first.root.name == "20261015_exp1"
    (tmp_path / "20261015_exp1_4").mkdir()
    assert allocate_experiment(tmp_path, "exp1", now=START).suffix == 3
    assert allocate_experiment(tmp_path, "exp1", now=START).suffix == 5


@pytest.mark.parametrize("identity", ["", " ", ".", "..", "../exp1", "a/b", "a\\b", "C:exp1", "exp?1", "exp*1", "a\x00b", "CON", "nul.txt", "COM1", "Lpt9.data", "trailing.", "a" * 81, None])
def test_unsafe_ids_fail_before_creating_output(tmp_path, identity):
    with pytest.raises(ValueError, match="experiment_id"):
        allocate_experiment(tmp_path / "outputs", identity, now=START)
    assert not (tmp_path / "outputs").exists()


def test_valid_meaningful_id_keeps_ledger_spelling(tmp_path):
    assert allocate_experiment(tmp_path, "Si6-48h_sample.2", now=START).root.name == "20261015_Si6-48h_sample.2"


def test_existing_file_is_not_overwritten(tmp_path):
    existing = tmp_path / "20261015_exp1"
    existing.write_bytes(b"existing file, not a directory")
    assert allocate_experiment(tmp_path, "exp1", now=START).suffix == 1
    assert existing.read_bytes() == b"existing file, not a directory"


def test_atomic_allocation_under_concurrency(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as workers:
        allocated = list(workers.map(lambda _: allocate_experiment(tmp_path, "exp1", now=START), range(24)))
    assert sorted(p.suffix for p in allocated) == list(range(24))
    assert len({p.root for p in allocated}) == 24


@pytest.mark.parametrize("relative", ["results/experiments", "results\\experiments"])
def test_relative_root_portable_between_cwd_and_copied_deployment(tmp_path, monkeypatch, relative):
    copied = tmp_path / "Windows deployment" / "chemyx_pump"
    copied.mkdir(parents=True)
    elsewhere = tmp_path / "outside"
    elsewhere.mkdir()
    monkeypatch.setattr(config, "REPO_ROOT", copied)
    monkeypatch.chdir(elsewhere)
    assert resolve_output_root(relative) == copied / "results" / "experiments"
    assert allocate_experiment(relative, "exp1", now=START).root.parent == copied / "results" / "experiments"


def snapshot(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob("*") if p.is_file()}


def prepared(tmp_path, identity="exp1"):
    raw = si6.base.load_si6_config(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml")
    raw["workflow"]["experiment_id"] = identity
    raw["output"]["run_root_dir"] = str(tmp_path / "Windows deployment" / "results" / "experiments").replace("/", "\\")
    recipe = tmp_path / f"{identity}.yaml"
    recipe.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return si6.prepare(recipe, config.REPO_ROOT / "configs/machines/si6_real_COM4.yaml", config.REPO_ROOT / "arduino/configs/arduino_real_COM3.yaml", mock=True)


def test_full_synthetic_repeat_run_safety_and_saved_output_paths(tmp_path, monkeypatch):
    setup = prepared(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(outside)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("Unexpected automated prompt"))
    with si6.open_services(*setup, identity=si6.RunIdentity("si6", True), fast_mock_processing=True) as services:
        result = si6.run_experiment(services)
        assert result.status.value == "completed"
        paths = services.paths
    run = paths.run_dir
    assert all((run / name).is_dir() for name in ("metadata", "logs", "step_1", "step_2", "channel_2_dose", "final_qc"))
    info = json.loads((run / "metadata/experiment_info.json").read_text())
    assert info["experiment_id"] == "exp1" and info["run_sequence_suffix"] == 0
    assert info["output_directory"] == str(run) and info["stage_names"] == ["stage_1", "stage_2"]
    assert info["timezone_offset"] and info["git_commit"]
    assert datetime.fromisoformat(info["started_at_local"]) == datetime.fromisoformat(info["started_at_utc"])
    assert info["status"] == "completed" and info["chemical_completion_verified"]
    assert info["stage_end_reasons"] == dict(stage_1="plateau_reached", stage_2="plateau_reached")
    assert yaml.safe_load((run / "metadata/configuration_snapshot.yaml").read_text())["workflow"]["experiment_id"] == "exp1"
    assert (run / "metadata/software_version.json").is_file()
    for index in (1, 2):
        step = run / f"step_{index}"
        raw = list((step / "nmr_raw").glob("*.dx"))
        assert raw and all(p.name.startswith(f"stage_{index}_") for p in raw)
        processed = list((step / "nmr_processed").glob("*/SIMULATION_ONLY.json"))
        assert len(raw) == len(processed)
        rows = list(csv.DictReader((step / "analysis/time_series.csv").open(newline="")))
        assert len(rows) == len(raw) and {r["stage"] for r in rows} == {f"stage_{index}"}
        for row in rows:
            for field in ("raw_path", "processed_path", "completion_evidence_path", "solvent_evidence_path"):
                assert (run / row[field]).exists() and (run / row[field]).is_relative_to(step)
            assert row["timestamp_source"] == "LONG DATE header"
        assert (step / "logs/operations.jsonl").is_file()
        assert (step / "figures/time_series.svg").is_file()
        assert run.name in (step / "figures/time_series.svg").read_text()
    receipt = json.loads(paths.dose_json.read_text())
    assert receipt["status"] == "CONFIRMED" and receipt["experiment_id"] == "exp1"
    assert receipt["channel"] == 2 and receipt["volume_ml"] == 1.8 and receipt["rate_ml_min"] == 1
    assert receipt["command_evidence"] and Path(receipt["canonical_ledger_path"]).is_file()
    assert (run / "channel_2_dose/operations.jsonl").is_file()
    assert (run / "logs/experiment.log").is_file() and paths.status_json.is_file()
    first_event = json.loads(paths.journal_jsonl.read_text().splitlines()[0])
    assert first_event["run_directory"] == str(run)
    assert inspect_run(run).classification is RecoveryClassification.TERMINAL_COMPLETED
    summary = summarize_run(run)
    assert summary == run / "final_qc/nmr_summary"
    overview = list(csv.DictReader((summary / "nmr_iteration_overview.csv").open(newline="")))
    assert {r["stage"] for r in overview} == {"stage_1", "stage_2"}
    assert all((run / r["raw_dx_path"]).is_file() for r in overview)
    assert not any((run / name).exists() for name in ("raw_nmr", "processed_nmr", "stages", "final", "transition", "operator_status.json"))
    saved = snapshot(run)
    ledger = Path(receipt["canonical_ledger_path"])
    ledger_hash = hashlib.sha256(ledger.read_bytes()).hexdigest()
    monkeypatch.setattr(si6, "FakeArduinoTransport", lambda **_: pytest.fail("Repeat opened transport"))
    for suffix in (1, 2):
        with pytest.raises(DoseReplayBlocked):
            with si6.open_services(*setup, identity=si6.RunIdentity("si6", True)):
                pytest.fail("Repeat admitted")
        repeated = run.with_name(f"{run.name}_{suffix}")
        attempted = json.loads((repeated / "metadata/experiment_info.json").read_text())
        assert attempted["experiment_id"] == "exp1" and attempted["status"] == "failed"
        assert attempted["run_sequence_suffix"] == suffix and not list(repeated.rglob("*.dx"))
    assert snapshot(run) == saved
    assert hashlib.sha256(ledger.read_bytes()).hexdigest() == ledger_hash


def test_missing_data_keeps_stage_evidence_and_timed_fallback_in_new_layout(tmp_path, monkeypatch):
    from test_si6_solvent_fallback import run_mock
    result, services, events, rows = run_mock(tmp_path, monkeypatch, missing_stages={"stage_1", "stage_2"}, empty=True,
                                             duration1=3, interval1=60, duration2=2, interval2=30, reports=True)
    assert result.status.value == "completed" and all(r["peak_area"] == "" for r in rows)
    root = services.paths.run_dir
    info = json.loads((root / "metadata/experiment_info.json").read_text())
    assert info["timed_fallback_occurred"] and not info["chemical_completion_verified"]
    assert info["stage_end_reasons"] == dict(stage_1="timed_fallback_completed", stage_2="timed_fallback_completed")
    for number in (1, 2):
        step = root / f"step_{number}"
        summary = json.loads((step / "analysis/stage_completion.json").read_text())
        assert summary["end_reason"] == "timed_fallback_completed"
        assert list((step / "nmr_processed").glob("*/sample_presence.json"))
        assert (step / "analysis/summary.json").is_file()
        assert (step / "analysis/qc_summary.json").is_file()
        assert not list((step / "figures").glob("*.png"))  # No fabricated acquisition time.
    assert sum(e["event_type"] == "dose_confirmed" for e in events) == 1


def test_real_fid_processing_exports_stay_in_selected_step(tmp_path):
    raw, _, _, nmr = prepared(tmp_path)
    paths = si6.create_identified_run(raw, si6.RunIdentity("si6", True))
    selected = paths.for_stage("stage_2")
    source = selected.raw_dir / "stage_2_0001.dx"
    shutil.copyfile(si6.MOCK_NMR_FIXTURE, source)
    processed = si6.base.run_process_fid_postprocessing(source, selected, paths.run_dir.name,
                                                       tracked_window=si6.tracked_window(raw, nmr),
                                                       peak_tracking=raw["analysis"]["peak_finding"])
    assert processed.is_relative_to(paths.stage_dir("stage_2") / "nmr_processed")
    assert list(processed.glob("*peaks_simple.csv")) and list(processed.rglob("*.png"))
    assert (paths.metadata_dir / "peak_tracking_config.json").is_file()
    phase_plot = next(processed.rglob("phase_before_after.png"))
    manifest = list(csv.DictReader((phase_plot.parent / "plot_manifest.csv").open(newline="")))
    assert manifest and all(row["visible_title"].startswith(paths.run_dir.name + " ") for row in manifest)
    assert not list(paths.stage_dir("stage_1").rglob("*.dx"))


def test_separate_soak_keeps_legacy_layout_and_receipt_contract(tmp_path):
    from test_si6_soak import configured, session
    with session(configured(tmp_path), tmp_path) as services:
        assert services.paths.layout is None
        assert services.paths.raw_dir == services.paths.run_dir / "raw_nmr"
        assert services.paths.journal_jsonl == services.paths.run_dir / "operation_journal.jsonl"
        assert not services.paths.dose_json.exists()
        assert not (services.paths.run_dir / "step_1").exists()
