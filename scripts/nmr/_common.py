"""Shared helpers for standalone NMR analysis scripts.

This module centralises file discovery, metadata parsing, output-directory
creation, CSV/JSON serialisation, and common plotting logic so the three
CLI scripts remain thin wrappers.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import _bootstrap  # noqa: F401  — puts repo root on sys.path

from chemyx_lab.analysis.nmr import (
    NmrProcessingError,
    PeakResult,
    analyze_dx_peak,
    build_magnitude_spectrum,
    estimate_local_baseline,
    iter_dx_files,
    read_jcamp_fid,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_TARGET_PPM = 6.1
DEFAULT_WINDOW_PPM = 0.12
DEFAULT_OUTPUT_DIR = Path("results") / "analysis"
DEFAULT_PLATEAU_THRESHOLD = 5.0  # percent
DEFAULT_PLATEAU_CONSECUTIVE = 2

RESULT_COLUMNS = [
    "file",
    "source_path",
    "timestamp",
    "elapsed_seconds",
    "target_ppm",
    "peak_ppm",
    "peak_height",
    "raw_peak_height",
    "peak_area",
    "snr",
    "prominence_snr",
    "prominence",
    "width_ppm",
    "peaks_considered",
    "baseline",
    "noise",
    "points_in_window",
    "delta_height",
    "delta_area",
    "pct_change_height",
    "pct_change_area",
    "error",
]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class FileResult:
    """Wrapper around a single file's analysis outcome (success or failure)."""

    path: Path
    peak: PeakResult | None = None
    error: str | None = None
    timestamp: datetime | None = None
    timestamp_source: str = ""
    metadata: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PlateauResult:
    """Whether / when a metric reached plateau."""

    metric: str
    reached: bool
    plateau_index: int | None = None
    plateau_file: str | None = None
    consecutive_below: int = 0
    threshold_pct: float = 0.0


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------


def collect_dx_files(paths: Sequence[str | Path]) -> list[Path]:
    """Gather ``.dx`` files from one or more paths (files or directories).

    Returns a de-duplicated list sorted by resolved path.
    """
    seen: set[Path] = set()
    result: list[Path] = []
    for p in paths:
        p = Path(p)
        for f in iter_dx_files(p):
            resolved = f.resolve()
            if resolved not in seen:
                seen.add(resolved)
                result.append(f)
    return sorted(result, key=lambda f: f.resolve())


# ---------------------------------------------------------------------------
# Metadata / timestamp parsing
# ---------------------------------------------------------------------------

_LONG_DATE_RE = re.compile(
    r"(\d{4})/(\d{2})/(\d{2})\s+(\d{2}):(\d{2}):(\d{2})"
)

_SEQUENCE_RE = re.compile(r"sequence-(\d+)", re.IGNORECASE)


def parse_acquisition_timestamp(
    metadata: dict[str, str], filepath: Path | None = None
) -> tuple[datetime | None, str]:
    """Extract the acquisition timestamp from JCAMP-DX metadata.

    Returns ``(datetime_or_None, source_description)``.

    Tries ``##LONG DATE`` first, then the ``##$DATE`` epoch, then
    falls back to a sequence number embedded in the filename.
    """
    # 1. ##LONG DATE=2026/06/08 14:21:24-0400
    long_date = metadata.get("LONG DATE", "")
    m = _LONG_DATE_RE.search(long_date)
    if m:
        try:
            dt = datetime(
                int(m.group(1)),
                int(m.group(2)),
                int(m.group(3)),
                int(m.group(4)),
                int(m.group(5)),
                int(m.group(6)),
            )
            return dt, "LONG DATE header"
        except (ValueError, OverflowError):
            pass

    # 2. ##$DATE=<unix epoch>
    epoch_str = metadata.get("$DATE", "").strip()
    if epoch_str:
        try:
            dt = datetime.fromtimestamp(int(epoch_str))
            return dt, "$DATE epoch header"
        except (ValueError, OSError, OverflowError):
            pass

    # 3. Filename sequence number (e.g. "sequence-1415")
    if filepath is not None:
        seq_m = _SEQUENCE_RE.search(filepath.stem)
        if seq_m:
            seq_val = seq_m.group(1)
            # Interpret HHMM — e.g. 1415 → 14:15
            if len(seq_val) == 4:
                try:
                    h, mi = int(seq_val[:2]), int(seq_val[2:])
                    dt = datetime(2000, 1, 1, h, mi)  # synthetic date
                    return dt, f"filename sequence-{seq_val} (HHMM)"
                except ValueError:
                    pass
            # Plain integer fallback — use as ordinal seconds from epoch 0
            try:
                dt = datetime(2000, 1, 1) 
                # We can't build a real datetime, but we can use the int
                # for relative ordering.  Store as ordinal "second".
                from datetime import timedelta
                dt = datetime(2000, 1, 1) + timedelta(seconds=int(seq_val))
                return dt, f"filename sequence-{seq_val} (ordinal)"
            except (ValueError, OverflowError):
                pass

    return None, "none"


def extract_useful_metadata(metadata: dict[str, str]) -> dict[str, str]:
    """Return a human-friendly subset of JCAMP-DX metadata."""
    keys_of_interest = [
        "TITLE",
        "LONG DATE",
        ".OBSERVE FREQUENCY",
        ".OBSERVE NUCLEUS",
        ".SOLVENT NAME",
        "$SCANS",
        "TEMPERATURE",
        "PRESSURE",
        ".ACQUISITION TIME",
        "$SWEEP WIDTH",
        "$TOTAL DURATION",
    ]
    result: dict[str, str] = {}
    for k in keys_of_interest:
        if k in metadata:
            result[k] = metadata[k]
    return result


# ---------------------------------------------------------------------------
# Analysis wrappers (with per-file error handling)
# ---------------------------------------------------------------------------


def analyze_file(
    path: Path,
    target_ppm: float = DEFAULT_TARGET_PPM,
    window_ppm: float = DEFAULT_WINDOW_PPM,
    **kwargs: Any,
) -> FileResult:
    """Analyse one ``.dx`` file, catching errors and returning a FileResult."""
    fr = FileResult(path=path)
    # Read metadata for timestamp even if analysis fails
    try:
        fid = read_jcamp_fid(path)
        fr.metadata = extract_useful_metadata(fid.metadata)
        fr.timestamp, fr.timestamp_source = parse_acquisition_timestamp(
            fid.metadata, path
        )
    except Exception as exc:
        fr.warnings.append(f"metadata read failed: {exc}")

    # Run peak analysis
    try:
        fr.peak = analyze_dx_peak(
            path,
            target_ppm=target_ppm,
            window_ppm=window_ppm,
            **kwargs,
        )
    except NmrProcessingError as exc:
        fr.error = str(exc)
    except Exception as exc:
        fr.error = f"unexpected error: {exc}"

    return fr


def analyze_batch(
    files: Sequence[Path],
    target_ppm: float = DEFAULT_TARGET_PPM,
    window_ppm: float = DEFAULT_WINDOW_PPM,
    **kwargs: Any,
) -> list[FileResult]:
    """Analyse multiple files, never stopping on a single failure."""
    results: list[FileResult] = []
    for f in files:
        results.append(analyze_file(f, target_ppm, window_ppm, **kwargs))
    return results


# ---------------------------------------------------------------------------
# Time-series ordering
# ---------------------------------------------------------------------------


def sort_by_timestamp(results: list[FileResult]) -> list[FileResult]:
    """Sort results by parsed acquisition timestamp, filename fallback."""

    def _sort_key(fr: FileResult) -> tuple:
        if fr.timestamp is not None:
            return (0, fr.timestamp, fr.path.name)
        return (1, datetime.min, fr.path.name)

    return sorted(results, key=_sort_key)


# ---------------------------------------------------------------------------
# Growth & plateau detection
# ---------------------------------------------------------------------------


def compute_deltas(
    results: list[FileResult],
) -> list[dict[str, Any]]:
    """Build rows with delta / percent-change columns for a sorted series.

    Returns a list of dicts ready for CSV serialisation.
    """
    first_ts: datetime | None = None
    prev_height: float | None = None
    prev_area: float | None = None
    rows: list[dict[str, Any]] = []

    for fr in results:
        row: dict[str, Any] = {
            "file": fr.path.name,
            "source_path": str(fr.path),
            "timestamp": (
                fr.timestamp.isoformat(timespec="seconds")
                if fr.timestamp
                else ""
            ),
        }

        if fr.timestamp is not None and first_ts is None:
            first_ts = fr.timestamp

        if fr.timestamp is not None and first_ts is not None:
            row["elapsed_seconds"] = (fr.timestamp - first_ts).total_seconds()
        else:
            row["elapsed_seconds"] = ""

        if fr.error or fr.peak is None:
            # Fill metric columns as empty for failed files
            for col in RESULT_COLUMNS:
                row.setdefault(col, "")
            row["error"] = fr.error or "analysis failed"
            rows.append(row)
            continue

        peak = fr.peak
        row.update(
            {
                "target_ppm": peak.target_ppm,
                "peak_ppm": peak.peak_ppm,
                "peak_height": peak.peak_height,
                "raw_peak_height": peak.raw_peak_height,
                "peak_area": peak.peak_area,
                "snr": peak.snr,
                "prominence_snr": peak.prominence_snr,
                "prominence": peak.prominence,
                "width_ppm": peak.width_ppm,
                "peaks_considered": peak.peaks_considered,
                "baseline": peak.baseline,
                "noise": peak.noise,
                "points_in_window": peak.points_in_window,
                "error": "",
            }
        )

        # Deltas
        if prev_height is not None:
            delta_h = peak.peak_height - prev_height
            row["delta_height"] = delta_h
            row["pct_change_height"] = (
                (delta_h / prev_height * 100.0) if prev_height != 0 else 0.0
            )
        else:
            row["delta_height"] = ""
            row["pct_change_height"] = ""

        if prev_area is not None:
            delta_a = peak.peak_area - prev_area
            row["delta_area"] = delta_a
            row["pct_change_area"] = (
                (delta_a / prev_area * 100.0) if prev_area != 0 else 0.0
            )
        else:
            row["delta_area"] = ""
            row["pct_change_area"] = ""

        prev_height = peak.peak_height
        prev_area = peak.peak_area
        rows.append(row)

    return rows


def detect_plateau(
    rows: list[dict[str, Any]],
    metric_key: str,
    pct_key: str,
    threshold_pct: float = DEFAULT_PLATEAU_THRESHOLD,
    min_consecutive: int = DEFAULT_PLATEAU_CONSECUTIVE,
) -> PlateauResult:
    """Detect plateau: at least *min_consecutive* changes below threshold.

    Only considers rows that have valid numeric percent-change values.
    """
    consecutive = 0
    first_plateau_idx: int | None = None
    first_plateau_file: str | None = None

    for i, row in enumerate(rows):
        pct = row.get(pct_key, "")
        if pct == "" or pct is None:
            consecutive = 0
            continue
        try:
            pct_val = float(pct)
        except (TypeError, ValueError):
            consecutive = 0
            continue

        if abs(pct_val) < threshold_pct:
            consecutive += 1
            if consecutive == 1:
                first_plateau_idx = i
                first_plateau_file = row.get("file", "")
            if consecutive >= min_consecutive:
                return PlateauResult(
                    metric=metric_key,
                    reached=True,
                    plateau_index=first_plateau_idx,
                    plateau_file=first_plateau_file,
                    consecutive_below=consecutive,
                    threshold_pct=threshold_pct,
                )
        else:
            consecutive = 0

    return PlateauResult(
        metric=metric_key,
        reached=False,
        consecutive_below=consecutive,
        threshold_pct=threshold_pct,
    )


# ---------------------------------------------------------------------------
# Output directory management
# ---------------------------------------------------------------------------


def create_output_dir(
    base_dir: Path,
    suffix: str,
    run_name: str | None = None,
) -> Path:
    """Create a timestamped output directory, handling collisions.

    Returns e.g.  ``base_dir/20260723_201500_timeseries/``
    If the directory already exists, appends ``_2``, ``_3``, etc.
    """
    label = run_name or datetime.now().strftime("%Y%m%d_%H%M%S") + f"_{suffix}"
    out = Path(base_dir) / _safe_name(label)
    if out.exists():
        idx = 2
        while True:
            candidate = Path(base_dir) / f"{_safe_name(label)}_{idx}"
            if not candidate.exists():
                out = candidate
                break
            idx += 1
    out.mkdir(parents=True, exist_ok=True)
    return out


def _safe_name(value: str) -> str:
    cleaned = []
    for char in str(value).strip():
        cleaned.append(char if char.isalnum() or char in {"-", "_"} else "_")
    name = "".join(cleaned).strip("_")
    return name or datetime.now().strftime("%Y%m%d_%H%M%S")


def derive_group(
    names: Sequence[str | Path],
    tokens: Sequence[str],
    explicit: str | None = None,
) -> str | None:
    """Pick an output sub-folder (e.g. sample type) for a set of inputs.

    If *explicit* is given it wins. Otherwise the first token in *tokens* that
    appears (case-insensitively) in any of the input *names* is used, so files
    like ``CEC-PhSi2-flow(...).dx`` land under a ``PhSi2`` group. Returns
    ``None`` when nothing matches, meaning "no grouping".
    """
    if explicit:
        return _safe_name(str(explicit))
    if not tokens:
        return None
    haystack = " ".join(str(n) for n in names).lower()
    for token in tokens:
        if str(token).lower() in haystack:
            return _safe_name(str(token))
    return None


def derive_run_name(paths: Sequence[str | Path], suffix: str) -> str:
    """Build a default output-folder name that identifies the input data.

    So an output folder can always be traced back to the data it came from.
    e.g. input ``results/raw/nmr/06-08-26/`` with suffix ``timeseries`` →
    ``06-08-26_20260727_143022_timeseries``.

    A directory contributes its own name; a single file contributes its stem.
    The timestamp keeps repeated runs on the same input from colliding.
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = ""
    if paths:
        first = Path(paths[0])
        if first.is_dir():
            base = first.name
        elif first.is_file():
            base = first.stem
        else:  # path may not exist yet (e.g. in tests) — use a best guess
            base = first.stem or first.name
    base = _safe_name(base) if base else suffix
    return f"{base}_{stamp}_{suffix}"


# ---------------------------------------------------------------------------
# CSV / JSON serialisation
# ---------------------------------------------------------------------------


def write_csv(
    path: Path,
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
) -> Path:
    """Write a list of dicts to CSV.  Returns the path written."""
    csv_path = Path(path)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    cols = columns or RESULT_COLUMNS
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def write_summary(
    path: Path,
    payload: dict[str, Any],
) -> Path:
    """Write a JSON summary / manifest.  Returns the path written."""
    json_path = Path(path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(payload, indent=2, default=str, sort_keys=True),
        encoding="utf-8",
    )
    return json_path


def build_summary_payload(
    *,
    script_name: str,
    input_paths: list[str],
    results: list[FileResult],
    parameters: dict[str, Any],
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the standard summary.json payload."""
    successes = [r for r in results if r.peak is not None]
    failures = [r for r in results if r.error is not None]
    all_warnings: list[dict[str, str]] = []
    for r in results:
        for w in r.warnings:
            all_warnings.append({"file": r.path.name, "warning": w})

    timestamp_sources: dict[str, str] = {}
    for r in results:
        timestamp_sources[r.path.name] = r.timestamp_source

    payload: dict[str, Any] = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "script": script_name,
        "input_paths": input_paths,
        "files_found": len(results),
        "files_succeeded": len(successes),
        "files_failed": len(failures),
        "failures": [
            {"file": r.path.name, "error": r.error} for r in failures
        ],
        "warnings": all_warnings,
        "timestamp_sources": timestamp_sources,
        "parameters": parameters,
    }
    if extra:
        payload.update(extra)
    return payload


# ---------------------------------------------------------------------------
# Plotting helpers
# ---------------------------------------------------------------------------


def _get_plt():
    """Lazy-import matplotlib with Agg backend."""
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import pyplot as plt
    return plt


def _get_np():
    import numpy as np
    return np


def apply_plot_style(ax, title: str, xlabel: str, ylabel: str) -> None:
    """Apply consistent styling to a matplotlib Axes."""
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)


def plot_metric_over_time(
    labels: list[str],
    values: list[float],
    *,
    ylabel: str,
    title: str,
    color: str = "#1f77b4",
    output_path: Path,
    elapsed_seconds: list[float] | None = None,
) -> Path:
    """Line plot of a single metric across ordered files.

    If *elapsed_seconds* is provided, uses elapsed time as x-axis;
    otherwise uses file labels.
    """
    plt = _get_plt()
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=140)

    if elapsed_seconds and len(elapsed_seconds) == len(values):
        # Use elapsed hours for readability
        x = [s / 3600.0 for s in elapsed_seconds]
        ax.plot(x, values, "o-", color=color, linewidth=1.6, markersize=5)
        ax.set_xlabel("Elapsed time (hours)", fontsize=10)
    else:
        x = list(range(len(values)))
        ax.plot(x, values, "o-", color=color, linewidth=1.6, markersize=5)
        ax.set_xticks(x)
        ax.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)

    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_growth_rate(
    labels: list[str],
    delta_height: list[float | None],
    delta_area: list[float | None],
    *,
    title: str = "Growth Rate per Iteration",
    output_path: Path,
) -> Path:
    """Bar chart of delta intensity and delta area per iteration."""
    plt = _get_plt()
    np = _get_np()

    n = len(labels)
    x = np.arange(n)
    width = 0.35

    dh = [v if v is not None else 0.0 for v in delta_height]
    da = [v if v is not None else 0.0 for v in delta_area]

    fig, ax1 = plt.subplots(figsize=(10, 5.5), dpi=140)
    ax1.bar(x - width / 2, dh, width, label="Δ Height", color="#2196F3", alpha=0.8)
    ax1.set_ylabel("Δ Peak Height", fontsize=10, color="#2196F3")
    ax1.tick_params(axis="y", labelcolor="#2196F3")

    ax2 = ax1.twinx()
    ax2.bar(x + width / 2, da, width, label="Δ Area", color="#FF9800", alpha=0.8)
    ax2.set_ylabel("Δ Peak Area", fontsize=10, color="#FF9800")
    ax2.tick_params(axis="y", labelcolor="#FF9800")

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax1.set_title(title, fontsize=11, fontweight="bold")
    ax1.grid(True, alpha=0.15, axis="y")

    # Combined legend
    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=8)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_combined_overlay(
    labels: list[str],
    heights: list[float],
    areas: list[float],
    *,
    title: str = "Peak Height & Area Over Time",
    output_path: Path,
    elapsed_seconds: list[float] | None = None,
) -> Path:
    """Dual y-axis overlay of height and area."""
    plt = _get_plt()

    fig, ax1 = plt.subplots(figsize=(10, 5.5), dpi=140)

    if elapsed_seconds and len(elapsed_seconds) == len(heights):
        x = [s / 3600.0 for s in elapsed_seconds]
        x_label = "Elapsed time (hours)"
    else:
        x = list(range(len(heights)))
        x_label = "Iteration"

    color1 = "#1f77b4"
    ax1.plot(x, heights, "o-", color=color1, linewidth=1.6, markersize=5, label="Peak Height")
    ax1.set_xlabel(x_label, fontsize=10)
    ax1.set_ylabel("Peak Height (intensity)", fontsize=10, color=color1)
    ax1.tick_params(axis="y", labelcolor=color1)

    ax2 = ax1.twinx()
    color2 = "#d62728"
    ax2.plot(x, areas, "s--", color=color2, linewidth=1.6, markersize=5, label="Peak Area")
    ax2.set_ylabel("Peak Area (integrated)", fontsize=10, color=color2)
    ax2.tick_params(axis="y", labelcolor=color2)

    ax1.set_title(title, fontsize=11, fontweight="bold")
    ax1.grid(True, alpha=0.25)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="best", fontsize=8)

    if not (elapsed_seconds and len(elapsed_seconds) == len(heights)):
        ax1.set_xticks(x)
        ax1.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_bar_comparison(
    labels: list[str],
    heights: list[float],
    areas: list[float],
    *,
    title: str = "Peak Height & Area by File",
    output_path: Path,
) -> Path:
    """Side-by-side bar chart for batch comparison."""
    plt = _get_plt()
    np = _get_np()

    n = len(labels)
    x = np.arange(n)
    width = 0.35

    fig, ax1 = plt.subplots(figsize=(10, 5.5), dpi=140)
    ax1.bar(x - width / 2, heights, width, label="Peak Height", color="#2196F3", alpha=0.8)
    ax1.set_ylabel("Peak Height", fontsize=10, color="#2196F3")
    ax1.tick_params(axis="y", labelcolor="#2196F3")

    ax2 = ax1.twinx()
    ax2.bar(x + width / 2, areas, width, label="Peak Area", color="#FF9800", alpha=0.8)
    ax2.set_ylabel("Peak Area", fontsize=10, color="#FF9800")
    ax2.tick_params(axis="y", labelcolor="#FF9800")

    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=45, ha="right", fontsize=7)
    ax1.set_title(title, fontsize=11, fontweight="bold")
    ax1.grid(True, alpha=0.15, axis="y")

    lines1, l1 = ax1.get_legend_handles_labels()
    lines2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, l1 + l2, loc="upper left", fontsize=8)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_scatter_correlation(
    heights: list[float],
    areas: list[float],
    labels: list[str],
    *,
    title: str = "Peak Height vs. Area",
    output_path: Path,
) -> Path:
    """Scatter plot of height vs area with file labels."""
    plt = _get_plt()
    np = _get_np()

    fig, ax = plt.subplots(figsize=(8, 6), dpi=140)
    ax.scatter(heights, areas, color="#1f77b4", s=50, zorder=3)

    for i, label in enumerate(labels):
        ax.annotate(
            label,
            (heights[i], areas[i]),
            textcoords="offset points",
            xytext=(5, 5),
            fontsize=6,
            alpha=0.8,
        )

    # Trend line
    if len(heights) >= 2:
        z = np.polyfit(heights, areas, 1)
        p = np.poly1d(z)
        x_line = np.linspace(min(heights), max(heights), 100)
        ax.plot(x_line, p(x_line), "--", color="#aaa", linewidth=1, label="trend")

    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_xlabel("Peak Height (intensity)", fontsize=10)
    ax.set_ylabel("Peak Area (integrated)", fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="best", fontsize=8)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_peak_review(
    path: Path,
    result: PeakResult,
    output_dir: Path,
    target_ppm: float = DEFAULT_TARGET_PPM,
    window_ppm: float = DEFAULT_WINDOW_PPM,
    plot_window_ppm: float = 0.5,
    line_broadening_hz: float | None = None,
    zero_fill_points: int | None = None,
) -> Path:
    """Generate a detailed review plot for a single file's peak."""
    from chemyx_lab.analysis.nmr import plot_peak_region

    return plot_peak_region(
        path,
        result,
        output_dir,
        target_ppm=target_ppm,
        detection_window_ppm=window_ppm,
        plot_window_ppm=plot_window_ppm,
        line_broadening_hz=line_broadening_hz,
        zero_fill_points=zero_fill_points,
    )


# ---------------------------------------------------------------------------
# Full-spectrum plotting (intensity vs ppm)
# ---------------------------------------------------------------------------


def load_spectrum(
    path: Path,
    *,
    line_broadening_hz: float | None = None,
    zero_fill_points: int | None = None,
):
    """Return ``(ppm_axis, magnitude)`` numpy arrays for a ``.dx`` file."""
    spec = build_magnitude_spectrum(
        path,
        line_broadening_hz=line_broadening_hz,
        zero_fill_points=zero_fill_points,
    )
    return spec.ppm_axis, spec.magnitude


def baseline_correct_spectrum(
    ppm,
    intensity,
    *,
    target_ppm: float,
    window_ppm: float,
    baseline_window_ppm: float,
    baseline_polynomial_order: int,
):
    """Return a locally baseline-corrected spectrum and noise estimate."""
    baseline, noise = estimate_local_baseline(
        ppm,
        intensity,
        target_ppm=target_ppm,
        detection_window_ppm=window_ppm,
        baseline_window_ppm=baseline_window_ppm,
        polynomial_order=baseline_polynomial_order,
    )
    return intensity - baseline, noise


def _reversed_xlim(ax, ppm, target_ppm=None, zoom_ppm=None):
    """Apply the NMR convention of high ppm on the left (descending x-axis)."""
    np = _get_np()
    if target_ppm is not None and zoom_ppm is not None:
        ax.set_xlim(float(target_ppm) + float(zoom_ppm), float(target_ppm) - float(zoom_ppm))
    else:
        ax.set_xlim(float(np.max(ppm)), float(np.min(ppm)))


def _apply_range_ylim(ax, series, lo, hi, pad_frac=0.08):
    """Scale the y-axis to the data whose ppm falls in ``[lo, hi]``.

    Without this, matplotlib autoscales y to the whole spectrum, so a zoomed-in
    peak that is small relative to the global maximum looks flat. *series* is a
    list of ``(ppm_axis, y_values)`` for every trace actually plotted.
    """
    np = _get_np()
    lo, hi = float(min(lo, hi)), float(max(lo, hi))
    chunks = []
    for ppm, y in series:
        mask = (ppm >= lo) & (ppm <= hi)
        if np.any(mask):
            chunks.append(np.asarray(y)[mask])
    if not chunks:
        return
    values = np.concatenate(chunks)
    ymin = float(np.min(values))
    ymax = float(np.max(values))
    span = ymax - ymin
    pad = span * pad_frac if span > 0 else (abs(ymax) * 0.05 or 1.0)
    ax.set_ylim(ymin - pad, ymax + pad)


def _apply_window_ylim(ax, series, target_ppm, zoom_ppm, pad_frac=0.08):
    """Scale the y-axis to ``target_ppm ± zoom_ppm`` (see _apply_range_ylim)."""
    _apply_range_ylim(
        ax, series,
        float(target_ppm) - float(zoom_ppm),
        float(target_ppm) + float(zoom_ppm),
        pad_frac,
    )


def plot_spectrum_single(
    ppm,
    magnitude,
    *,
    label: str,
    output_path: Path,
    target_ppm: float | None = None,
    window_ppm: float | None = None,
    peak_ppm: float | None = None,
    zoom_ppm: float | None = None,
    range_ppm: tuple[float, float] | None = None,
    title: str | None = None,
    ylabel: str = "Magnitude (intensity)",
) -> Path:
    """Plot one spectrum (intensity vs ppm), marking the target/detected peak.

    X-axis limits, in priority order:
      * *range_ppm* ``(lo, hi)`` — an explicit ppm window (e.g. 5–7 ppm), or
      * *zoom_ppm* — limits to ``target_ppm ± zoom_ppm``, or
      * neither — the full spectrum.
    In the zoomed/ranged cases the y-axis is rescaled to the visible data.
    """
    plt = _get_plt()
    fig, ax = plt.subplots(figsize=(10, 5.5), dpi=140)
    ax.plot(ppm, magnitude, color="#1f77b4", linewidth=0.9)

    if target_ppm is not None and window_ppm is not None:
        ax.axvspan(
            float(target_ppm) - float(window_ppm),
            float(target_ppm) + float(window_ppm),
            color="#f2c94c", alpha=0.22, label="detection window",
        )
        ax.axvline(float(target_ppm), color="#555555", linestyle="--",
                   linewidth=1.0, label=f"target {float(target_ppm):.3g} ppm")
    if peak_ppm is not None:
        ax.axvline(float(peak_ppm), color="#d62728", linewidth=1.3,
                   label=f"detected {float(peak_ppm):.3f} ppm")

    ax.set_xlabel("Chemical shift (ppm)", fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.set_title(title or label, fontsize=11, fontweight="bold")
    ax.grid(True, alpha=0.25)
    if range_ppm is not None:
        lo, hi = float(min(range_ppm)), float(max(range_ppm))
        ax.set_xlim(hi, lo)  # NMR convention: high ppm on the left
        _apply_range_ylim(ax, [(ppm, magnitude)], lo, hi)
    elif zoom_ppm is not None and target_ppm is not None:
        _reversed_xlim(ax, ppm, target_ppm, zoom_ppm)
        _apply_window_ylim(ax, [(ppm, magnitude)], target_ppm, zoom_ppm)
    else:
        _reversed_xlim(ax, ppm)
    ax.legend(loc="best", fontsize=8)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_spectra_overlay(
    spectra: list[tuple[str, Any, Any]],
    *,
    output_path: Path,
    target_ppm: float | None = None,
    window_ppm: float | None = None,
    zoom_ppm: float | None = None,
    normalize: bool = False,
    title: str = "Overlaid spectra",
) -> Path:
    """Overlay multiple spectra on one axis.

    *spectra* is a list of ``(label, ppm_axis, magnitude)`` tuples. Colours run
    along the ``viridis`` map so acquisition order is visible.
    """
    plt = _get_plt()
    np = _get_np()
    fig, ax = plt.subplots(figsize=(11, 6), dpi=140)
    cmap = plt.get_cmap("viridis")
    n = len(spectra)

    plotted: list[tuple[Any, Any]] = []
    for i, (label, ppm, mag) in enumerate(spectra):
        y = mag
        if normalize:
            peak = float(np.max(np.abs(mag))) or 1.0
            y = mag / peak
        color = cmap(i / max(1, n - 1))
        ax.plot(ppm, y, linewidth=0.9, color=color, alpha=0.85, label=label)
        plotted.append((ppm, y))

    if target_ppm is not None and window_ppm is not None:
        ax.axvspan(
            float(target_ppm) - float(window_ppm),
            float(target_ppm) + float(window_ppm),
            color="#f2c94c", alpha=0.15,
        )
        ax.axvline(float(target_ppm), color="#555555", linestyle="--", linewidth=1.0)

    ax.set_xlabel("Chemical shift (ppm)", fontsize=10)
    ax.set_ylabel("Normalized magnitude" if normalize else "Magnitude (intensity)", fontsize=10)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.grid(True, alpha=0.25)

    all_ppm = np.concatenate([s[1] for s in spectra]) if spectra else np.array([0.0, 1.0])
    _reversed_xlim(ax, all_ppm, target_ppm if zoom_ppm else None, zoom_ppm)
    if zoom_ppm is not None and target_ppm is not None:
        _apply_window_ylim(ax, plotted, target_ppm, zoom_ppm)
    if 0 < n <= 12:
        ax.legend(loc="best", fontsize=7, ncol=2)

    fig.tight_layout()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path)
    plt.close(fig)
    return output_path


def plot_spectra_stacked(
    spectra: list[tuple[str, Any, Any]],
    *,
    output_path: Path,
    target_ppm: float | None = None,
    window_ppm: float | None = None,
    zoom_ppm: float | None = None,
    offset_frac: float = 0.6,
    title: str = "Stacked spectra (waterfall)",
) -> Path:
    """Waterfall plot: each spectrum normalized and vertically offset.

    Makes it easy to see how the target peak evolves file-to-file without
    traces overlapping. Bottom trace is the first (earliest) file.
    """
    plt = _get_plt()
    np = _get_np()
    fig, ax = plt.subplots(figsize=(11, 7), dpi=140)
    cmap = plt.get_cmap("viridis")
    n = len(spectra)

    # When zoomed, normalize each trace to its in-window max (scaled to ~90% of
    # the offset gap) so the target peak fills its band; otherwise normalize to
    # the whole-spectrum max.
    zoom_lo = zoom_hi = None
    trace_scale = 1.0
    if zoom_ppm is not None and target_ppm is not None:
        zoom_lo = float(target_ppm) - float(zoom_ppm)
        zoom_hi = float(target_ppm) + float(zoom_ppm)
        trace_scale = float(offset_frac) * 0.9

    plotted: list[tuple[Any, Any]] = []
    for i, (label, ppm, mag) in enumerate(spectra):
        if zoom_lo is not None:
            mask = (ppm >= zoom_lo) & (ppm <= zoom_hi)
            denom = float(np.max(np.abs(mag[mask]))) if np.any(mask) else 0.0
            denom = denom or (float(np.max(np.abs(mag))) or 1.0)
            y = mag / denom * trace_scale + i * float(offset_frac)
        else:
            peak = float(np.max(np.abs(mag))) or 1.0
            y = mag / peak + i * float(offset_frac)
        color = cmap(i / max(1, n - 1))
        ax.plot(ppm, y, linewidth=0.9, color=color)
        plotted.append((ppm, y))
        # Label each trace at the left (high-ppm) edge of the *visible* range,
        # so labels stay inside the axes even when zoomed.
        label_x = zoom_hi if zoom_hi is not None else float(np.max(ppm))
        ax.text(
            label_x, i * float(offset_frac), f" {label}",
            fontsize=6, va="bottom", ha="left", color=color,
        )

    if target_ppm is not None:
        ax.axvline(float(target_ppm), color="#555555", linestyle="--", linewidth=1.0,
                   label=f"target {float(target_ppm):.3g} ppm")
        if window_ppm is not None:
            ax.axvspan(
                float(target_ppm) - float(window_ppm),
                float(target_ppm) + float(window_ppm),
                color="#f2c94c", alpha=0.15,
            )

    ax.set_xlabel("Chemical shift (ppm)", fontsize=10)
    ax.set_ylabel("Normalized magnitude (offset per file)", fontsize=10)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.grid(True, alpha=0.2, axis="x")

    all_ppm = np.concatenate([s[1] for s in spectra]) if spectra else np.array([0.0, 1.0])
    _reversed_xlim(ax, all_ppm, target_ppm if zoom_ppm else None, zoom_ppm)
    if zoom_ppm is not None and target_ppm is not None:
        _apply_window_ylim(ax, plotted, target_ppm, zoom_ppm)
    if target_ppm is not None:
        ax.legend(loc="upper right", fontsize=8)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Edge trace labels can overflow the axes; bbox_inches="tight" handles that
    # cleanly without matplotlib's tight_layout warning.
    fig.savefig(output_path, bbox_inches="tight")
    plt.close(fig)
    return output_path
