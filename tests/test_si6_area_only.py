"""Area-only endpoints, production moving area, QC independence and safe limits."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import numpy as np
import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.analysis.stage_completion import completion_evidence, validate_completion
from chemyx_lab.analysis.stage_measurement import tracked_peak_area_observation, PeakEvidenceError
from chemyx_lab.analysis.peak_tracking import identification_config
from chemyx_lab.analysis import si6_final_qc as qc
from chemyx_lab.testing.si6_synthetic_analysis import SyntheticSpectra, metadata_carrier
from chemyx_lab.workflows import three_instrument_si6 as si6
from chemyx_lab.workflows import si6_automated_nmr as base


def recipe():
    return base.load_si6_config(config.REPO_ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml")


def rules(stage):
    return base.build_stages(recipe()["workflow"])[stage-1].completion


@pytest.mark.parametrize("stage,values,first_complete", [
    (1, [100, 70, 40, 15, 5, 2.3, 2.0, 1.8], 8),
    (1, [100, 50, 20, 2, 10, 2, 1.8, 1.7], 8),
    (1, [100]*20, None),
    (1, [100, 0, 0, 0], 4),
    (1, [0, 0, 0], 3),
    (2, [1, 10, 30, 60, 90, 99, 100, 100.5, 99.8, 100.1], 9),
    (2, [1]*20, None),
    (2, [0]*20, None),
    (2, [0, 10, 10, 10, 10], 5),
    (2, [1, 2, 2, 2, 0, 2, 2, 2, 2], 9),
])
def test_area_rules_ignore_every_diagnostic(stage, values, first_complete):
    rows = []
    for index, area in enumerate(values, 1):
        rows.append(dict(peak_area=area, measurement_valid=False, peak_clear=False,
                         qc_pass=False, low_snr_warning=True, high_noise_warning=True,
                         area_uncertainty=1.e99, noise=1.e99, error="QC failed", acquired_at=None))
        assert completion_evidence(rows, rules(stage))["complete"] is (first_complete is not None and index >= first_complete)
    if first_complete is None:
        assert completion_evidence(rows, rules(stage))["limit_reached"]


def test_growth_disabled_and_stability_observations_are_explicit():
    r = rules(2)
    r["minimum_growth"]["enabled"] = False
    assert not completion_evidence([dict(peak_area=1)]*3, r)["complete"]
    assert completion_evidence([dict(peak_area=1)]*4, r)["complete"]


@pytest.mark.parametrize("mutation", ["qc_enabled", "snr_in_live", "old_rule", "double_limit", "too_short"])
def test_profile_cannot_reintroduce_qc_or_competing_rules(tmp_path, mutation):
    raw = recipe()
    if mutation == "qc_enabled":
        raw["qc_reporting"]["affect_workflow"] = True
    elif mutation == "snr_in_live":
        raw["analysis"]["peak_finding"]["snr"] = dict(enabled=True, minimum=100)
    elif mutation == "old_rule":
        raw["workflow"]["initial_stage"]["completion"]["max_window_range_fraction"] = .01
    elif mutation == "double_limit":
        raw["workflow"]["initial_stage"]["max_measurements"] = 20
    else:
        raw["workflow"]["first_addition_stage"]["max_hours"] = 6
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        base.load_si6_config(path)


def test_live_identity_has_no_hidden_qc_filters():
    cfg = identification_config(recipe()["analysis"]["peak_finding"])
    assert all(not cfg[k]["enabled"] for k in ("snr", "height", "area", "shoulder", "slope", "shape", "neighbor"))


def test_final_iteration_limit_precedes_runtime_limit():
    from dataclasses import replace
    clock = [0.0]
    stage = replace(base.build_stages(recipe()["workflow"])[0], max_measurements=1, max_hours=.01)
    def measure(schedule):
        clock[0] = 100
        return base.MeasurementObservation(True, False, "start", "end", "end")
    result = base.run_monitoring_stage(stage, measure, monotonic_fn=lambda: clock[0], wall_now_fn=lambda: datetime(2026, 1, 1), sleep_fn=lambda *a: None)
    assert result.outcome.stage_outcome is base.StageOutcome.PLATEAU_NOT_REACHED_WITHIN_LIMIT


def test_profile_processor_ignores_retrospective_qc_and_auto_reference_configs(tmp_path):
    import subprocess
    import sys
    shared = yaml.safe_load((config.REPO_ROOT / "configs/nmr/analysis.yaml").read_text())
    shared.update(peak_qc="deliberately invalid retrospective setting", target_peak="invalid",
                  statistics="invalid", reference="invalid")
    shared["plots"]["flattened_overlay"] = "invalid"
    shared["processing"].update(reference_method="validated_peak", normalization="max")
    processing_config = tmp_path / "processor.yaml"
    processing_config.write_text(yaml.safe_dump(shared))
    identity = tmp_path / "identity.json"
    identity.write_text(json.dumps(recipe()["analysis"]["peak_finding"]))
    result = subprocess.run([sys.executable, "-B", str(config.REPO_ROOT / "scripts/nmr/process_fid.py"), str(si6.MOCK_NMR_FIXTURE),
                             "--config", str(processing_config), "--peak-tracking-config", str(identity),
                             "--output-dir", str(tmp_path), "--run-name", "live_profile", "--dataset-display-name", "QC INDEPENDENCE"],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    audit = json.loads(next((tmp_path / "live_profile").glob("phase_audit/*/*processing_metadata.json")).read_text())
    assert audit["parameters"]["normalization"] == "none"
    assert audit["parameters"]["reference_method"] == "metadata"
    assert audit["parameters"]["reference_model"] == "metadata"


@pytest.fixture(scope="module")
def engine():
    return SyntheticSpectra()


def metadata(index):
    time = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(hours=index)
    return dict(iteration=index, stage="stage_1", elapsed_hours=index,
                acquired_at=time.isoformat(), timestamp_source="LONG DATE header",
                target_ppm=5.8, dataset_display_name="AREA ONLY TEST")


def test_production_moving_area_and_qc_warning_do_not_reject(tmp_path, engine):
    raw = recipe()
    rows = []
    for i, drift in enumerate((-.01, -.025, .005, .02), 1):
        source = tmp_path / f"source_{i}.dx"
        metadata_carrier(source, datetime.fromisoformat(metadata(i)["acquired_at"]))
        row, _ = engine.measure(source, tmp_path / f"processed_{i}", metadata(i), raw["analysis"], rows,
                                area=100, drift_ppm=drift, noise_sd=.1)
        rows.append(row)
    assert all(r["observation_recorded"] for r in rows)
    assert np.ptp([r["peak_area"] for r in rows]) / np.mean([r["peak_area"] for r in rows]) < .03
    assert np.ptp([r["integration_left_ppm"] for r in rows]) > .03
    assert np.ptp([r["peak_ppm"] for r in rows]) > .03
    report_cfg = deepcopy(raw["qc_reporting"])
    report_cfg["peak_filters"]["snr"]["minimum"] = 1.e30
    assert "low SNR" in qc.diagnostic_row(rows[-1], report_cfg, tmp_path)["qc_warnings"]
    assert rows[-1]["peak_area"] > 0


def test_missing_peak_zero_and_no_neighbor_substitution(tmp_path, engine):
    raw = recipe()
    rows = []
    for i, (area, drift) in enumerate(((100, 0), (100, .12), (0, 0), (100, .01)), 1):
        source = tmp_path / f"source_{i}.dx"
        metadata_carrier(source, datetime.fromisoformat(metadata(i)["acquired_at"]))
        row, _ = engine.measure(source, tmp_path / f"p{i}", metadata(i), raw["analysis"], rows, area=area, drift_ppm=drift)
        rows.append(row)
    assert rows[1]["peak_area"] == rows[2]["peak_area"] == 0
    assert rows[1]["signal_classification"] == "peak_not_detected"
    assert rows[-1]["previous_peak_ppm"] == rows[0]["peak_ppm"]
    assert rows[-1]["peak_area"] > 0


def test_unavailable_processing_is_operational_failure_not_zero(tmp_path):
    source = tmp_path / "raw.dx"
    source.write_bytes(b"unprocessed")
    with pytest.raises(PeakEvidenceError):
        tracked_peak_area_observation(source, tmp_path, metadata(1), recipe()["analysis"], [])


def test_spectrum_that_failed_old_noise_qc_is_recorded_now(tmp_path, engine):
    from chemyx_lab.testing.si6_synthetic_analysis import historical_nominal
    source = tmp_path / "source.dx"
    metadata_carrier(source, datetime.fromisoformat(metadata(1)["acquired_at"]))
    legacy = historical_nominal()["analysis"]
    reference, _ = engine.measure(source, tmp_path / "reference", metadata(1), legacy, [], area=100)
    with pytest.raises(ValueError, match="noise|uncertainty|Negative"):
        engine.measure(source, tmp_path / "bad_noise", metadata(2), legacy, [reference], area=25, noise_sd=20)
    row = tracked_peak_area_observation(source, tmp_path / "bad_noise", metadata(2), recipe()["analysis"], [dict(reference, signal_classification="detected")])
    assert row["observation_recorded"]
    assert row["peak_area"] >= 0
    assert row["tracking_status"] in {"TRACKED_PEAK_FOUND", "PEAK_NOT_DETECTED"}


def run_fixture(tmp_path, monkeypatch, values1, values2, *, force_qc=True, report_fails=False, processing_failure=False, missing_time=False):
    raw = recipe()
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(raw))
    # Full plot rendering/title contract tested independently below.
    monkeypatch.setattr(si6, "write_stage_reports", lambda *a, **k: [])
    def skip_figure(fig, *a, **k):
        import matplotlib.pyplot as plt
        plt.close(fig)
    monkeypatch.setattr(qc, "save_figure", skip_figure)
    if report_fails:
        monkeypatch.setattr(si6, "write_final_qc", lambda *a, **k: (_ for _ in ()).throw(ValueError("report fault")))
    with si6.open_services(*si6.prepare(path, tmp_path / "no_machine.yaml", config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True), identity=si6.RunIdentity("si6", True)) as s:
        s.simulation.fixture["stages"] = {"stage_1": values1, "stage_2": values2}
        if processing_failure:
            monkeypatch.setattr(s, "process", lambda *a: (_ for _ in ()).throw(base.AnalysisInconclusiveError("processor crashed")))
        if missing_time:
            acquire = s.acquire
            def without_time(*a, **k):
                import re
                result = acquire(*a, **k)
                text = re.sub(r"(?m)^##LONG DATE=.*$", "##LONG DATE=", result.read_text(encoding="latin-1"))
                result.write_text(text, encoding="latin-1")
                return result
            monkeypatch.setattr(s, "acquire", without_time)
        analyze = s.analyze
        def flagged(*args):
            row, spectrum = analyze(*args)
            if force_qc:
                row.update(measurement_valid=False, qc_pass=False, low_snr_warning=True,
                           high_noise_warning=True, area_uncertainty=1.e99)
            return row, spectrum
        s.analyze = flagged
        monkeypatch.setattr(base, "operator_checkpoint", lambda *a, **k: pytest.fail("extra confirmation"))
        outcome = si6.run_experiment(s)
    records = [json.loads(x) for x in s.paths.journal_jsonl.read_text().splitlines()]
    return outcome, s, records


@pytest.mark.parametrize("force_qc,report_fails", [(False, False), (True, False), (True, True)])
def test_full_run_qc_never_changes_transition_or_outcome(tmp_path, monkeypatch, force_qc, report_fails):
    outcome, s, records = run_fixture(tmp_path, monkeypatch, [100, 70, 40, 15, 5, 2.3, 2, 1.8],
                                      [1, 10, 30, 60, 90, 99, 100, 100.5, 99.8, 100.1], force_qc=force_qc, report_fails=report_fails)
    assert outcome.status is base.TerminalStatus.COMPLETED
    assert [e["workflow_phase"] for e in records if e["event_type"] == "nmr_retrieved"].count("stage_1") == 8
    assert [e["workflow_phase"] for e in records if e["event_type"] == "nmr_retrieved"].count("stage_2") == 9
    assert len([e for e in records if e["event_type"] == "dose_confirmed"]) == 1
    assert s.dose_guard.data["status"] == "CONFIRMED"
    assert not any(e.get("terminal_status") == "analysis_inconclusive" for e in records)
    if not report_fails:
        review = json.loads((s.paths.run_dir / "final_qc/qc_summary.json").read_text())
        assert len(review["observations"]) == 17
        assert review["affect_workflow"] is False
        if force_qc:
            assert all("high noise" in r["qc_warnings"] for r in review["observations"])


@pytest.mark.parametrize("stage", [1, 2])
def test_stage_iteration_limit_is_distinct_and_never_authorizes_extra_dose(tmp_path, monkeypatch, stage):
    outcome, s, records = run_fixture(tmp_path, monkeypatch, [100]*20 if stage == 1 else [100, 0, 0, 0], [1]*20)
    assert outcome.status is getattr(base.TerminalStatus, f"STAGE_{stage}_MAX_ITERATIONS_REACHED")
    assert len([e for e in records if e["event_type"] == "nmr_retrieved" and e["workflow_phase"] == f"stage_{stage}"]) == 20
    assert len([e for e in records if e["event_type"] == "dose_confirmed"]) == stage-1


def test_processor_crash_stops_as_operational_failure_without_dosing(tmp_path, monkeypatch):
    outcome, s, records = run_fixture(tmp_path, monkeypatch, [100, 0, 0, 0], [1, 2, 2, 2, 2], processing_failure=True)
    assert outcome.status is base.TerminalStatus.INSTRUMENT_FAILURE
    assert not any(e["event_type"] == "dose_confirmed" for e in records)
    assert s.dose_guard.data["status"] == "RESERVED"


def test_missing_timing_disables_plots_but_not_area_control(tmp_path, monkeypatch):
    outcome, s, records = run_fixture(tmp_path, monkeypatch, [100, 0, 0, 0], [1, 2, 2, 2, 2], missing_time=True)
    assert outcome.status is base.TerminalStatus.COMPLETED
    assert len([e for e in records if e["event_type"] == "dose_confirmed"]) == 1
    report = json.loads((s.paths.run_dir / "final_qc/qc_summary.json").read_text())
    assert report["plots_available"] is False
    assert all(r["acquired_at"] is None and r["plot_elapsed_hours"] is None for r in report["observations"])


def test_final_qc_plot_titles_timing_and_all_formats(tmp_path, monkeypatch):
    seen = []
    original = qc.save_figure
    def capture(fig, stem, dataset, title, manifest):
        result = original(fig, stem, dataset, title, manifest)
        seen.append((result, fig._suptitle.get_text()))
        return result
    monkeypatch.setattr(qc, "save_figure", capture)
    rows = [dict(metadata(1), peak_area=10, peak_ppm=5.8, peak_clear=True, snr=10, width_ppm=.03)]
    outcome = base.RunOutcome(base.TerminalStatus.COMPLETED, "test")
    figures = qc.write_final_qc(SimpleNamespace(run_dir=tmp_path), rows, base.build_stages(recipe()["workflow"]), recipe()["qc_reporting"], dataset="  TITLE TEST  ", outcome=outcome)
    assert len(figures) == 12
    assert all(title == visible and title.startswith("TITLE TEST ") for title, visible in seen)
    assert all(f["visible_title"].startswith("TITLE TEST ") for f in figures)
    from pathlib import Path
    assert {Path(f["path"]).suffix for f in figures} == {".png", ".svg", ".pdf"}
    rows[0]["acquired_at"] = None
    rows[0]["timestamp_source"] = "unavailable"
    assert qc.write_final_qc(SimpleNamespace(run_dir=tmp_path / "no_time"), rows, base.build_stages(recipe()["workflow"]), recipe()["qc_reporting"], dataset="NO TIME", outcome=outcome) == []
    assert "No filename" in (tmp_path / "no_time/final_qc/qc_report.md").read_text()
