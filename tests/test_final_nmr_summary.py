"""Saved-results summaries, failure evidence, title identity and no reprocessing."""
import csv
import importlib.util
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from matplotlib.figure import Figure

from chemyx_lab import config
from chemyx_lab.analysis import final_nmr_summary as summary
from chemyx_lab.workflows import si6_automated_nmr as base


FIXTURE = config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"


def write_table(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def saved_run(tmp_path):
    run = tmp_path / "RUN-TEST-001"
    (run / "raw_nmr").mkdir(parents=True)
    rows = []
    events = []
    for iteration in range(1, 5):
        filename = f"iteration_{iteration}.dx"
        (run / "raw_nmr" / filename).write_bytes(FIXTURE.read_bytes())
        processed = run / "processed_nmr" / f"iteration_{iteration}"
        metrics = {"file": filename, "peak_ppm": 5.8, "integrated_area": 10 + iteration, "snr": 20, "prominence_snr": 12}
        write_table(processed / "run_peaks_simple.csv", [metrics])
        write_table(processed / "statistics/run_target_peak_spectra_long.csv", [
            {"file": filename, "ppm": 5.7, "intensity": iteration},
            {"file": filename, "ppm": 5.8, "intensity": 10 * iteration},
            {"file": filename, "ppm": 5.9, "intensity": iteration},
        ])
        events.append({"event_type": "nmr_retrieved", "cycle_number": iteration, "workflow_phase": "initial", "result_path": f"raw_nmr/{filename}"})
        if iteration == 3:
            events += [
                {"event_type": "analysis_result", "cycle_number": 3, "workflow_phase": "initial", "result_classification": "invalid", "analysis_result": {"peak_area": 13, "snr": 2}, "error_message": "QC failed"},
                {"event_type": "measurement_failed", "cycle_number": 3, "workflow_phase": "initial", "failed_step": "NMR analysis", "error_message": "QC failed"},
            ]
        else:
            rows.append({"iteration": iteration, "stage": "initial" if iteration < 4 else "addition", "file": filename, "peak_ppm": 5.8, "peak_area": 10 + iteration, "snr": 20, "prominence_snr": 12, "growth_percent": "" if iteration in (1, 4) else 1.25, "peak_clear": True, "plateau": iteration == 2})
    events += [
        {"event_type": "measurement_failed", "cycle_number": 5, "workflow_phase": "addition", "failed_step": "NMR acquisition", "error_message": "acquisition timed out"},
        {"event_type": "terminal", "terminal_status": "analysis_inconclusive"},
    ]
    write_table(run / "time_series.csv", rows)
    (run / "operation_journal.jsonl").write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")
    (run / "manifest.json").write_text(json.dumps({"dataset_display_name": "  RUN-TEST-001  "}), encoding="utf-8")
    return run


def test_summary_keeps_failures_existing_metrics_and_source_files(tmp_path, monkeypatch):
    run = saved_run(tmp_path)
    before = {p: p.read_bytes() for p in run.rglob("*") if p.is_file()}
    titles = {}
    real_save = Figure.savefig
    def save(fig, path, **kwargs):
        titles[Path(path).name] = fig.axes[0].get_title()
        if Path(path).name == "all_nmr_spectra.png":
            assert fig.axes[0].xaxis_inverted()
            assert len(fig.axes[0].lines) == 4  # includes a rejected iteration's saved spectrum
        return real_save(fig, path, **kwargs)
    monkeypatch.setattr(Figure, "savefig", save)
    out = summary.summarize_run(run)
    overview = summary._csv(out / "nmr_iteration_overview.csv")
    overall = summary._csv(out / "nmr_run_summary.csv")[0]
    assert len(overview) == 5
    assert [r["status"] for r in overview] == ["valid", "valid", "qc_rejected", "valid", "failed"]
    assert overview[1]["growth_percent"] == "1.25"
    assert overview[3]["growth_percent"] == ""  # no new cross-stage calculation
    assert overview[2]["snr"] == "2"  # rejected metrics are retained, not replaced
    assert overview[4]["acquired_at"] == ""  # no made-up acquisition timestamp
    assert overview[0]["timestamp_source"] == "LONG DATE header"
    assert (run / overview[0]["raw_dx_path"]).is_file()
    assert (run / overview[0]["processed_result"]).is_dir()
    assert (run / overview[0]["spectrum_source"]).is_file()
    assert overall["total_attempted_nmr_measurements"] == "5"
    assert overall["total_valid_measurements"] == "3"
    assert overall["total_failed_or_qc_rejected"] == "2"
    assert overall["first_plateau_iteration"] == "2"
    manifest = json.loads((out / "manifest.json").read_text())
    assert len(manifest["figures"]) == 3
    for figure in manifest["figures"]:
        assert titles[figure["file"]] == figure["visible_title"]
        assert figure["visible_title"].startswith("RUN-TEST-001 ")
        assert figure["visible_title"].count("RUN-TEST-001") == 1
        assert (out / figure["file"]).stat().st_size > 1000
    assert (out / "nmr_qc_overview.csv").is_file()
    summary.summarize_run(run)  # rerunnable, updates only the report
    assert all(p.read_bytes() == data for p, data in before.items())


def test_missing_metadata_never_uses_csv_filename_or_mtime(tmp_path, monkeypatch):
    run = saved_run(tmp_path)
    for path in (run / "raw_nmr").glob("*.dx"):
        lines = path.read_text().splitlines()
        path.write_text("\n".join(line for line in lines if not line.startswith("##LONG DATE=")), encoding="utf-8")
    real_save = Figure.savefig
    def save(fig, path, **kwargs):
        if Path(path).name == "peak_area_percent_change.png":
            # An iteration-axis plot needs no invented acquisition time.
            assert list(fig.axes[0].lines[0].get_ydata()) == [1.25]
        return real_save(fig, path, **kwargs)
    monkeypatch.setattr(Figure, "savefig", save)
    out = summary.summarize_run(run)
    assert all(row["elapsed_hours"] == "" for row in summary._csv(out / "nmr_iteration_overview.csv"))
    assert summary._csv(out / "nmr_run_summary.csv")[0]["total_elapsed_hours"] == ""
    manifest = json.loads((out / "manifest.json").read_text())
    assert any("no timed valid points" in warning for warning in manifest["warnings"])


def test_byte_identical_mock_uses_saved_real_trace_not_magnitude(tmp_path):
    run = saved_run(tmp_path)
    reused = run / "processed_nmr/iteration_2"
    for p in (reused / "statistics").glob("*.csv"):
        p.unlink()
    (reused / "MOCK_REUSED_PROCESSING.txt").write_text("Mock fixture byte-identical to iteration_1.dx; its production process_fid tables were reused.", encoding="utf-8")
    out = summary.summarize_run(run)
    rows = summary._csv(out / "nmr_iteration_overview.csv")
    assert rows[1]["spectrum_source"] == rows[0]["spectrum_source"]
    assert rows[1]["spectrum_kind"] == "corrected regional real"


def test_bad_export_and_truncated_journal_do_not_hide_other_iterations(tmp_path):
    run = saved_run(tmp_path)
    (run / "processed_nmr/iteration_2/statistics/run_target_peak_spectra_long.csv").write_text("file,ppm,intensity\niteration_2.dx,bad,nan\n", encoding="utf-8")
    with (run / "operation_journal.jsonl").open("a", encoding="utf-8") as handle:
        handle.write('\n{"truncated":')
    out = summary.summarize_run(run)
    rows = summary._csv(out / "nmr_iteration_overview.csv")
    assert len(rows) == 5
    assert rows[1]["spectrum_source"] == ""
    manifest = json.loads((out / "manifest.json").read_text())
    assert any("Journal line" in warning for warning in manifest["warnings"])
    assert summary._csv(out / "nmr_run_summary.csv")[0]["processed_spectra_available"] == "3"


def test_report_error_does_not_change_cli_outcome_or_open_services(tmp_path, monkeypatch):
    script = config.REPO_ROOT / "scripts/02_si6_experiment.py"
    import sys
    monkeypatch.syspath_prepend(str(script.parent))
    spec = importlib.util.spec_from_file_location("experiment_summary_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    state = {"closed": False}
    @contextmanager
    def services(*args, **kwargs):
        yield SimpleNamespace(paths=SimpleNamespace(run_dir=tmp_path))
        state["closed"] = True
    def broken_report(path):
        assert state["closed"]
        raise OSError("report disk failure")
    monkeypatch.setattr(module.si6, "open_services", services)
    monkeypatch.setattr(module.si6, "run_experiment", lambda *a, **k: base.RunOutcome(base.TerminalStatus.ANALYSIS_INCONCLUSIVE, "measurement failed"))
    monkeypatch.setattr(module, "summarize_run", broken_report)
    assert module.main(["--mock"]) == 7
