"""Standalone recovery analysis of saved Stage 2 NMR data; no instrument control.

Select a run folder or its nmr_raw directory, or a copied Stage 2 raw-DX folder.
Recover from raw files even when live-analysis results or run JSON are broken.
Each invocation writes a fresh analysis_stage2only folder and never overwrites
raw data, controller state or the background worker's review output.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from uuid import uuid4

from .. import config
from .plot_titles import format_dataset_plot_title, resolve_dataset_display_name
from ..workflows.si6_stage2only_analysis import metadata_time

PROCESSOR = config.REPO_ROOT / "scripts/nmr/process_fid.py"
DEFAULT_ANALYSIS_CONFIG = config.REPO_ROOT / "configs/nmr/analysis.yaml"
STAGE_FILE = re.compile(r"^stage_([12])_(\d+)(?:\.rpc)?\.dx$", re.IGNORECASE)
COLUMNS = ["observation", "status", "raw_file", "eligible", "acquired_at", "timestamp_source",
           "source_value", "raw_sha256", "note"]


def choose_directory():
    """Desktop picker with a terminal path fallback if Tk is unavailable."""
    root = None
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        return filedialog.askdirectory(parent=root, title="Select Stage 2 run or raw NMR data folder", mustexist=True) or None
    except Exception:
        try:
            return input("Stage 2 data folder (or Enter to cancel): ").strip().strip('"') or None
        except EOFError:
            return None
    finally:
        if root is not None:
            try:
                root.destroy()
            except Exception:
                pass


def source_folders(selected):
    selected = Path(selected).expanduser().resolve()
    if not selected.is_dir():
        raise ValueError(f"Not a data directory: {selected}")
    if selected.name.casefold() in {"nmr_raw", "raw_nmr"}:
        return selected.parent, selected
    for name in ("nmr_raw", "raw_nmr"):
        if (selected / name).is_dir():
            return selected, selected / name
    return selected, selected


def saved_rows(root):
    """Use optional run records only to describe missing/duplicate observations."""
    rows = {}
    try:
        report = json.loads((root / "report.json").read_text(encoding="utf-8"))
        candidates = report.get("observations", [])
        if not isinstance(candidates, list):
            candidates = []
    except (OSError, ValueError, AttributeError):
        candidates = []
    try:
        lines = (root / "observations.jsonl").read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        lines = []
    for line in lines:
        try:
            candidates.append(json.loads(line))
        except ValueError:
            continue
    for row in candidates:
        if isinstance(row, dict) and row.get("stage") == "stage_2" and type(row.get("observation")) is int:
            rows[str(row["observation"])] = row
    return rows


def inventory(root, raw):
    """Trust available raw data, never a prior analysis success/failure token."""
    previous = saved_rows(root)
    files = sorted(p for p in raw.iterdir() if p.is_file() and p.suffix.lower() == ".dx")
    labelled = bool(previous) or any(STAGE_FILE.match(p.name) for p in files)
    groups = {}
    ignored_stage1 = []
    for path in files:
        match = STAGE_FILE.match(path.name)
        if match and match[1] == "1":
            ignored_stage1.append(str(path))
            continue
        if labelled and not match:
            continue  # Do not mix unrelated unlabelled files into a recorded run.
        key = str(int(match[2])) if match else path.name[:-7] if path.name.lower().endswith(".rpc.dx") else path.stem
        groups.setdefault(key, []).append(path)
    entries, sources, unusable = [], [], 0
    keys = sorted(set(groups) | set(previous), key=lambda key: (not key.isdigit(), int(key) if key.isdigit() else key))
    for key in keys:
        old = previous.get(key, {})
        preferred = Path(old.get("path") or "").name
        candidates = sorted(groups.get(key, []), key=lambda p: (p.name != preferred, not p.name.lower().endswith(".rpc.dx"), p.name))
        if not candidates:
            entries.append({"observation": key, "status": old.get("status", "MISSING"), "raw_file": "", "eligible": False,
                            "note": old.get("error") or "No saved Stage 2 raw FID"})
            continue
        recovered = False
        for path in candidates:
            entry = {"observation": key, "status": old.get("status", "UNRECORDED"), "raw_file": str(path), "eligible": False}
            if recovered:
                entry["note"] = "Duplicate export of the same observation; using the selected raw FID"
            else:
                try:
                    source = metadata_time(path)
                    entry.update(source, eligible=True, raw_sha256=hashlib.sha256(path.read_bytes()).hexdigest(), note="")
                    sources.append(source)
                    recovered = True
                except Exception as exc:
                    entry["note"] = f"{type(exc).__name__}: {exc}"
            entries.append(entry)
        if not recovered:
            unusable += 1
    return entries, sources, unusable, ignored_stage1


def fresh_output(parent):
    parent = Path(parent).expanduser().resolve()
    while True:
        folder = parent / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid4().hex[:8])
        try:
            folder.mkdir(parents=True, exist_ok=False)
            return folder
        except FileExistsError:
            continue


def run_processor(command, log_path, timeout):
    env = dict(os.environ, MPLBACKEND="Agg", OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1",
               MKL_NUM_THREADS="1", NUMEXPR_NUM_THREADS="1")
    options = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.IDLE_PRIORITY_CLASS} if os.name == "nt" else {}
    with log_path.open("w", encoding="utf-8") as log:
        return subprocess.run(command, cwd=config.REPO_ROOT, env=env, timeout=timeout, stdin=subprocess.DEVNULL,
                              stdout=log, stderr=subprocess.STDOUT, close_fds=True, **options).returncode


def analyze(selected, *, output_dir=None, analysis_config=DEFAULT_ANALYSIS_CONFIG,
            dataset_display_name=None, timeout=3600, runner=None):
    root, raw = source_folders(selected)
    entries, sources, unusable, ignored = inventory(root, raw)
    out = fresh_output(output_dir or root / "analysis_stage2only")
    dataset = resolve_dataset_display_name(configured_name=dataset_display_name, input_paths=[root], output_path=out)
    dataset = format_dataset_plot_title(dataset, "")
    if not dataset.casefold().endswith("stage 2"):
        dataset = format_dataset_plot_title(dataset, "Stage 2")
    with (out / "observation_inventory.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(entries)
    with (out / "acquisition_metadata.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["raw_file", "acquired_at", "timestamp_source", "source_value"])
        writer.writeheader()
        writer.writerows(sources)
    print(f"Stage 2 source: {raw}\nSaved spectra eligible for analysis: {len(sources)}\nOutput: {out}")
    code, error = None, None
    if sources:
        command = [sys.executable, str(PROCESSOR), *[s["raw_file"] for s in sources],
                   "--config", str(config.resolve_repo_path(analysis_config)), "--phase-method", "acme",
                   "--dataset-display-name", dataset, "--output-dir", str(out), "--run-name", "stage_2"]
        try:
            code = (runner or run_processor)(command, out / "processor.log", timeout)
        except Exception as exc:
            code, error = -1, f"{type(exc).__name__}: {exc}"
        if code and error is None:
            error = f"Processor exit {code}; see processor.log for details"
    else:
        error = "No Stage 2 raw FIDs with authoritative acquisition metadata; nothing sent to the processor"
    status = "NO_DATA" if not sources else "FAILED" if code else "PARTIAL" if unusable else "COMPLETE"
    summary = {"status": status, "selected_directory": str(Path(selected).resolve()), "raw_directory": str(raw),
               "analysis_directory": str(out), "processed_directory": str(out / "stage_2"),
               "dataset_display_name": dataset, "phase_method": "acme", "config": str(analysis_config),
               "eligible_spectra": len(sources), "unusable_observations": unusable, "processor_exit_code": code,
               "error": error, "inventory": entries, "ignored_stage1_files": ignored,
               "raw_files_modified": False, "instruments_contacted": False}
    (out / "analysis_summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = [f"{dataset}: {status}", f"Raw data: {raw}", f"Full Stage 2 analysis: {out / 'stage_2'}",
             f"Spectra sent to processor: {len(sources)}; unusable observations: {unusable}",
             "Timing uses JCAMP metadata; filename and file modification times are never substituted."]
    if error:
        lines.append(error)
    lines += [f"Not selected: {e['raw_file'] or 'observation ' + e['observation']}: {e.get('note', '')}"
              for e in entries if not e["eligible"]]
    (out / "ANALYSIS_SUMMARY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    return 2 if not sources else 1 if status != "COMPLETE" else 0


def main(argv=None, *, chooser=None, runner=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", type=Path, help="Run folder, nmr_raw folder or copied Stage 2 DX folder; omit to choose")
    parser.add_argument("--output-dir", type=Path, help="Parent for a NEW analysis folder; existing results are preserved")
    parser.add_argument("--config", type=Path, default=DEFAULT_ANALYSIS_CONFIG, help="Existing NMR processing YAML")
    parser.add_argument("--dataset-display-name", help="Optional dataset label displayed on every plot")
    parser.add_argument("--timeout-seconds", type=float, default=3600, help="Processor timeout (default: one hour); never affects instruments")
    args = parser.parse_args(argv)
    selected = args.directory if args.directory is not None else (chooser or choose_directory)()
    if not selected:
        print("Folder selection cancelled; no analysis started")
        return 0
    try:
        if not 0 < args.timeout_seconds < float("inf"):
            raise ValueError("--timeout-seconds must be finite and positive")
        return analyze(selected, output_dir=args.output_dir, analysis_config=args.config,
                       dataset_display_name=args.dataset_display_name, timeout=args.timeout_seconds, runner=runner)
    except Exception as exc:
        print(f"Stage 2 analysis failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
