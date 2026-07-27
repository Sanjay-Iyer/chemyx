"""Batch NMR peak report for multiple .dx files.

Process multiple .dx files (not necessarily a time series) and generate a
comprehensive report with per-file metrics, aggregate statistics, and
comparison plots.

Usage::

    python scripts/nmr/batch_report.py results/raw/nmr/06-08-26/
    python scripts/nmr/batch_report.py file1.dx file2.dx file3.dx
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    RESULT_COLUMNS,
    analyze_batch,
    build_summary_payload,
    collect_dx_files,
    create_output_dir,
    derive_run_name,
    plot_bar_comparison,
    plot_scatter_correlation,
    write_csv,
    write_summary,
)
from _config import (
    add_config_argument,
    apply_cli_overrides,
    load_config,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Batch NMR analysis report for multiple .dx files. "
                    "Parameters come from configs/nmr/analysis.yaml unless "
                    "overridden by the flags below."
    )
    parser.add_argument(
        "paths", nargs="+",
        help="one or more .dx files or directories containing .dx files",
    )
    add_config_argument(parser)
    # Flags default to None so an unset flag falls back to the config file.
    parser.add_argument("--target", dest="target_ppm", type=float, default=None,
                        help="target peak position in ppm (overrides config)")
    parser.add_argument("--window", dest="window_ppm", type=float, default=None,
                        help="detection half-window in ppm (overrides config)")
    parser.add_argument("--output-dir", dest="output_dir", type=Path, default=None,
                        help="base directory for analysis output (overrides config)")
    parser.add_argument("--run-name", dest="run_name", default=None,
                        help="custom folder name (default: auto-timestamp)")
    parser.add_argument("--plots", dest="plots", action=argparse.BooleanOptionalAction,
                        default=None, help="generate plots (overrides config)")
    return parser


def _stats(values: list[float]) -> dict[str, float]:
    """Compute basic statistics for a list of floats."""
    if not values:
        return {"mean": 0.0, "std": 0.0, "min": 0.0, "max": 0.0, "count": 0}
    import numpy as np
    arr = np.array(values)
    return {
        "count": len(values),
        "mean": float(np.mean(arr)),
        "std": float(np.std(arr)),
        "min": float(np.min(arr)),
        "max": float(np.max(arr)),
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = apply_cli_overrides(load_config("batch_report", args.config), args)

    # Collect files
    files = collect_dx_files(args.paths)
    if not files:
        print("ERROR: no .dx files found in the given paths.", file=sys.stderr)
        return 1

    print(f"Found {len(files)} .dx file(s)")

    # Analyse all (per-file error handling)
    results = analyze_batch(files, target_ppm=cfg.target_ppm, window_ppm=cfg.window_ppm)

    # Create output dir (named after the input data when not overridden)
    out_dir = create_output_dir(
        cfg.output_dir, "batch",
        run_name=cfg.run_name or derive_run_name(args.paths, "batch"),
    )
    plots_dir = out_dir / "plots"

    # Build rows for CSV
    rows: list[dict] = []
    ok_labels: list[str] = []
    ok_heights: list[float] = []
    ok_areas: list[float] = []
    successes = 0
    failures = 0

    for fr in results:
        if fr.error or fr.peak is None:
            failures += 1
            row = {col: "" for col in RESULT_COLUMNS}
            row["file"] = fr.path.name
            row["source_path"] = str(fr.path)
            row["error"] = fr.error or "analysis failed"
            rows.append(row)
            continue

        successes += 1
        peak = fr.peak
        row = {
            "file": fr.path.name,
            "source_path": str(fr.path),
            "timestamp": fr.timestamp.isoformat() if fr.timestamp else "",
            "elapsed_seconds": "",
            "target_ppm": peak.target_ppm,
            "peak_ppm": peak.peak_ppm,
            "peak_height": peak.peak_height,
            "peak_area": peak.peak_area,
            "snr": peak.snr,
            "prominence_snr": peak.prominence_snr,
            "prominence": peak.prominence,
            "width_ppm": peak.width_ppm,
            "peaks_considered": peak.peaks_considered,
            "baseline": peak.baseline,
            "noise": peak.noise,
            "points_in_window": peak.points_in_window,
            "delta_height": "",
            "delta_area": "",
            "pct_change_height": "",
            "pct_change_area": "",
            "error": "",
        }
        rows.append(row)
        ok_labels.append(fr.path.name)
        ok_heights.append(peak.peak_height)
        ok_areas.append(peak.peak_area)

    # Print table
    print()
    print(f"{'File':<55s} {'Peak ppm':>10s} {'Height':>14s} {'Area':>14s} {'SNR':>10s}")
    print("-" * 100)
    for row in rows:
        if row.get("error"):
            print(f"{row['file']:<55s}  ** ERROR: {row['error']}")
            continue
        print(
            f"{row['file']:<55s} {float(row['peak_ppm']):>10.4f}"
            f" {float(row['peak_height']):>14.6g}"
            f" {float(row['peak_area']):>14.6g}"
            f" {float(row['snr']):>10.2f}"
        )

    # Statistics
    height_stats = _stats(ok_heights)
    area_stats = _stats(ok_areas)

    print()
    print("  --- Statistics ---")
    print(f"    Height  : mean={height_stats['mean']:.4g}, "
          f"std={height_stats['std']:.4g}, "
          f"min={height_stats['min']:.4g}, max={height_stats['max']:.4g}")
    print(f"    Area    : mean={area_stats['mean']:.4g}, "
          f"std={area_stats['std']:.4g}, "
          f"min={area_stats['min']:.4g}, max={area_stats['max']:.4g}")

    # Rankings
    if ok_labels:
        ranked_h = sorted(zip(ok_labels, ok_heights), key=lambda t: t[1], reverse=True)
        ranked_a = sorted(zip(ok_labels, ok_areas), key=lambda t: t[1], reverse=True)
        print()
        print("  --- Rankings (by Height) ---")
        for i, (name, val) in enumerate(ranked_h, 1):
            print(f"    {i}. {name} - {val:.6g}")
        print()
        print("  --- Rankings (by Area) ---")
        for i, (name, val) in enumerate(ranked_a, 1):
            print(f"    {i}. {name} - {val:.6g}")

    # Plots
    plot_files: list[str] = []
    if cfg.plots and len(ok_labels) >= 2:
        try:
            p = plot_bar_comparison(
                ok_labels, ok_heights, ok_areas,
                output_path=plots_dir / "intensity_area_bars.png",
            )
            plot_files.append(str(p))
        except Exception as exc:
            print(f"  WARNING: bar plot failed: {exc}")

        try:
            p = plot_scatter_correlation(
                ok_heights, ok_areas, ok_labels,
                output_path=plots_dir / "intensity_vs_area.png",
            )
            plot_files.append(str(p))
        except Exception as exc:
            print(f"  WARNING: scatter plot failed: {exc}")

    # Save CSV
    csv_path = write_csv(out_dir / "results.csv", rows, columns=RESULT_COLUMNS)

    # Save summary
    summary = build_summary_payload(
        script_name="batch_report.py",
        input_paths=[str(p) for p in args.paths],
        results=results,
        parameters={
            "target_ppm": cfg.target_ppm,
            "window_ppm": cfg.window_ppm,
            "plots_enabled": cfg.plots,
        },
        extra={
            "statistics": {
                "height": height_stats,
                "area": area_stats,
            },
            "plot_files": plot_files,
        },
    )
    summary_path = write_summary(out_dir / "summary.json", summary)

    print()
    print(f"  Results CSV  : {csv_path}")
    print(f"  Summary JSON : {summary_path}")
    if plot_files:
        print(f"  Plots        : {plots_dir}")
    print(f"  Output dir   : {out_dir}")
    print(f"  ({successes} succeeded, {failures} failed)")
    return 1 if failures > 0 and successes == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
