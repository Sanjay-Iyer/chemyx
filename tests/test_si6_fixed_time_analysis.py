"""Post-run analysis entry point: file selection, missing observations, isolation.

A stand-in replaces the processor subprocess; no instrument or network is used.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest

from chemyx_lab import config

SCRIPTS = config.REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("analyze_fixed_time_run", SCRIPTS / "05_analyze_fixed_time_run.py")
analysis = importlib.util.module_from_spec(spec)
spec.loader.exec_module(analysis)

FID = (config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx").read_bytes().replace(b"\r\r\n", b"\r\n")
RUN_ID = "SI6_FIXED_20261017T120000Z_abcdef123456"


def fid(stamp):
    return FID.replace(b"2026/08/10 17:27:21-0400", stamp.encode())


@pytest.fixture
def run(tmp_path):
    run = tmp_path / RUN_ID
    raw = run / "nmr_raw"
    raw.mkdir(parents=True)
    rows = []

    def observation(stage, n, status, content=None, name=None, error=None):
        stem = f"{stage}_{n:04d}"
        path = raw / (name or f"{stem}.dx") if content is not None else None
        if path is not None:
            path.write_bytes(content)
        result = {"status": status, "path": str(path) if status == "NMR_SAVED" and path else None, "error": error}
        (raw / f"{stem}.result.json").write_text(json.dumps(result), encoding="utf-8")
        rows.append(dict(result, stage=stage, observation=n, scheduled_seconds=(n - 1) * (7200 if stage == "stage_1" else 1800),
                         physical_cleanup="COMPLETE"))

    observation("stage_1", 1, "NMR_SAVED", fid("2026/10/17 08:10:00-0400"))
    observation("stage_1", 2, "NMR_MISSING", error="Raw TD JCAMP absent")
    (raw / "stage_1_0003.dx").write_bytes(b"partial export")  # foreign/partial server export kept beside the raw
    observation("stage_1", 3, "NMR_SAVED", fid("2026/10/17 12:10:00-0400"), name="stage_1_0003.rpc.dx")
    observation("stage_1", 4, "NMR_FAILED", error="NMR acquisition not confirmed complete after 300s")
    observation("stage_2", 1, "NMR_SAVED", fid("2026/10/19 08:15:00-0400"))
    (run / "report.json").write_text(json.dumps({"status": "COMPLETE", "observations": rows}), encoding="utf-8")
    return run


def snapshot(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.iterdir())}


class Processor:
    def __init__(self, fail=()):
        self.calls, self.fail = [], set(fail)

    def __call__(self, command, log_path):
        name = command[command.index("--run-name") + 1]
        self.calls.append(command)
        log_path.write_text("stand-in processor", encoding="utf-8")
        return 1 if name in self.fail else 0


def files_of(command):
    return [Path(a) for a in command[2:command.index("--phase-method")]]


def test_inventory_lists_every_observation_and_selects_only_real_fids(run):
    before = snapshot(run / "nmr_raw")
    processor = Processor()
    assert analysis.main([str(run)], runner=processor) == 0
    assert snapshot(run / "nmr_raw") == before  # raw data only read
    out = next((run / "analysis").iterdir())
    rows = list(__import__("csv").DictReader((out / "observation_inventory.csv").open(encoding="utf-8")))
    assert [(r["stage"], r["observation"], r["nmr_status"], r["analysed"]) for r in rows] == [
        ("stage_1", "1", "NMR_SAVED", "True"), ("stage_1", "2", "NMR_MISSING", "False"),
        ("stage_1", "3", "NMR_SAVED", "True"), ("stage_1", "4", "NMR_FAILED", "False"),
        ("stage_2", "1", "NMR_SAVED", "True")]
    assert rows[1]["note"] == "no raw spectrum (NMR_MISSING): Raw TD JCAMP absent" and rows[1]["raw_file"] == ""
    assert rows[2]["raw_file"].endswith("stage_1_0003.rpc.dx") and "stage_1_0003.dx is not identifiable" in rows[2]["note"]
    assert rows[0]["long_date"] == "2026/10/17 08:10:00-0400" and rows[0]["scheduled_hours"] == "0.0"
    summary = json.loads((out / "analysis_summary.json").read_text(encoding="utf-8"))
    assert summary["status_counts"] == {"NMR_SAVED": 3, "NMR_MISSING": 1, "NMR_FAILED": 1}
    assert [m["observation"] for m in summary["not_analysed"]] == [2, 4]


def test_each_stage_runs_the_production_processor_with_acme_and_run_identity(run):
    processor = Processor()
    analysis.main([str(run)], runner=processor)
    out = next((run / "analysis").iterdir())
    assert [c[c.index("--run-name") + 1] for c in processor.calls] == ["all_stages", "stage_1", "stage_2"]
    assert [len(files_of(c)) for c in processor.calls] == [3, 2, 1]
    for command, label in zip(processor.calls, ("Stages 1-2", "Stage 1", "Stage 2")):
        assert command[1] == str(analysis.PROCESSOR)
        assert command[command.index("--phase-method") + 1] == "acme"
        assert command[command.index("--dataset-display-name") + 1] == f"{RUN_ID} {label}"  # plot-title identity
        assert Path(command[command.index("--output-dir") + 1]) == out
        assert all(f.parent == run / "nmr_raw" and f.is_file() for f in files_of(command))
    assert out.parent == run / "analysis" and not (run / "nmr_raw" / "analysis").exists()


def test_reruns_never_replace_an_earlier_analysis(run):
    analysis.main([str(run)], runner=Processor())
    first = sorted((run / "analysis").iterdir())
    analysis.main([str(run / "nmr_raw")], runner=Processor())  # the raw folder path works too
    second = sorted((run / "analysis").iterdir())
    assert len(second) == 2 and first[0] in second and (first[0] / "analysis_summary.json").is_file()


def test_one_failed_group_does_not_stop_the_others(run):
    processor = Processor(fail={"stage_1"})
    assert analysis.main([str(run)], runner=processor) == 1
    out = next((run / "analysis").iterdir())
    groups = json.loads((out / "analysis_summary.json").read_text(encoding="utf-8"))["groups"]
    assert [(g["group"], g["status"]) for g in groups] == [("all_stages", "COMPLETE"), ("stage_1", "FAILED"), ("stage_2", "COMPLETE")]

    def crashes(command, log):
        raise MemoryError("processor crashed")
    assert analysis.main([str(run)], runner=crashes) == 1


def test_stage_without_spectra_is_skipped_never_sent_to_config_fallback(run):
    for path in (run / "nmr_raw").glob("stage_2_*"):
        path.unlink()
    report = json.loads((run / "report.json").read_text(encoding="utf-8"))
    report["observations"] = [r for r in report["observations"] if r["stage"] == "stage_1"]
    (run / "report.json").write_text(json.dumps(report), encoding="utf-8")
    processor = Processor()
    assert analysis.main([str(run)], runner=processor) == 0
    assert [c[c.index("--run-name") + 1] for c in processor.calls] == ["all_stages", "stage_1"]
    assert all(files_of(c) for c in processor.calls)


def test_run_with_no_saved_spectra_reports_missing_and_runs_nothing(tmp_path):
    run = tmp_path / RUN_ID
    (run / "nmr_raw").mkdir(parents=True)
    rows = [{"stage": "stage_1", "observation": n, "status": "NMR_FAILED", "path": None, "error": "NMR idle not confirmed",
             "scheduled_seconds": (n - 1) * 7200.0} for n in (1, 2)]
    (run / "report.json").write_text(json.dumps({"observations": rows}), encoding="utf-8")
    processor = Processor()
    assert analysis.main([str(run)], runner=processor) == 2 and not processor.calls
    text = next((run / "analysis").iterdir()).joinpath("ANALYSIS_SUMMARY.txt").read_text(encoding="utf-8")
    assert "NOT ANALYSED stage_1 #1: no raw spectrum (NMR_FAILED): NMR idle not confirmed" in text


def test_analysis_imports_no_controller_or_instrument_session(run):
    assert "si6_fixed_time" not in vars(analysis) and not hasattr(analysis, "controller_session")
    source = (SCRIPTS / "05_analyze_fixed_time_run.py").read_text(encoding="utf-8")
    for forbidden in ("chemyx_manual", "chemyx_position", "PortProcessLock", "controller_session", "Pump(", "DoseGuard"):
        assert forbidden not in source
