"""Output helpers for NMR analysis runs."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

from .nmr import PeakResult


RESULT_COLUMNS = [
    "file",
    "source_path",
    "target_ppm",
    "peak_ppm",
    "snr",
    "prominence_snr",
    "prominence",
    "width_ppm",
    "peaks_considered",
    "peak_height",
    "baseline",
    "noise",
    "points_in_window",
    "plot_file",
    "error",
]


@dataclass(frozen=True)
class NmrAnalysisRun:
    run_dir: Path
    results_csv: Path
    plots_dir: Path
    manifest_json: Path


def create_analysis_run(
    output_dir: Path,
    run_name: str | None = None,
    csv_file: Path | None = None,
    plots_dir: Path | None = None,
) -> NmrAnalysisRun:
    """Create a clean output folder for one NMR processing pass."""
    run_label = run_name or datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = Path(output_dir) / _safe_name(run_label)
    resolved_csv = Path(csv_file) if csv_file else run_dir / "results.csv"
    resolved_plots = Path(plots_dir) if plots_dir else run_dir / "plots"
    manifest_json = run_dir / "manifest.json"

    run_dir.mkdir(parents=True, exist_ok=True)
    resolved_csv.parent.mkdir(parents=True, exist_ok=True)
    resolved_plots.mkdir(parents=True, exist_ok=True)
    return NmrAnalysisRun(
        run_dir=run_dir,
        results_csv=resolved_csv,
        plots_dir=resolved_plots,
        manifest_json=manifest_json,
    )


def peak_result_to_row(result: PeakResult, plot_file: Path | str | None = None) -> dict:
    """Convert a successful peak result into a CSV row."""
    return {
        "file": result.source.name,
        "source_path": str(result.source),
        "target_ppm": result.target_ppm,
        "peak_ppm": result.peak_ppm,
        "snr": result.snr,
        "prominence_snr": result.prominence_snr,
        "prominence": result.prominence,
        "width_ppm": result.width_ppm,
        "peaks_considered": result.peaks_considered,
        "peak_height": result.peak_height,
        "baseline": result.baseline,
        "noise": result.noise,
        "points_in_window": result.points_in_window,
        "plot_file": str(plot_file or ""),
        "error": "",
    }


def error_row(path: Path, error: Exception | str) -> dict:
    """Convert an analysis failure into a CSV row with traceable context."""
    row = {column: "" for column in RESULT_COLUMNS}
    row["file"] = Path(path).name
    row["source_path"] = str(path)
    row["error"] = str(error)
    return row


def write_results_csv(path: Path, rows: Iterable[dict]) -> None:
    """Write NMR peak-analysis rows to CSV."""
    csv_path = Path(path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_manifest(path: Path, payload: dict) -> None:
    """Write a JSON manifest documenting how the analysis run was produced."""
    manifest_path = Path(path)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _safe_name(value: str) -> str:
    cleaned = []
    for char in str(value).strip():
        cleaned.append(char if char.isalnum() or char in {"-", "_"} else "_")
    name = "".join(cleaned).strip("_")
    return name or datetime.now().strftime("%Y%m%d_%H%M%S")
