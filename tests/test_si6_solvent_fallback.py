"""Sample absence must never masquerade as target disappearance or plateau."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import csv
import json
import subprocess
import sys
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.analysis.solvent_presence import DEFAULTS, evaluate_solvent_spectrum, solvent_presence, record_sample_presence, solvent_config
from chemyx_lab.analysis.stage_completion import completion_evidence
from chemyx_lab.workflows import si6_automated_nmr as base, three_instrument_si6 as si6


def spectrum(*, solvent=True, target=True, single=False, scale=1):
    x = np.linspace(-5, 15, 65536)
    y = np.abs(np.random.default_rng(55).normal(0, 20, len(x)))
    centers = ([2.08] if single else [2.08, 6.99]) if solvent else []
    for center in centers:
        y += scale * 60000 * np.exp(-.5*((x-center)/.025)**2)
    if target:
        y += 1000*np.exp(-.5*((x-5.8)/.02)**2)
    return x, y


@pytest.mark.parametrize("solvent,target,single,present", [
    (True, True, False, True), (True, False, False, True),
    (False, True, False, False), (False, False, False, False),
    (True, True, True, False),
])
def test_sample_presence_is_independent_of_target(solvent, target, single, present):
    assert evaluate_solvent_spectrum(*spectrum(solvent=solvent, target=target, single=single), DEFAULTS)["present"] is present


def test_noise_flat_trace_and_weak_residual_solvent_fail():
    for arrays in [spectrum(solvent=False, target=False), (np.linspace(-5, 15, 65536), np.ones(65536)*1.e6), spectrum(scale=.01)]:
        assert not evaluate_solvent_spectrum(*arrays, DEFAULTS)["present"]


def test_neighbor_at_7p12_cannot_replace_missing_6p99_solvent_signal():
    x, y = spectrum(solvent=False, target=False)
    for center in (2.08, 7.12):
        y += 60000 * np.exp(-.5*((x-center)/.025)**2)
    assert not evaluate_solvent_spectrum(x, y, DEFAULTS)["present"]


def test_all_archived_spectra_have_two_solvent_signals():
    paths = sorted((config.REPO_ROOT / "results/raw/nmr/06-08-26").glob("*.dx")) + sorted((config.REPO_ROOT / "results/raw/nmr/06-09-26").glob("*.dx"))
    if not paths:
        pytest.skip("Optional archived datasets are not installed on this computer")
    assert len(paths) == 15
    for path in paths:
        evidence = solvent_presence(path, DEFAULTS)
        assert evidence["present"], path.name
        assert 2.07 < evidence["windows"][0]["peak"]["ppm"] < 2.09
        assert 6.97 < evidence["windows"][1]["peak"]["ppm"] < 7.01


def test_absent_target_real_fixture_still_has_sample():
    assert solvent_presence(config.REPO_ROOT / "chemyx_lab/testing/fixtures/no_resonance_phsi2_20260609_0900.dx", DEFAULTS)["present"]


@pytest.mark.parametrize("stage", [1, 2])
def test_missing_samples_reset_early_window_and_latch_after_three(stage):
    raw = base.load_si6_config(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml")
    rules = base.build_stages(raw["workflow"])[stage-1].completion
    rows = []
    for area, present in [(100, True), (0 if stage == 1 else 200, True), (0 if stage == 1 else 200, True), (0, False)]:
        row = record_sample_presence(dict(peak_area=area), rows, DEFAULTS, {"present": present})
        rows.append(row)
    assert rows[-1]["peak_area"] is None
    assert completion_evidence(rows, rules)["confirmations_passed"] == 0
    for _ in range(2):
        rows.append(record_sample_presence(dict(peak_area=0), rows, DEFAULTS, {"present": False}))
    assert rows[-1]["stage_timed_fallback"]
    # Reappearing solvent does not shorten a stage already latched to its deadline.
    for _ in range(8):
        rows.append(record_sample_presence(dict(peak_area=0 if stage == 1 else 200), rows, DEFAULTS, {"present": True}))
    assert not completion_evidence(rows, rules)["complete"]


def test_isolated_missing_samples_do_not_latch():
    rows = []
    for present in [False, False, True, False, False, True]:
        rows.append(record_sample_presence(dict(peak_area=1), rows, DEFAULTS, {"present": present}))
    assert not rows[-1]["stage_timed_fallback"]
    assert rows[-1]["solvent_missing_streak"] == 0


def run_mock(tmp_path, monkeypatch, *, missing_stages, empty=False, recovery=False, duration1=48, duration2=4, interval1=120, interval2=30, acquisition_failure_at=None, reports=False):
    raw = base.load_si6_config(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml")
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    for name, hours, minutes in [("initial_stage", duration1, interval1), ("first_addition_stage", duration2, interval2)]:
        raw["workflow"][name].update(max_hours=hours, interval_minutes=minutes)
    path = tmp_path / "recipe.yaml"
    path.write_text(yaml.safe_dump(raw))
    if not reports:
        monkeypatch.setattr(si6, "final_reports", lambda *a: None)
    monkeypatch.setattr(base, "operator_checkpoint", lambda *a, **k: pytest.fail("unexpected prompt"))
    # Only sample-presence evidence is injected; the shared real pump/needle
    # mock and production scheduler, endpoints, cleanup and dose ledger run.
    actual_solvent = si6.solvent_presence
    def check(source, settings):
        stage = source.stem.rsplit("_", 1)[0]
        count = int(source.stem.rsplit("_", 1)[1])
        if stage in missing_stages and not (recovery and count > 3):
            return {"present": False, "reason": "TEST: no solvent in synthetic full spectrum", "windows": []}
        return actual_solvent(source, settings)
    monkeypatch.setattr(si6, "solvent_presence", check)
    with si6.open_services(*si6.prepare(path, tmp_path / "no_machine.yaml", config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True), identity=si6.RunIdentity("si6", True)) as s:
        s.simulation.fixture["stages"] = {"stage_1": [100, 0, 0, 0, 0] + [0]*100, "stage_2": [1, 2, 2, 2, 2] + [2]*100}
        if empty or acquisition_failure_at is not None:
            acquire = s.acquire
            def no_data(*args, **kwargs):
                if acquisition_failure_at is not None and kwargs["label"] == f"stage_1_{acquisition_failure_at:04d}":
                    raise RuntimeError("TEST: NMR acquisition transport failed")
                result = acquire(*args, **kwargs)
                if empty and result.stem.rsplit("_", 1)[0] in missing_stages:
                    if empty == "missing":
                        result.unlink()
                    elif empty == "malformed":
                        result.write_text("Instrument returned no FID")
                    else:
                        result.write_bytes(b"")
                return result
            s.acquire = no_data
        result = si6.run_experiment(s)
    records = [json.loads(line) for line in s.paths.journal_jsonl.read_text().splitlines()]
    rows = list(csv.DictReader(s.paths.time_series_csv.open(newline="")))
    return result, s, records, rows


@pytest.mark.parametrize("missing_stages,empty", [
    ({"stage_1"}, False), ({"stage_2"}, False),
    ({"stage_1", "stage_2"}, False), ({"stage_1", "stage_2"}, True),
])
def test_full_48_hour_and_4_hour_automation_continues_and_doses_once(tmp_path, monkeypatch, missing_stages, empty):
    result, s, records, rows = run_mock(tmp_path, monkeypatch, missing_stages=missing_stages, empty=empty)
    assert result.status is base.TerminalStatus.COMPLETED
    assert "chemical completion was not verified" in result.message
    doses = [e for e in records if e["event_type"] == "dose_confirmed"]
    assert len(doses) == 1
    for stage, hours, count in [("stage_1", 48, 24), ("stage_2", 4, 8)]:
        selected = [r for r in rows if r["stage"] == stage]
        if stage in missing_stages:
            assert len(selected) == count
            assert all(r["peak_area"] == "" and r["plateau"] == "False" for r in selected)
            assert selected[-1]["stage_timed_fallback"] == "True"
            start = next(e for e in records if e["event_type"] == "monitoring_stage_started" and e["workflow_phase"] == stage)
            decision = next(e for e in records if e["event_type"] == "stage_transition_decision" and e["workflow_phase"] == stage and e["result_classification"] == "timed_fallback_completed")
            assert decision["timestamp_utc"]  # actual journal time, distinct from virtual test clock
            if stage == "stage_1":
                # Stage 2 start is after the 48-hour deadline and matched dose cleanup.
                start2 = next(e for e in records if e["event_type"] == "monitoring_stage_started" and e["workflow_phase"] == "stage_2")
                assert datetime.fromisoformat(start2["stage_started_at"]) - datetime.fromisoformat(start["stage_started_at"]) >= timedelta(hours=hours)
            assert not any(e["event_type"] == "plateau_detection" and e["workflow_phase"] == stage for e in records)
    assert s.simulation.monotonic() >= (48*3600 if "stage_1" in missing_stages else 0) + (4*3600 if "stage_2" in missing_stages else 0)
    assert s.channel_states[1].retained_volume_ml == 0
    assert s.needle.state.logical_position == 1
    positions = [e["target_steps"] for e in records if e["event_type"] == "needle_transition" and e["result_classification"] == "completed"]
    assert set(positions) == {0, 1}
    assert positions[0] == 0


def test_healthy_target_disappearance_and_growth_finish_early(tmp_path, monkeypatch):
    result, s, records, rows = run_mock(tmp_path, monkeypatch, missing_stages=set())
    assert result.status is base.TerminalStatus.COMPLETED
    assert len([r for r in rows if r["stage"] == "stage_1"]) == 5
    assert len([r for r in rows if r["stage"] == "stage_2"]) == 5
    assert not any(e["event_type"] == "solvent_timed_fallback" for e in records)
    assert s.simulation.monotonic() < 48*3600


def test_recovered_solvent_cannot_shorten_latched_stage(tmp_path, monkeypatch):
    result, s, records, rows = run_mock(tmp_path, monkeypatch, missing_stages={"stage_1"}, recovery=True)
    assert result.status is base.TerminalStatus.COMPLETED
    assert len([r for r in rows if r["stage"] == "stage_1"]) == 24
    assert not any(e["event_type"] == "plateau_detection" and e["workflow_phase"] == "stage_1" for e in records)


def test_timer_does_not_oversleep_when_cadence_exceeds_remaining_duration(tmp_path, monkeypatch):
    result, s, records, rows = run_mock(tmp_path, monkeypatch, missing_stages={"stage_1", "stage_2"}, duration1=5, interval1=120, duration2=2, interval2=30)
    assert result.status is base.TerminalStatus.COMPLETED
    assert len([r for r in rows if r["stage"] == "stage_1"]) == 3
    stage2 = next(e for e in records if e["event_type"] == "monitoring_stage_started" and e["workflow_phase"] == "stage_2")
    # Includes startup movements and a 108-second dose plus pump padding.
    assert 5*3600 <= (datetime.fromisoformat(stage2["stage_started_at"])-s.simulation.origin).total_seconds() < 5*3600+180


@pytest.mark.parametrize("missing", ["missing", "malformed"])
def test_no_retrievable_spectrum_uses_same_automatic_fallback(tmp_path, monkeypatch, missing):
    result, s, records, rows = run_mock(tmp_path, monkeypatch, missing_stages={"stage_1", "stage_2"}, empty=missing,
                                      duration1=3, interval1=60, duration2=2, interval2=30)
    assert result.status is base.TerminalStatus.COMPLETED
    assert len(rows) == 7
    assert all(r["peak_area"] == "" and r["acquired_at"] == "" for r in rows)
    assert len([e for e in records if e["event_type"] == "dose_confirmed"]) == 1


def test_startup_returns_known_up_position_to_saved_zero(tmp_path):
    raw = base.load_si6_config(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml")
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    path = tmp_path / "recipe.yaml"
    path.write_text(yaml.safe_dump(raw))
    with si6.open_services(*si6.prepare(path, tmp_path / "no_machine", config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True), identity=si6.RunIdentity("si6", True)) as s:
        s.needle.move_to(1)
        si6.home_and_raise(s)
        assert s.needle.state.logical_position == 1
        assert any("JOG -1 " in line for line in s.needle.transport.tx_log)
        records = [json.loads(line) for line in s.paths.journal_jsonl.read_text().splitlines()]
        finished = [e for e in records if e["event_type"] == "needle_transition" and e["result_classification"] == "completed"]
        assert [(e["target"], e["target_steps"]) for e in finished] == [("HOME", 0), ("UP", 1)]


def test_manual_commands_are_relative_from_home_and_home_alias_works(tmp_path):
    raw = yaml.safe_load((config.REPO_ROOT / "arduino/configs/arduino.example.yaml").read_text())
    raw["results"]["run_root_dir"] = str(tmp_path / "manual")
    path = tmp_path / "arduino.yaml"
    path.write_text(yaml.safe_dump(raw))
    for action, position in [("set-home", 0), ("up", 1), ("up", 2), ("down", 1), ("down", 0), ("up", 1), ("home", 0)]:
        result = subprocess.run([sys.executable, "-B", str(config.REPO_ROOT / "arduino/scripts/needle_control.py"), action, "--mock", "--config", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        state = json.loads((tmp_path / "manual/mock_needle_state.json").read_text())
        assert state["logical_position"] == position


def test_current_three_instrument_clis_have_no_acknowledgement_flag():
    for script in ("01_three_instrument_system_test.py", "02_si6_experiment.py", "03_si6_real_soak_test.py"):
        result = subprocess.run([sys.executable, "-B", str(config.REPO_ROOT / "scripts" / script), "--help"], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert "--acknowledge" not in result.stdout


def test_reports_keep_missing_areas_unavailable_and_do_not_claim_chemistry_completion(tmp_path):
    from chemyx_lab.analysis.si6_final_qc import write_final_qc
    raw = base.load_si6_config(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml")
    rows = []
    for _ in range(3):
        rows.append(record_sample_presence(dict(stage="stage_1", acquired_at=None, timestamp_source="unavailable", peak_area=0), rows, DEFAULTS, {"present": False}))
    outcome = base.RunOutcome(base.TerminalStatus.COMPLETED, "Timed fallback; chemical completion unverified.", base.StageOutcome.TIMED_FALLBACK_COMPLETED)
    figures = write_final_qc(SimpleNamespace(run_dir=tmp_path), rows, base.build_stages(raw["workflow"]), raw["qc_reporting"], dataset="SOLVENT-FALLBACK-TEST", outcome=outcome)
    assert figures == []
    summary = json.loads((tmp_path / "final_qc/qc_summary.json").read_text())
    assert summary["plots_available"] is False
    assert summary["stages"][0]["timed_fallback"]
    assert not summary["stages"][0]["endpoint"]["complete"]
    assert all(r["peak_area"] is None and r["plot_elapsed_hours"] is None for r in summary["observations"])
    assert "chemical completion unverified" in (tmp_path / "final_qc/qc_report.md").read_text()


def test_transport_failure_during_latched_fallback_still_stops_without_dose(tmp_path, monkeypatch):
    result, s, records, rows = run_mock(tmp_path, monkeypatch, missing_stages={"stage_1"}, acquisition_failure_at=4)
    assert result.status is base.TerminalStatus.INSTRUMENT_FAILURE
    assert rows[-1]["stage_timed_fallback"] == "True"
    assert not any(e["event_type"] == "dose_confirmed" for e in records)
    assert s.dose_guard.data["status"] == "RESERVED"
    assert s.channel_states[1].retained_volume_ml == 0


@pytest.mark.parametrize("change", [dict(consecutive_missing=0), dict(minimum_snr=float("nan")), dict(windows_ppm=[[2, 3]]), dict(noise_windows_ppm=[[2, 3]])])
def test_bad_solvent_settings_rejected(change):
    with pytest.raises(ValueError):
        solvent_config(dict(deepcopy(DEFAULTS), **change))
