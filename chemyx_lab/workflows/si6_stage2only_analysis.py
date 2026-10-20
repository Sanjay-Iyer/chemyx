"""Optional background NMR review for stage2only. Never controls instruments.

The experiment does not import this module or wait for this process. Read only
completed observations, process them serially with the existing ACME processor,
and publish review snapshots. Every error/timeout is confined to this process.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import quote

from .. import config
from ..analysis.plot_titles import resolve_dataset_display_name
from ..runtime_state import write_json_atomic

PROCESSOR = config.REPO_ROOT / "scripts/nmr/process_fid.py"
ANALYSIS_CONFIG = config.REPO_ROOT / "configs/nmr/analysis.yaml"
PROCESSING_TIMEOUT_SECONDS = 600


def parent_alive(pid):
    if pid is None:
        return True
    if os.name == "nt":
        # os.kill(pid, 0) is not used on Windows: use a read-only process handle.
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        handle = kernel.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE only
        if not handle:
            return ctypes.get_last_error() != 87  # Invalid PID means exited; access denied is inconclusive.
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258  # WAIT_TIMEOUT: still running
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def metadata_time(path):
    """Use the production hierarchy with NO filename/mtime fallback."""
    from ..analysis.nmr import read_jcamp_fid
    script_dir = str(config.REPO_ROOT / "scripts/nmr")
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    from _common import parse_acquisition_timestamp
    metadata = read_jcamp_fid(path).metadata
    timestamp, source = parse_acquisition_timestamp(metadata, filepath=None)
    if timestamp is None:
        raise ValueError(f"No authoritative JCAMP acquisition timestamp in {path.name}; no filename/mtime substitution")
    key = {"LONG DATE header": "LONG DATE", "$DATE epoch header": "$DATE"}.get(source, source.split(" run metadata")[0])
    return {"raw_file": str(path), "acquired_at": timestamp.isoformat(), "timestamp_source": source,
            "source_value": metadata.get(key, "")}


def observation_rows(run):
    """Ignore incomplete last lines; the controller never waits for this reader."""
    try:
        lines = (run / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    rows = {}
    for line in lines:
        try:
            row = json.loads(line)
            number = row.get("observation")
            if (row.get("stage") == "stage_2" and type(number) is int and number > 0
                    and row.get("physical_cleanup") == "COMPLETE"):
                rows[number] = row
        except (ValueError, AttributeError):
            continue
    return [rows[number] for number in sorted(rows)]


def saved_path(run, row):
    path = Path(row.get("path") or "").resolve()
    stem = f"stage_2_{row['observation']:04d}"
    if path.parent != (run / "nmr_raw").resolve() or path.name not in {stem + ".dx", stem + ".rpc.dx"}:
        raise ValueError("Saved observation does not point to its own run's raw FID")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def fresh_snapshot(folder, number):
    base = folder / f"stage_2_{number:04d}"
    path, suffix = base, 1
    while True:
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            suffix += 1
            path = base.with_name(f"{base.name}_{suffix}")


def process_snapshot(run, snapshot, sources, dataset, *, runner=None, timeout=PROCESSING_TIMEOUT_SECONDS):
    """One cumulative snapshot, in a killable processor subprocess."""
    if not sources:
        raise ValueError("No explicit raw inputs; never use the processor's configured fallback dataset")
    command = [sys.executable, str(PROCESSOR), *[s["raw_file"] for s in sources],
               "--config", str(ANALYSIS_CONFIG), "--phase-method", "acme",
               "--dataset-display-name", dataset, "--output-dir", str(snapshot), "--run-name", "processed"]
    with (snapshot / "acquisition_metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["raw_file", "acquired_at", "timestamp_source", "source_value"])
        writer.writeheader()
        writer.writerows(sources)
    env = dict(os.environ, MPLBACKEND="Agg", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.IDLE_PRIORITY_CLASS} if os.name == "nt" else {}
    with (snapshot / "processor.log").open("w", encoding="utf-8") as log:
        completed = (runner or subprocess.run)(command, cwd=config.REPO_ROOT, env=env, timeout=timeout,
                         stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, close_fds=True, **options)
    if completed.returncode:
        raise RuntimeError(f"NMR processor exit {completed.returncode}; see {snapshot / 'processor.log'}")
    return snapshot / "processed"


class ReviewWorker:
    def __init__(self, run, *, processor=None, timestamp_reader=None):
        self.run = Path(run).resolve()
        self.folder = self.run / "analysis_live"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.dataset = resolve_dataset_display_name(output_path=self.run) + " Stage 2"
        self.processor = processor or process_snapshot
        self.timestamp_reader = timestamp_reader or metadata_time
        self.handled, self.sources, self.jobs = set(), [], []
        self.latest = None

    def publish(self, status):
        """Optional dashboard/log writes have no bearing on processing or control."""
        payload = {"status": status, "run": str(self.run), "dataset_display_name": self.dataset,
                   "updated_at": datetime.now(timezone.utc).isoformat(), "analysis_controls_run": False,
                   "latest_completed_snapshot": str(self.latest) if self.latest else None, "jobs": self.jobs}
        try:
            write_json_atomic(self.folder / "status.json", payload)
        except Exception as exc:
            print(f"Optional analysis status write failed: {exc}", flush=True)
        try:
            def link(path, label):
                href = quote(Path(path).relative_to(self.folder).as_posix())
                return f'<a href="{href}">{html.escape(label)}</a>'
            rows = []
            for job in self.jobs:
                detail = html.escape(job.get("error", ""))
                if job.get("snapshot"):
                    snapshot = Path(job["snapshot"])
                    detail = link(snapshot / "processor.log", "Processing log") + " " + detail
                rows.append(f"<tr><td>{job['observation']}</td><td>{html.escape(job['status'])}</td><td>{detail}</td></tr>")
            artifacts = "<p>No completed analysis yet. Measurements continue independently.</p>"
            if self.latest:
                files = sorted(p for p in self.latest.rglob("*") if p.suffix.lower() in {".png", ".svg", ".pdf", ".csv", ".json"})
                artifacts = "<ul>" + "".join(f"<li>{link(p, str(p.relative_to(self.latest)))}</li>" for p in files) + "</ul>"
                image = next((p for p in files if p.suffix.lower() == ".png" and "region" in p.name), None)
                if image:
                    artifacts = f'<img style="max-width:100%" src="{quote(image.relative_to(self.folder).as_posix())}" alt="Latest processed spectrum">' + artifacts
            page = (f'<!doctype html><meta charset="utf-8"><meta http-equiv="refresh" content="15">'
                    f"<title>{html.escape(self.dataset)} live analysis</title>"
                    '<style>body{font:16px system-ui;max-width:1100px;margin:24px auto;padding:0 16px}td,th{padding:6px;text-align:left}table{border-collapse:collapse}tr{border-bottom:1px solid #ddd}</style>'
                    f"<h1>{html.escape(self.dataset)}</h1><p>Analysis: {html.escape(status)}. This page refreshes every 15 seconds. Analysis does not control the experiment.</p>"
                    f"<h2>Latest completed analysis</h2>{artifacts}<h2>Measurements</h2>"
                    '<table><tr><th>Measurement</th><th>Analysis status</th><th>Details</th></tr>' + "".join(rows) + "</table>")
            (self.folder / "index.html").write_text(page, encoding="utf-8")
        except Exception as exc:
            print(f"Optional analysis page write failed: {exc}", flush=True)

    def tick(self):
        for row in observation_rows(self.run):
            number = row["observation"]
            if number in self.handled:
                continue
            job = {"observation": number, "status": "PROCESSING"}
            self.jobs.append(job)
            try:
                if row.get("status") != "NMR_SAVED":
                    job.update(status="SKIPPED", error=row.get("error") or row.get("status", "no saved raw FID"))
                    continue
                path = saved_path(self.run, row)
                timestamp = self.timestamp_reader(path)
                self.sources.append(timestamp)
                snapshot = fresh_snapshot(self.folder, number)
                job["snapshot"] = str(snapshot)
                self.publish("PROCESSING")
                self.latest = self.processor(self.run, snapshot, list(self.sources), self.dataset)
                job["status"] = "COMPLETE"
            except Exception as exc:
                job.update(status="FAILED", error=f"{type(exc).__name__}: {exc}")
                print(f"Measurement {number} analysis failed: {job['error']}", flush=True)
            finally:
                self.handled.add(number)
                self.publish("WAITING")


def watch(run, *, parent_pid=None, once=False, poll_seconds=2, worker=None):
    worker = worker or ReviewWorker(run)
    worker.publish("WAITING")
    while True:
        worker.tick()
        try:
            report = json.loads((worker.run / "report.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {}
        terminal = report.get("status") in {"COMPLETE", "FAILED", "INTERRUPTED", "DECLINED"}
        if once or terminal or not parent_alive(parent_pid):
            # Re-read once to include the last observation written before final report.
            worker.tick()
            worker.publish("FINISHED")
            return worker
        time.sleep(poll_seconds)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--once", action="store_true", help="Process currently saved observations, then exit")
    args = parser.parse_args(argv)
    try:
        watch(args.run_dir, parent_pid=args.parent_pid, once=args.once)
        return 0
    except Exception as exc:
        print(f"Optional analysis worker failed: {type(exc).__name__}: {exc}", flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
