"""Offline run summaries from saved measurements, journal evidence and spectra.

No FID processing, peak detection, plateau recalculation, or hardware access.
Missing measurements/spectra stay explicit; acquisition timing is metadata only.
"""
from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime
from pathlib import Path

from .nmr import read_jcamp_fid
from .plot_titles import format_dataset_plot_title, resolve_dataset_display_name


OVERVIEW_COLUMNS = [
    "iteration", "stage", "nmr_attempted", "status", "acquired_at",
    "timestamp_source", "elapsed_hours", "raw_dx_file", "raw_dx_path", "processed_result",
    "peak_ppm", "peak_area", "snr", "prominence", "prominence_snr",
    "qc_pass", "growth_percent", "plateau", "error", "timing_error",
    "spectrum_source", "spectrum_kind",
]


def _csv(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def _true(value) -> bool:
    return str(value).lower() == "true"


def _number(value) -> float | None:
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _journal(run: Path, warnings: list[str]) -> list[dict]:
    path = run / "operation_journal.jsonl"
    if not path.is_file():
        return []
    events = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            try:
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("expected an event mapping")
                events.append(event)
            except (ValueError, TypeError) as exc:
                warnings.append(f"Journal line {line_number} unreadable: {exc}")
    return events


def _processed_index(run: Path, warnings: list[str]) -> dict[str, Path]:
    result = {}
    for table in sorted((run / "processed_nmr").rglob("*peaks_simple.csv")):
        try:
            for row in _csv(table):
                if row.get("file"):
                    result[row["file"]] = table.parent
        except (OSError, csv.Error, UnicodeError) as exc:
            warnings.append(f"Cannot read {table.relative_to(run)}: {exc}")
    return result


def _collect(run: Path, warnings: list[str]) -> tuple[list[dict], dict]:
    """Merge successful rows with failed/unfinished cycle evidence, without QC inference."""
    rows: dict[int, dict] = {}
    try:
        measurements = _csv(run / "time_series.csv")
    except (OSError, csv.Error, UnicodeError) as exc:
        warnings.append(f"Time-series CSV unreadable; using available journal evidence: {exc}")
        measurements = []
    for source in measurements:
        try:
            iteration = int(source["iteration"])
        except (KeyError, ValueError, TypeError):
            warnings.append("Unreadable time-series iteration number; row omitted")
            continue
        row = dict(source, iteration=iteration, raw_dx_file=source.get("file", ""),
                   nmr_attempted=True, qc_pass=_true(source.get("peak_clear")),
                   status="valid" if _true(source.get("peak_clear")) else "qc_rejected")
        rows[iteration] = row
    terminal = {}
    for event in _journal(run, warnings):
        kind = event.get("event_type")
        if kind == "terminal":
            terminal = event
        cycle = event.get("cycle_number")
        if cycle is None or kind not in {
            "measurement_scheduled", "cycle_status", "nmr_retrieved",
            "nmr_acquisition_time", "analysis_result", "measurement_failed",
        }:
            continue
        try:
            cycle = int(cycle)
        except (ValueError, TypeError):
            warnings.append("Unreadable journal cycle number; event omitted")
            continue
        row = rows.setdefault(cycle, {
            "iteration": cycle, "stage": event.get("workflow_phase", ""),
            "status": "not_confirmed", "nmr_attempted": False, "qc_pass": "",
        })
        if kind == "nmr_retrieved":
            row.update(nmr_attempted=True, raw_dx_file=Path(event["result_path"]).name)
        elif kind == "nmr_acquisition_time":
            row.update(acquired_at=event.get("acquired_at", ""),
                       timestamp_source=event.get("timestamp_source", ""))
        elif kind == "analysis_result":
            classification = event.get("result_classification")
            if event.get("result_path"):
                row["processed_result"] = event["result_path"]
            if classification in {"valid", "invalid"}:
                row.update(event.get("analysis_result") or {})
                row.update(nmr_attempted=True, qc_pass=classification == "valid",
                           status="valid" if classification == "valid" else "qc_rejected")
                row["plateau"] = (event.get("plateau_progress") or {}).get("plateau_reached", row.get("plateau", ""))
            if event.get("error_message"):
                row["error"] = event["error_message"]
        elif kind == "measurement_failed":
            row.update(nmr_attempted=True, error=event.get("error_message", ""))
            if row["status"] != "qc_rejected":
                row.update(status="failed", qc_pass=False)
        elif kind == "cycle_status":
            if event.get("raw_path"):
                row["raw_dx_file"] = Path(event["raw_path"]).name
            if event.get("processed_path"):
                row["processed_result"] = event["processed_path"]
            if event.get("status") == "FAILED":
                row["error"] = event.get("error_message", row.get("error", ""))
                if "NMR" in event.get("failed_step", ""):
                    row.update(nmr_attempted=True)
                    if row["status"] != "valid":
                        row.update(status="failed", qc_pass=False)
    index = _processed_index(run, warnings)
    for row in rows.values():
        raw_name = row.get("raw_dx_file", "")
        if not row.get("processed_result") and raw_name in index:
            row["processed_result"] = str(index[raw_name].relative_to(run))
        if row.get("processed_result"):
            processed = run / row["processed_result"]
            for table in sorted(processed.glob("*peaks_simple.csv")):
                try:
                    peaks = _csv(table)
                except (OSError, csv.Error, UnicodeError) as exc:
                    warnings.append(f"Cannot read {table.relative_to(run)}: {exc}")
                    continue
                for peak in peaks:
                    if peak.get("file") == raw_name:
                        for key, source in (("peak_ppm", "peak_ppm"), ("peak_area", "integrated_area"), ("snr", "snr"), ("prominence_snr", "prominence_snr")):
                            if row.get(key) in (None, ""):
                                row[key] = peak.get(source, "")
        # Read metadata, never filename time or mtime. A present but bad DX
        # must not silently fall back to a laptop/journal timestamp.
        raw = run / "raw_nmr" / raw_name if raw_name else None
        row["raw_dx_path"] = str(raw.relative_to(run)) if raw is not None else ""
        try:
            if raw is not None and raw.is_file():
                text = str(read_jcamp_fid(raw).metadata.get("LONG DATE", "")).strip()
                acquired = datetime.strptime(text, "%Y/%m/%d %H:%M:%S%z")
                row.update(acquired_at=acquired.isoformat(), timestamp_source="LONG DATE header")
            elif row.get("acquired_at") and row.get("timestamp_source") == "LONG DATE header":
                acquired = datetime.fromisoformat(row["acquired_at"])
                if acquired.tzinfo is None:
                    raise ValueError("LONG DATE journal time has no timezone")
            else:
                raise ValueError("No authoritative LONG DATE acquisition timestamp")
            row["_time"] = acquired
        except Exception as exc:
            row.update(acquired_at="", timestamp_source="", timing_error=str(exc))
            warnings.append(f"Iteration {row['iteration']}: {exc}")
        row["plateau"] = _true(row["plateau"]) if row.get("plateau") not in (None, "") else ""
    times = [row["_time"] for row in rows.values() if "_time" in row]
    first = min(times) if times else None
    for row in rows.values():
        row["elapsed_hours"] = (row["_time"] - first).total_seconds() / 3600 if "_time" in row else ""
    return sorted(rows.values(), key=lambda row: row["iteration"]), terminal


def _spectra(run: Path, rows: list[dict], warnings: list[str]) -> dict[int, tuple[list, list]]:
    """Use existing corrected regional exports, or exported phased real traces."""
    result = {}
    cache = {}
    for row in rows:
        processed = run / row.get("processed_result", "")
        if not row.get("processed_result") or not processed.is_dir():
            warnings.append(f"Iteration {row['iteration']}: no saved processed real spectrum; not reprocessed")
            continue
        filename = row.get("raw_dx_file", "")
        # Fast mocks reuse byte-identical fixture tables. Reuse the same saved
        # real spectrum too, and record its source rather than pretending a rerun.
        marker = processed / "MOCK_REUSED_PROCESSING.txt"
        if marker.is_file():
            match = re.search(r"byte-identical to (.*?);", marker.read_text(encoding="utf-8"))
            source = match.group(1) if match else ""
            source_row = next((r for r in rows if r.get("raw_dx_file") == source), None)
            if source_row and source_row.get("processed_result"):
                processed = run / source_row["processed_result"]
                filename = source
        candidates = list(sorted(processed.rglob("*target_peak_spectra_long.csv")))
        candidates += list(sorted(processed.rglob("*spectra_csv*.csv")))
        candidates += list(sorted((processed / "spectra_csv").glob("*.csv")))
        # process_fid shortens/renames its CSV exports into the output root.
        for table in sorted(processed.glob("*.csv")):
            if table not in candidates:
                try:
                    with table.open(encoding="utf-8-sig") as handle:
                        if "referenced_ppm" in handle.readline():
                            candidates.append(table)
                except (OSError, UnicodeError) as exc:
                    warnings.append(f"Cannot read spectrum header {table.relative_to(run)}: {exc}")
        for table in candidates:
            try:
                if table not in cache:
                    cache[table] = _csv(table)
                data = cache[table]
                if not data:
                    continue
                regional = "intensity" in data[0] and "ppm" in data[0]
                if regional:
                    data = [r for r in data if r.get("file") == filename]
                    xkey, ykey = "ppm", "intensity"
                else:
                    # Match full-spectrum exports to this raw file through the
                    # pipeline summary; do not take an unrelated batch trace.
                    summaries = sorted(processed.glob("*summary.json"))
                    matched = False
                    for summary in summaries:
                        records = json.loads(summary.read_text(encoding="utf-8")).get("records", [])
                        matched |= any(r.get("file") == filename and Path(r.get("spectrum_csv") or "").name == table.name for r in records)
                    if not matched:
                        continue
                    xkey, ykey = "referenced_ppm", "real"
                points = [(_number(r.get(xkey)), _number(r.get(ykey))) for r in data]
                points = [(x, y) for x, y in points if x is not None and y is not None]
                if not points:
                    continue
                points.sort()
                result[row["iteration"]] = ([p[0] for p in points], [p[1] for p in points])
                row.update(spectrum_source=str(table.relative_to(run)),
                           spectrum_kind="corrected regional real" if regional else "exported phased real")
                break
            except (OSError, ValueError, csv.Error, UnicodeError) as exc:
                warnings.append(f"Spectrum {table.relative_to(run)} unavailable: {exc}")
        if row["iteration"] not in result:
            warnings.append(f"Iteration {row['iteration']}: no saved processed real spectrum; not reprocessed")
    return result


def _plots(out: Path, rows: list[dict], spectra: dict, dataset: str, warnings: list[str]) -> list[dict]:
    # Explicit Agg canvas avoids changing the GUI/global matplotlib backend.
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figures = []
    valid = [r for r in rows if r.get("status") == "valid"]
    timed_valid = sorted((r for r in valid if _number(r.get("peak_area")) is not None and "_time" in r), key=lambda r: (r["_time"], r["iteration"]))
    for filename, label in (
        ("peak_area_vs_time.png", "Tracked Peak Area vs Time"),
        ("peak_area_percent_change.png", "Tracked Peak Area Percent Change"),
        ("all_nmr_spectra.png", "All NMR Spectra (Processed Real Traces)"),
    ):
        fig = Figure(figsize=(10, 6), constrained_layout=True)
        FigureCanvasAgg(fig)
        ax = fig.subplots()
        title = format_dataset_plot_title(dataset, label)
        ax.set_title(title)
        if filename == "peak_area_vs_time.png":
            # One series per stage avoids connecting across reagent additions.
            for stage in dict.fromkeys(r.get("stage", "") for r in timed_valid):
                group = [r for r in timed_valid if r.get("stage", "") == stage]
                ax.plot([r["elapsed_hours"] for r in group], [float(r["peak_area"]) for r in group], "o-", label=stage or "Measurements")
            ax.set(xlabel="Elapsed time from first acquisition (h; LONG DATE)", ylabel="Integrated tracked-peak area")
        elif filename == "peak_area_percent_change.png":
            changes = [r for r in valid if _number(r.get("growth_percent")) is not None]
            for stage in dict.fromkeys(r.get("stage", "") for r in changes):
                group = [r for r in changes if r.get("stage", "") == stage]
                ax.plot([r["iteration"] for r in group], [float(r["growth_percent"]) for r in group], "o-", label=stage or "Measurements")
            ax.axhline(0, color="gray", linewidth=0.8)
            ax.set(xlabel="Iteration (change within its stage)", ylabel="Existing peak-area change (%)")
        else:
            offset = 0.0
            # Stacking retains physical intensity differences (no normalization).
            span = max((max(y) - min(y) for _, y in spectra.values()), default=1.0) or 1.0
            for row in rows:
                trace = spectra.get(row["iteration"])
                if trace:
                    x, y = trace
                    ax.plot(x, [value + offset for value in y], linewidth=0.9,
                            label=f"{row['iteration']}: {row.get('stage', '')} [{row['status']}]")
                    offset += span * 1.1
            ax.invert_xaxis()
            ax.set(xlabel="Chemical shift (ppm)", ylabel="Processed real intensity + vertical offset")
        if ax.get_legend_handles_labels()[0]:
            ax.legend(fontsize=7, loc="upper left", bbox_to_anchor=(1.01, 1))
        if filename == "peak_area_vs_time.png" and not timed_valid:
            ax.text(0.5, 0.5, "No valid measurements with authoritative acquisition timing", ha="center", transform=ax.transAxes)
            warnings.append(f"{filename}: no timed valid points; no timestamp fallback used")
        elif filename == "all_nmr_spectra.png" and not spectra:
            ax.text(0.5, 0.5, "No saved processed real spectra available; no FIDs reprocessed", ha="center", transform=ax.transAxes)
        elif filename == "peak_area_percent_change.png" and not changes:
            ax.text(0.5, 0.5, "No existing within-stage percent changes available", ha="center", transform=ax.transAxes)
        fig.savefig(out / filename, dpi=160)
        figures.append({"file": filename, "dataset_display_name": dataset, "visible_title": title})
    return figures


def summarize_run(run_dir: str | Path) -> Path:
    """Create/update final_nmr_summary only; source results/journal stay untouched."""
    run = Path(run_dir).resolve()
    if not run.is_dir():
        raise ValueError(f"Run folder does not exist: {run}")
    warnings: list[str] = []
    rows, terminal = _collect(run, warnings)
    if not rows:
        raise ValueError("No iteration evidence in time_series.csv or operation_journal.jsonl")
    manifest_path = run / "manifest.json"
    try:
        original_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
        if not isinstance(original_manifest, dict):
            raise ValueError("expected a manifest mapping")
    except (OSError, ValueError) as exc:
        warnings.append(f"Source manifest unreadable; using run-directory identity: {exc}")
        original_manifest = {}
    dataset = format_dataset_plot_title(resolve_dataset_display_name(
        configured_name=original_manifest.get("dataset_display_name") or run.name, output_path=run,
    ), "")
    spectra = _spectra(run, rows, warnings)
    valid = sorted((r for r in rows if r["status"] == "valid"),
                   key=lambda r: (r.get("_time").timestamp() if "_time" in r else math.inf, r["iteration"]))
    timed = sorted((r for r in rows if "_time" in r), key=lambda r: (r["_time"], r["iteration"]))
    plateau = [r for r in valid if _true(r.get("plateau"))]
    areas = [float(r["peak_area"]) for r in valid if _number(r.get("peak_area")) is not None]
    overall_change = (areas[-1] - areas[0]) / abs(areas[0]) * 100 if areas and areas[0] != 0 else ""
    summary = {
        "dataset_display_name": dataset, "terminal_status": terminal.get("terminal_status", "unknown"),
        "total_attempted_nmr_measurements": sum(_true(r.get("nmr_attempted")) for r in rows),
        "total_valid_measurements": len(valid),
        "total_failed_or_qc_rejected": sum(r["status"] in {"failed", "qc_rejected"} for r in rows),
        "total_unconfirmed_cycles": sum(r["status"] == "not_confirmed" for r in rows),
        "first_acquisition_time": timed[0]["acquired_at"] if timed else "",
        "last_acquisition_time": timed[-1]["acquired_at"] if timed else "",
        "total_elapsed_hours": (timed[-1]["_time"] - timed[0]["_time"]).total_seconds() / 3600 if timed else "",
        "starting_peak_area": areas[0] if areas else "", "final_peak_area": areas[-1] if areas else "",
        "overall_area_change_percent": overall_change,
        "plateau_reached": bool(plateau),
        "first_plateau_iteration": plateau[0]["iteration"] if plateau else "",
        "processed_spectra_available": len(spectra),
    }
    out = run / "final_nmr_summary"
    out.mkdir(exist_ok=True)
    _write_csv(out / "nmr_iteration_overview.csv", rows, OVERVIEW_COLUMNS)
    _write_csv(out / "nmr_run_summary.csv", [summary], list(summary))
    _write_csv(out / "nmr_qc_overview.csv", rows, ["iteration", "stage", "status", "qc_pass", "peak_ppm", "peak_area", "snr", "growth_percent", "error"])
    figures = _plots(out, rows, spectra, dataset, warnings)
    payload = {
        "dataset_display_name": dataset, "source_run": run.name,
        "figures": figures, "warnings": warnings,
        "timing_source": "JCAMP LONG DATE (or its explicit journal record)",
        "plateau_source": "existing per-iteration workflow decisions; not recalculated",
        "percent_change_source": "existing within-stage growth_percent; not recalculated across stages",
        "overall_area_change_note": "Descriptive first-to-last valid area; may span reagent stages, not a plateau decision",
        "source_files": ["time_series.csv", "operation_journal.jsonl", "processed_nmr/", "raw_nmr/"],
    }
    (out / "manifest.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return out
