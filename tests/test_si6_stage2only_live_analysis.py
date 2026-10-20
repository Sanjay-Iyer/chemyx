"""Review processing is independent of the stage2only instrument controller."""
from copy import deepcopy
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from chemyx_lab import config
from chemyx_lab.workflows import si6_stage2only as stage2
from chemyx_lab.workflows import si6_stage2only_analysis as review
from chemyx_lab.workflows import si6_fixed_time as fixed
from test_si6_stage2only import live  # shared fixture: fake instruments, live control path

FID = (config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx").read_bytes().replace(b"\r\r\n", b"\r\n")


def add_observation(run, number, *, status="NMR_SAVED", content=FID):
    raw = run / "nmr_raw" / f"stage_2_{number:04d}.dx"
    raw.parent.mkdir(parents=True, exist_ok=True)
    if status == "NMR_SAVED":
        raw.write_bytes(content)
    row = {"stage": "stage_2", "observation": number, "status": status,
           "path": str(raw) if status == "NMR_SAVED" else None, "physical_cleanup": "COMPLETE",
           "scheduled_seconds": (number - 1) * 1800}
    with (run / "observations.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    return raw


@pytest.mark.parametrize("fault", ["cannot_start", "crashed", "hung", "garbage_analysis_status"])
def test_analysis_fault_never_stops_or_shortens_the_full_six_hour_run(live, monkeypatch, fault):
    # The full sampling recipe has its own integration test. Isolate scheduling
    # from optional analysis here, without repeating 13 physical mock cycles.
    def sample(self, _acquire):
        self.clock.sleep(60)
        return {"status": "NMR_MISSING"}
    monkeypatch.setattr(stage2.Stage2OnlyRig, "sample", sample)
    def launch(output):
        if fault == "cannot_start":
            raise OSError("Analysis dependency/process unavailable")
        folder = Path(output) / "analysis_live"
        folder.mkdir()
        (folder / "status.json").write_text('{"analysis completed reaction early' if fault == "garbage_analysis_status"
            else json.dumps({"status": fault, "reaction_complete": True, "stop_run": True}))
        return {"status": "STARTED", "index": str(folder / "index.html"), "pid": 123, "controls_run": False}
    monkeypatch.setattr(stage2, "start_review_worker", launch)
    result = live.run()
    assert result["status"] == "COMPLETE" and result["physical_cleanup"]
    assert len(result["observations"]) == 13
    assert [r["scheduled_seconds"] for r in result["observations"]] == list(range(0, 21601, 1800))
    assert result["stages"][0]["duration_seconds"] == 21600
    assert not result["analysis_controls_run"] and not result["dose_performed"]
    if fault == "cannot_start":
        assert result["live_analysis"]["status"] == "UNAVAILABLE"


def test_launcher_never_waits_for_analysis_and_uses_no_instrument_handles(tmp_path, monkeypatch):
    calls = []
    class Child:
        pid = 123
        def wait(self, *_, **__):
            pytest.fail("Experiment waited for optional analysis")
        poll = communicate = wait
    def popen(command, **kwargs):
        calls.append((command, kwargs))
        return Child()
    monkeypatch.setattr(stage2.subprocess, "Popen", popen)
    result = stage2.start_review_worker(tmp_path)
    command, kwargs = calls[0]
    assert "chemyx_lab.workflows.si6_stage2only_analysis" in command and "--parent-pid" in command
    assert kwargs["stdin"] == subprocess.DEVNULL and kwargs["close_fds"] is True
    assert kwargs["env"]["OPENBLAS_NUM_THREADS"] == "1" and kwargs["env"]["MPLBACKEND"] == "Agg"
    if os.name == "nt":
        assert kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW
        assert kwargs["creationflags"] & subprocess.IDLE_PRIORITY_CLASS
    assert result["controls_run"] is False


def test_worker_continues_after_crash_timeout_and_missing_nmr_without_changing_raw_data(tmp_path):
    run = tmp_path / "SI6_STAGE2ONLY_TEST"
    paths = [add_observation(run, i) for i in (1, 2, 4)]
    add_observation(run, 3, status="NMR_MISSING")
    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    calls = []
    def processor(run, snapshot, sources, dataset):
        calls.append((snapshot.name, len(sources), dataset))
        if len(calls) == 1:
            raise RuntimeError("Processor crashed")
        if len(calls) == 2:
            raise subprocess.TimeoutExpired("processor", 600)
        out = snapshot / "processed"
        out.mkdir()
        (out / "results.csv").write_text("review data")
        return out
    worker = review.ReviewWorker(run, processor=processor)
    worker.tick()
    worker.tick()  # No duplicate processing of the same completed observations.
    assert [j["status"] for j in worker.jobs] == ["FAILED", "FAILED", "SKIPPED", "COMPLETE"]
    assert [c[1] for c in calls] == [1, 2, 3]
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths} == before
    status = json.loads((worker.folder / "status.json").read_text())
    assert status["latest_completed_snapshot"] == str(worker.latest) and not status["analysis_controls_run"]
    page = (worker.folder / "index.html").read_text()
    assert "refresh" in page and worker.dataset in page and "Processor crashed" in page


def test_missing_metadata_fails_only_that_analysis_without_filename_or_mtime_substitution(tmp_path):
    run = tmp_path / "SI6_STAGE2ONLY_METADATA"
    missing = b"\n".join(line for line in FID.splitlines() if not line.startswith((b"##LONG DATE=", b"##$DATE=", b"##$TIMESTAMP=", b"##TIMESTAMP=")))
    add_observation(run, 1, content=missing)
    add_observation(run, 2)
    calls = []
    worker = review.ReviewWorker(run, processor=lambda *args: calls.append(args) or args[1])
    worker.tick()
    assert [j["status"] for j in worker.jobs] == ["FAILED", "COMPLETE"]
    assert "No authoritative JCAMP" in worker.jobs[0]["error"] and len(calls) == 1
    assert worker.sources[0]["timestamp_source"] == "LONG DATE header"


def test_snapshot_command_preserves_acme_dataset_identity_explicit_inputs_and_metadata(tmp_path):
    run = tmp_path / "SI6_STAGE2ONLY_SOURCE"
    raw = add_observation(run, 1)
    snapshot = review.fresh_snapshot(run / "analysis_live", 1)
    captured = []
    def runner(command, **kwargs):
        captured.append((command, kwargs))
        return SimpleNamespace(returncode=0)
    dataset = review.ReviewWorker(run).dataset
    source = review.metadata_time(raw)
    out = review.process_snapshot(run, snapshot, [source], dataset, runner=runner)
    command, options = captured[0]
    assert command[2:command.index("--config")] == [str(raw)]
    assert command[command.index("--phase-method") + 1] == "acme"
    assert command[command.index("--dataset-display-name") + 1] == dataset
    assert options["timeout"] == 600 and options["close_fds"]
    assert out == snapshot / "processed"
    table = list(csv.DictReader((snapshot / "acquisition_metadata.csv").open()))
    assert table[0]["timestamp_source"] == "LONG DATE header" and "2026-08-10" in table[0]["acquired_at"]
    assert table[0]["source_value"] == "2026/08/10 17:27:21-0400"
    assert review.fresh_snapshot(run / "analysis_live", 1) != snapshot  # Never overwrite prior review output.


@pytest.mark.parametrize("failure", ["crash", "timeout"])
def test_processor_exit_and_timeout_are_reported_as_optional_analysis_failures(tmp_path, failure):
    run = tmp_path / "run"
    raw = add_observation(run, 1)
    snapshot = review.fresh_snapshot(run / "analysis_live", 1)
    def runner(*_, **__):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("processor", 600)
        return SimpleNamespace(returncode=7)
    with pytest.raises((RuntimeError, subprocess.TimeoutExpired)):
        review.process_snapshot(run, snapshot, [review.metadata_time(raw)], run.name, runner=runner)
    with pytest.raises(ValueError, match="No explicit raw inputs"):
        review.process_snapshot(run, snapshot, [], run.name, runner=runner)


def test_worker_finishes_pending_reviews_after_controller_completion_or_exit(tmp_path, monkeypatch):
    run = tmp_path / "completed"
    add_observation(run, 1)
    (run / "report.json").write_text('{"status":"COMPLETE"}')
    worker = review.ReviewWorker(run, processor=lambda _run, snapshot, *_: snapshot)
    review.watch(run, worker=worker, parent_pid=99999999)
    assert worker.jobs[0]["status"] == "COMPLETE"
    assert json.loads((worker.folder / "status.json").read_text())["status"] == "FINISHED"
    assert review.parent_alive(os.getpid())


def test_real_hung_processor_is_killed_within_its_own_deadline(tmp_path, monkeypatch):
    run = tmp_path / "hung_analysis"
    raw = add_observation(run, 1)
    sleeper = tmp_path / "hung_processor.py"
    sleeper.write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(review, "PROCESSOR", sleeper)
    snapshot = review.fresh_snapshot(run / "analysis_live", 1)
    import time
    start = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        review.process_snapshot(run, snapshot, [review.metadata_time(raw)], run.name, timeout=.2)
    assert time.monotonic() - start < 5


def test_actual_background_launch_exits_after_reviewing_completed_measurements(tmp_path):
    run = tmp_path / "SI6_STAGE2ONLY_BACKGROUND"
    add_observation(run, 1, status="NMR_MISSING")
    (run / "report.json").write_text('{"status":"COMPLETE"}')
    launched = stage2.start_review_worker(run)
    import time
    deadline = time.monotonic() + 20
    status = {}
    while time.monotonic() < deadline:
        try:
            status = json.loads(Path(launched["status_file"]).read_text())
        except (OSError, ValueError):
            pass
        if status.get("status") == "FINISHED":
            break
        time.sleep(.05)
    assert status.get("status") == "FINISHED", (run / "analysis_live/worker.log").read_text()
    assert status["jobs"][0]["status"] == "SKIPPED"
    assert Path(launched["index"]).is_file() and launched["controls_run"] is False


def test_real_processor_produces_review_plots_with_actual_run_titles(tmp_path):
    run = tmp_path / "SI6_STAGE2ONLY_PLOT_SOURCE"
    add_observation(run, 1)
    worker = review.ReviewWorker(run)
    worker.tick()
    assert worker.jobs[0]["status"] == "COMPLETE", worker.jobs[0]
    assert list(worker.latest.rglob("*.png")), "No review plots produced"
    summary = next(worker.latest.glob("*_summary.json"))
    assert json.loads(summary.read_text())["parameters"]["dataset_display_name"] == worker.dataset
    manifests = list(worker.latest.rglob("*target_peak_plot_manifest.csv"))
    assert manifests, "Missing plot-title provenance"
    manifest = list(csv.DictReader(manifests[0].open(encoding="utf-8")))
    assert manifest and {r["dataset"] for r in manifest} == {worker.dataset}
    assert all(r["visible_title"].startswith(worker.dataset + " ") for r in manifest)
