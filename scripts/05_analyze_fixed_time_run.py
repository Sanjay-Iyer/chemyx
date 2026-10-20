"""Post-run NMR analysis of one fixed-time results folder; never controls instruments.

Reads <run>/nmr_raw and the run's own reports, and writes a NEW folder
<run>/analysis/<timestamp>/. Raw files are only read: never moved, edited or
written over. Missing or failed observations are listed, never substituted.

Each group (both stages, Stage 1, Stage 2) runs the existing production
processor scripts/nmr/process_fid.py in its own process: FFT, ACME phase, ALS
baseline, peak detection and integration, spectrum/overlay plots, peak area
versus JCAMP LONG DATE time, CSV and JSON summaries. One failed group cannot
stop the others. Rerun at any time; every run gets its own folder.

    python scripts\\05_analyze_fixed_time_run.py                        # newest live run
    python scripts\\05_analyze_fixed_time_run.py results\\fixed_time\\live\\<run id>
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.workflows.si6_fixed_time_nmr import local_fid

PROCESSOR = config.REPO_ROOT / "scripts/nmr/process_fid.py"
LIVE_ROOT = config.REPO_ROOT / "results/fixed_time/live"
RAW_NAME = re.compile(r"^(stage_[12])_(\d{4})(\.rpc)?\.dx$")
LONG_DATE = re.compile(r"^##LONG\s*DATE=(.*)$", re.IGNORECASE | re.MULTILINE)
GROUPS = (("all_stages", "Stages 1-2", None), ("stage_1", "Stage 1", "stage_1"), ("stage_2", "Stage 2", "stage_2"))
COLUMNS = ["stage", "observation", "scheduled_hours", "nmr_status", "analysed", "raw_file", "long_date", "raw_sha256", "note"]


def run_processor(command, log_path):
    """The existing processor in a separate process; its output goes to a log."""
    with log_path.open("w", encoding="utf-8") as log:
        return subprocess.run(command, cwd=config.REPO_ROOT, stdout=log, stderr=subprocess.STDOUT).returncode


def resolve_run(path):
    if path is None:
        runs = sorted(p for p in LIVE_ROOT.glob("SI6_FIXED_*") if p.is_dir())
        if not runs:
            raise SystemExit(f"No fixed-time run folder under {LIVE_ROOT}; pass the run folder path")
        return runs[-1]
    run = Path(path).resolve()
    return run.parent if run.name == "nmr_raw" else run


def report_rows(run):
    """Observation rows the controller recorded (report.json, else observations.jsonl)."""
    try:
        rows = json.loads((run / "report.json").read_text(encoding="utf-8")).get("observations")
        if isinstance(rows, list):
            return rows
    except (OSError, ValueError, AttributeError):
        pass
    rows = []
    try:
        for line in (run / "observations.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    except OSError:
        pass
    return rows


def inventory(run):
    raw_dir = run / "nmr_raw"
    found = {}
    for path in sorted(raw_dir.glob("*")) if raw_dir.is_dir() else []:
        match = RAW_NAME.match(path.name)
        stem = path.name.split(".", 1)[0]
        key = tuple(stem.rsplit("_", 1)) if re.fullmatch(r"stage_[12]_\d{4}", stem) else None
        if key:
            found.setdefault(key, {"files": [], "result": None})
            if match:
                found[key]["files"].append(path)
            elif path.name.endswith(".result.json"):
                try:
                    found[key]["result"] = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    pass
    rows = {}
    for row in report_rows(run):
        if isinstance(row, dict) and row.get("stage") in ("stage_1", "stage_2") and isinstance(row.get("observation"), int):
            rows[(row["stage"], f"{row['observation']:04d}")] = row
    entries = []
    for key in sorted(set(found) | set(rows)):
        row, files = rows.get(key, {}), found.get(key, {"files": [], "result": None})
        result = files["result"] if isinstance(files["result"], dict) else {}
        status = row.get("status") or result.get("status") or "UNRECORDED"
        reported = row.get("path") or result.get("path")
        # Prefer the file the worker reported as saved; otherwise any valid FID.
        ordered = sorted(files["files"], key=lambda p: (reported is None or Path(reported).name != p.name, p.name))
        chosen, notes = None, []
        for candidate in ordered:
            text = candidate.read_text(encoding="utf-8", errors="replace")
            if chosen is None and local_fid(text):
                chosen = candidate
            elif not local_fid(text):
                notes.append(f"{candidate.name} is not identifiable as a raw FID; not analysed")
        error = row.get("error") or result.get("error")
        if chosen is None:
            notes.insert(0, f"no raw spectrum ({status})" + (f": {error}" if error else ""))
        long_date = None
        if chosen is not None:
            date = LONG_DATE.search(chosen.read_text(encoding="utf-8", errors="replace"))
            long_date = date.group(1).strip() if date else None
            if long_date is None:
                notes.append("no LONG DATE header; the processor reports the timing source it used")
        scheduled = row.get("scheduled_seconds")
        entries.append({"stage": key[0], "observation": int(key[1]),
                        "scheduled_hours": round(scheduled / 3600, 4) if isinstance(scheduled, (int, float)) else None,
                        "nmr_status": status, "analysed": chosen is not None,
                        "raw_file": str(chosen) if chosen else None, "long_date": long_date,
                        "raw_sha256": hashlib.sha256(chosen.read_bytes()).hexdigest() if chosen else None,
                        "note": "; ".join(notes)})
    return entries


def fresh_folder(run):
    base = run / "analysis" / datetime.now().strftime("%Y%m%d_%H%M%S")
    folder, n = base, 1
    while True:
        try:
            folder.mkdir(parents=True)
            return folder
        except FileExistsError:
            n += 1
            folder = base.with_name(f"{base.name}_{n}")


def analyze(run, *, runner=run_processor, phase_method="acme"):
    run = Path(run).resolve()
    if not (run / "nmr_raw").is_dir() and not (run / "report.json").is_file():
        raise SystemExit(f"Not a fixed-time results folder (no nmr_raw or report.json): {run}")
    entries = inventory(run)
    out = fresh_folder(run)
    with (out / "observation_inventory.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(entries)
    groups = []
    for name, label, stage in GROUPS:
        files = [e["raw_file"] for e in entries if e["analysed"] and (stage is None or e["stage"] == stage)]
        if not files:  # Never call the processor without files: it would fall back to its configured dataset.
            groups.append({"group": name, "files": 0, "status": "SKIPPED (no saved spectra)"})
            continue
        command = [sys.executable, str(PROCESSOR), *files, "--phase-method", phase_method,
                   "--dataset-display-name", f"{run.name} {label}",
                   "--output-dir", str(out), "--run-name", name]
        try:
            code = runner(command, out / f"{name}_process_fid.log")
        except Exception as exc:  # One failed group never stops the rest.
            code = f"{type(exc).__name__}: {exc}"
        groups.append({"group": name, "files": len(files), "exit_code": code, "output": str(out / name),
                       "log": str(out / f"{name}_process_fid.log"), "status": "COMPLETE" if code == 0 else "FAILED"})
    counts = {}
    for e in entries:
        counts[e["nmr_status"]] = counts.get(e["nmr_status"], 0) + 1
    summary = {"run": str(run), "run_id": run.name, "analysis_folder": str(out),
               "created": datetime.now().astimezone().isoformat(timespec="seconds"),
               "phase_method": phase_method, "processor": str(PROCESSOR),
               "observations": len(entries), "analysed_spectra": sum(e["analysed"] for e in entries),
               "status_counts": counts, "groups": groups,
               "not_analysed": [{k: e[k] for k in ("stage", "observation", "nmr_status", "note")} for e in entries if not e["analysed"]],
               "raw_files_modified": False, "instruments_contacted": False}
    (out / "analysis_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = [f"{run.name} NMR analysis ({phase_method})", f"Folder: {out}",
             f"Observations: {len(entries)}; spectra analysed: {summary['analysed_spectra']}; status counts: {counts}"]
    lines += [f"  {g['group']}: {g['status']}" + (f" -> {g['output']}" if "output" in g else "") for g in groups]
    lines += [f"  NOT ANALYSED {e['stage']} #{e['observation']}: {e['note']}" for e in entries if not e["analysed"]]
    (out / "ANALYSIS_SUMMARY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    attempted = [g for g in groups if "exit_code" in g]
    return 2 if not attempted else 0 if all(g["exit_code"] == 0 for g in attempted) else 1


def main(argv=None, *, runner=run_processor):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("run", nargs="?", type=Path, help="Fixed-time run folder (default: newest under results/fixed_time/live)")
    parser.add_argument("--phase-method", default="acme", help="Passed to process_fid.py (default: acme)")
    args = parser.parse_args(argv)
    return analyze(resolve_run(args.run), runner=runner, phase_method=args.phase_method)


if __name__ == "__main__":
    raise SystemExit(main())
