"""Independent HOME-only counterexamples for chemical completion and admission."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json

import numpy as np
import pytest

from chemyx_lab.analysis.stage_completion import completion_evidence, validate_completion
from chemyx_lab.analysis.stage_measurement import fixed_window_measurement
from chemyx_lab.workflows.dose_guard import DoseGuard, DoseReplayBlocked


def rules(trend):
    value = dict(trend=trend, minimum_points=6, minimum_duration_hours=2,
                 window_points=3, consecutive_confirmations=2,
                 minimum_progress_fraction=0.5, max_window_range_fraction=0.03,
                 max_abs_slope_fraction_per_hour=0.01)
    if trend == "decreasing":
        value["low_fraction"] = 0.03
    return validate_completion(value)


def observations(values):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [dict(peak_area=area, completion_area=area, area_uncertainty=0.001,
                 measurement_valid=True, peak_clear=area > 0,
                 acquired_at=(start + timedelta(hours=i)).isoformat())
            for i, area in enumerate(values)]


def test_validator_zero_stage2_baseline_can_grow_to_sustained_plateau():
    rows = observations([0, 10, 40, 80, 100, 100.2, 100.3, 100.3])
    assert completion_evidence(rows, rules("increasing"))["complete"]


def test_validator_stage2_large_decline_then_flat_low_level_is_not_completion():
    criterion = rules("increasing")
    criterion["minimum_progress_fraction"] = 0.25
    rows = observations([0, 100, 200, 60, 60, 60, 60, 60])
    assert not completion_evidence(rows, criterion)["complete"]


@pytest.mark.parametrize("trend,values", [
    ("increasing", [0, 0, 0, 0, 0, 0, 0, 0]),
    ("increasing", [1, 10, 20, 40, 50, 60, 70, 80]),
    ("increasing", [1, 10, 20, 40, 40, 40, 60, 80]),
    ("decreasing", [100, 80, 0.1, 65, 50, 40, 30, 20]),
    ("decreasing", [100, 80, 50, 30, 15, 10, 9.9, 9.8]),
])
def test_validator_chemical_counterexamples_do_not_complete(trend, values):
    assert not completion_evidence(observations(values), rules(trend))["complete"]


@pytest.mark.parametrize("corruption", ["repeated", "backwards", "invalid", "qc_failed"])
def test_validator_corrupt_or_invalid_stage_evidence_never_completes(corruption):
    rows = observations([100, 50, 10, 2, 1, 1, 1, 1])
    if corruption == "repeated":
        rows[-1]["acquired_at"] = rows[-2]["acquired_at"]
    elif corruption == "backwards":
        rows[-1]["acquired_at"] = rows[0]["acquired_at"]
    elif corruption == "invalid":
        rows[-1]["completion_area"] = float("nan")
    else:
        rows[-1]["measurement_valid"] = False
    assert not completion_evidence(rows, rules("decreasing"))["complete"]


def test_validator_only_one_concurrent_experiment_reservation_wins(tmp_path):
    guards = [DoseGuard(tmp_path, "validation-exp", {"revision": i}, f"run-{i}") for i in range(2)]
    def reserve(guard):
        try:
            guard.reserve()
        except DoseReplayBlocked:
            return "blocked"
        return "reserved"
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(reserve, guards)) == ["blocked", "reserved"]


@pytest.mark.parametrize("status", ["RESERVED", "DISPATCH_INTENT", "CONFIRMED", "torn"])
def test_validator_every_existing_ledger_blocks_fresh_dispatch(tmp_path, status):
    guard = DoseGuard(tmp_path, "validation-exp", {"revision": 1}, "run-original")
    guard.reserve()
    if status in {"DISPATCH_INTENT", "CONFIRMED"}:
        guard.mark("DISPATCH_INTENT")
    if status == "CONFIRMED":
        guard.mark("CONFIRMED", completion_journal_sequence=123)
    if status == "torn":
        guard.path.write_text('{"status":', encoding="utf-8")
    changed = DoseGuard(tmp_path, "validation-exp", {"revision": 2}, "run-restart")
    with pytest.raises(DoseReplayBlocked):
        changed.reserve()


@pytest.mark.parametrize("corruption", ["rejected_candidate", "nan_phase", "wrong_raw"])
def test_validator_invalid_production_trace_cannot_be_counted_as_absence(tmp_path, corruption):
    source = tmp_path / "sample.dx"
    source.write_bytes(b"independent validator raw source")
    processed = tmp_path / "processed"
    audit_dir = processed / "phase_audit" / "sample"
    audit_dir.mkdir(parents=True)
    x = np.linspace(5.3, 6.3, 1001)
    y = 0.001 * np.sin(np.arange(len(x)))
    np.savez(audit_dir / "spectral_evidence.npz", region_ppm=x, regional_quantitative=y)
    meta = dict(raw_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                parameters={"normalization": "none"},
                phase={"method": "stored", "p0_deg": 0, "p1_deg": 0})
    row = dict(target_ppm=5.8, peak_area=0, peak_clear=False,
               qc_failure_reasons="no QC-passing peak in the tracked window")
    if corruption == "rejected_candidate":
        row["rejected_candidate_count"] = 1
    elif corruption == "nan_phase":
        meta["phase"]["p0_deg"] = float("nan")
    else:
        meta["raw_sha256"] = "wrong"
    (audit_dir / "processing_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    reference = dict(peak_clear=True, peak_area=100, completion_area=100,
                     reference_height=100, qc_reference_area=100)
    analysis = dict(integration_window_ppm=0.1, plot_window_ppm=0.5,
                    measurement_qc=dict(noise_multiplier=3, max_noise_fraction=0.05,
                                        max_area_uncertainty_fraction=0.01,
                                        undetected_max_fraction=0.03))
    with pytest.raises(ValueError):
        fixed_window_measurement(source, processed, row, analysis, [], reference=reference)


def test_validator_missing_git_is_optional_provenance(monkeypatch):
    from chemyx_lab.analysis import phase_audit
    def missing_git(*args, **kwargs):
        raise FileNotFoundError("Git is not installed on copied offline computer")
    monkeypatch.setattr(phase_audit.subprocess, "run", missing_git)
    value = phase_audit.provenance()
    assert value["git_commit"] == "unavailable"
    assert value["source_code_sha256"]


@pytest.mark.parametrize("prefix", ["", "processor_run_"])
def test_validator_positive_trace_then_bounded_low_trace_are_distinct_from_failed_detection(tmp_path, prefix):
    source = tmp_path / "sample.dx"
    source.write_bytes(b"validator synthetic production-audit input")
    processed = tmp_path / "processed"
    folder = processed / "phase_audit" / "sample"
    folder.mkdir(parents=True)
    x = np.linspace(5.3, 6.3, 1001)
    noise = 0.001 * np.sin(np.arange(len(x)))
    meta = dict(raw_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                parameters={"normalization": "none"},
                phase={"method": "stored", "p0_deg": 0, "p1_deg": 0})
    (folder / f"{prefix}processing_metadata.json").write_text(json.dumps(meta), encoding="utf-8")
    analysis = dict(integration_window_ppm=0.1, plot_window_ppm=0.5,
                    measurement_qc=dict(noise_multiplier=3, max_noise_fraction=0.01,
                                        max_area_uncertainty_fraction=0.005,
                                        undetected_max_fraction=0.025))
    peak = 100 * np.exp(-((x - 5.8) / 0.01) ** 2)
    np.savez(folder / f"{prefix}spectral_evidence.npz", region_ppm=x, regional_quantitative=peak + noise)
    reference = fixed_window_measurement(
        source, processed, dict(target_ppm=5.8, peak_area=2, peak_clear=True), analysis, [])
    assert reference["measurement_valid"] and reference["completion_area"] > 0
    np.savez(folder / f"{prefix}spectral_evidence.npz", region_ppm=x, regional_quantitative=noise)
    low = fixed_window_measurement(
        source, processed, dict(target_ppm=5.8, peak_area=0, peak_clear=False,
                                qc_failure_reasons="no QC-passing peak in the tracked window"),
        analysis, [], reference=reference)
    assert low["measurement_valid"] and not low["peak_clear"]
    assert low["signal_classification"] == "bounded_low_signal"
    # A dispersive/cancelled target must not be classified as vanished signal.
    cancelled = 100 * (x - 5.8) / 0.01 * np.exp(-((x - 5.8) / 0.01) ** 2) + noise
    np.savez(folder / f"{prefix}spectral_evidence.npz", region_ppm=x, regional_quantitative=cancelled)
    with pytest.raises(ValueError, match="Negative target"):
        fixed_window_measurement(
            source, processed, dict(target_ppm=5.8, peak_area=0, peak_clear=False,
                                    qc_failure_reasons="no QC-passing peak in the tracked window"),
            analysis, [], reference=reference)


def test_validator_prior_reservation_blocks_changed_output_root_before_transports(tmp_path, monkeypatch):
    """Use fake constructor sentinels only; no instrument can be opened."""
    from chemyx_lab import config
    from chemyx_lab.workflows import three_instrument_si6 as si6
    source_root = config.REPO_ROOT
    raw, arduino, pump, nmr = si6.prepare(
        source_root / "config_templates/experiments/si6_two_stage_fast_sim.yaml",
        tmp_path / "absent_machine.yaml",
        source_root / "arduino/configs/arduino.example.yaml", mock=True)
    del raw["simulation"]
    raw["workflow"]["experiment_id"] = "validator-blocked-experiment"
    raw["output"]["run_root_dir"] = str(tmp_path / "different_output_root")
    monkeypatch.setattr(config, "REPO_ROOT", tmp_path / "copied_repo")
    DoseGuard(config.REPO_ROOT / "runtime/si6_doses", raw["workflow"]["experiment_id"],
              {"prior_configuration": True}, "prior-run").reserve()
    def forbidden_transport(*args, **kwargs):
        pytest.fail("Transport was constructed before reservation refusal")
    monkeypatch.setattr(si6, "SerialTransport", forbidden_transport)
    monkeypatch.setattr(si6, "Pump", forbidden_transport)
    with pytest.raises(DoseReplayBlocked):
        with si6.open_services(raw, arduino, pump, nmr,
                               identity=si6.RunIdentity("si6", False)):
            pytest.fail("Restart was admitted")


@pytest.mark.parametrize("source", ["yaml", "environment"])
def test_validator_automatic_gain_cannot_bypass_quantitative_completion(tmp_path, monkeypatch, source):
    import yaml
    from chemyx_lab import config
    from chemyx_lab.workflows import three_instrument_si6 as si6
    raw = yaml.safe_load((config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml").read_text())
    if source == "yaml":
        raw["nmr"]["auto_gain"] = True
    else:
        monkeypatch.setenv("NMR_AUTO_GAIN", "true")
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="gain"):
        si6.prepare(path, tmp_path / "absent_machine.yaml",
                    config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True)


def test_validator_dose_confirmation_requires_actual_pump_completion_evidence(tmp_path):
    from chemyx_lab.runtime_journal import OperationJournal
    from chemyx_lab.runtime_state import replay_journal
    journal = OperationJournal(tmp_path / "journal.jsonl", "validator-run")
    common = dict(dose_id="validator-exp:transition", experiment_id="validator-exp",
                  configuration_sha256="a" * 64)
    journal.append("experiment_reserved", **common)
    journal.append("dose_dispatch_intent", **common)
    journal.append("dose_confirmed", channel=2, volume_ml=1.8, rate_ml_min=1.0, **common)
    replay = replay_journal(journal.path)
    assert not replay.valid, "Semantic confirmation without any pump completion must fail closed"
