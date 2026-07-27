"""Time-series NMR peak comparison.

Compare multiple .dx files as a time series, tracking how the peak near
~6.1 ppm grows over iterations.  Computes intensity/area deltas, percent
changes, and detects plateau for both metrics independently.

Usage::

    # All .dx files in a directory
    python scripts/nmr/compare_timeseries.py results/raw/nmr/06-08-26/

    # Specific files
    python scripts/nmr/compare_timeseries.py file1.dx file2.dx file3.dx

    # Custom parameters
    python scripts/nmr/compare_timeseries.py dir/ --target 6.1 --plateau-threshold 3
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    RESULT_COLUMNS,
    PlateauResult,
    analyze_batch,
    build_summary_payload,
    collect_dx_files,
    compute_deltas,
    create_output_dir,
    derive_group,
    derive_run_name,
    detect_plateau,
    plot_combined_overlay,
    plot_growth_rate,
    plot_metric_over_time,
    sort_by_timestamp,
    write_csv,
    write_summary,
)
from _config import (
    add_config_argument,
    apply_cli_overrides,
    load_config,
    peak_analysis_kwargs,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Compare NMR .dx files as a time series. Parameters come "
                    "from configs/nmr/analysis.yaml unless overridden by the "
                    "flags below."
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
    parser.add_argument("--group", dest="group", default=None,
                        help="output sub-folder, e.g. sample type (overrides config)")
    parser.add_argument("--plateau-threshold", dest="plateau_threshold_pct", type=float,
                        default=None,
                        help="percent-change threshold for plateau (overrides config)")
    parser.add_argument("--plateau-consecutive", dest="plateau_consecutive", type=int,
                        default=None,
                        help="consecutive iterations below threshold to declare plateau (overrides config)")
    parser.add_argument("--plots", dest="plots", action=argparse.BooleanOptionalAction,
                        default=None, help="generate plots (overrides config)")
    return parser


def _plateau_dict(pr: PlateauResult) -> dict:
    return {
        "metric": pr.metric,
        "reached": pr.reached,
        "plateau_index": pr.plateau_index,
        "plateau_file": pr.plateau_file,
        "consecutive_below_threshold": pr.consecutive_below,
        "threshold_pct": pr.threshold_pct,
    }


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = apply_cli_overrides(load_config("compare_timeseries", args.config), args)

    # Collect files
    files = collect_dx_files(args.paths)
    if not files:
        print("ERROR: no .dx files found in the given paths.", file=sys.stderr)
        return 1

    print(f"Found {len(files)} .dx file(s)")

    # Analyse all (errors handled per-file)
    results = analyze_batch(
        files,
        target_ppm=cfg.target_ppm,
        window_ppm=cfg.window_ppm,
        **peak_analysis_kwargs(cfg),
    )

    # Sort by timestamp
    results = sort_by_timestamp(results)

    # Compute deltas
    rows = compute_deltas(results)

    # Plateau detection (height and area separately)
    height_plateau = detect_plateau(
        rows, "peak_height", "pct_change_height",
        threshold_pct=cfg.plateau_threshold_pct,
        min_consecutive=cfg.plateau_consecutive,
    )
    area_plateau = detect_plateau(
        rows, "peak_area", "pct_change_area",
        threshold_pct=cfg.plateau_threshold_pct,
        min_consecutive=cfg.plateau_consecutive,
    )

    # Create output dir: <output_dir>/[group/]<input>_<timestamp>_timeseries
    group = derive_group([f.name for f in files], cfg.group_tokens, cfg.group)
    base_out = cfg.output_dir / group if group else cfg.output_dir
    out_dir = create_output_dir(
        base_out, "timeseries",
        run_name=cfg.run_name or derive_run_name(args.paths, "timeseries"),
    )
    plots_dir = out_dir / "plots"

    # Print summary table
    print()
    print(f"{'File':<55s} {'Peak ppm':>10s} {'Height':>14s} {'Area':>14s} "
          f"{'d.Height':>14s} {'d.Area':>14s} {'%d.Ht':>8s} {'%d.Ar':>8s}")
    print("-" * 140)
    successes = 0
    failures = 0
    for row in rows:
        if row.get("error"):
            failures += 1
            print(f"{row['file']:<55s}  ** ERROR: {row['error']}")
            continue
        successes += 1

        def _fmt(v, fmt_str=".6g"):
            if v == "" or v is None:
                return "-".rjust(14)
            return f"{float(v):{fmt_str}}".rjust(14)

        def _pct(v):
            if v == "" or v is None:
                return "-".rjust(8)
            return f"{float(v):.1f}%".rjust(8)

        print(
            f"{row['file']:<55s} {float(row['peak_ppm']):>10.4f}"
            f" {_fmt(row['peak_height'])}"
            f" {_fmt(row['peak_area'])}"
            f" {_fmt(row.get('delta_height'))}"
            f" {_fmt(row.get('delta_area'))}"
            f" {_pct(row.get('pct_change_height'))}"
            f" {_pct(row.get('pct_change_area'))}"
        )

    # Plateau status
    print()
    for label, plat in [("Height", height_plateau), ("Area", area_plateau)]:
        if plat.reached:
            print(f"  [Y] {label} PLATEAU reached at '{plat.plateau_file}' "
                  f"(index {plat.plateau_index}, "
                  f"{plat.consecutive_below} consecutive < {plat.threshold_pct}%)")
        else:
            print(f"  [N] {label} plateau NOT reached "
                  f"({plat.consecutive_below} consecutive < {plat.threshold_pct}%, "
                  f"need {cfg.plateau_consecutive})")

    # Generate plots
    plot_files: list[str] = []
    if cfg.plots:
        # Extract data for plots (only successful files)
        ok_labels: list[str] = []
        ok_heights: list[float] = []
        ok_areas: list[float] = []
        ok_elapsed: list[float] = []
        ok_delta_h: list[float | None] = []
        ok_delta_a: list[float | None] = []

        for row in rows:
            if row.get("error"):
                continue
            ok_labels.append(row["file"])
            ok_heights.append(float(row["peak_height"]))
            ok_areas.append(float(row["peak_area"]))
            elapsed = row.get("elapsed_seconds", "")
            ok_elapsed.append(float(elapsed) if elapsed != "" else 0.0)
            dh = row.get("delta_height")
            da = row.get("delta_area")
            ok_delta_h.append(float(dh) if dh != "" and dh is not None else None)
            ok_delta_a.append(float(da) if da != "" and da is not None else None)

        if len(ok_labels) >= 2:
            elapsed_valid = any(e > 0 for e in ok_elapsed)
            elapsed_arg = ok_elapsed if elapsed_valid else None

            try:
                p = plot_metric_over_time(
                    ok_labels, ok_heights,
                    ylabel="Peak Height (intensity)",
                    title="Peak Height Over Time (~6.1 ppm)",
                    color="#1f77b4",
                    output_path=plots_dir / "intensity_over_time.png",
                    elapsed_seconds=elapsed_arg,
                )
                plot_files.append(str(p))
            except Exception as exc:
                print(f"  WARNING: intensity plot failed: {exc}")

            try:
                p = plot_metric_over_time(
                    ok_labels, ok_areas,
                    ylabel="Peak Area (integrated)",
                    title="Peak Area Over Time (~6.1 ppm)",
                    color="#d62728",
                    output_path=plots_dir / "area_over_time.png",
                    elapsed_seconds=elapsed_arg,
                )
                plot_files.append(str(p))
            except Exception as exc:
                print(f"  WARNING: area plot failed: {exc}")

            try:
                p = plot_growth_rate(
                    ok_labels, ok_delta_h, ok_delta_a,
                    output_path=plots_dir / "growth_rate.png",
                )
                plot_files.append(str(p))
            except Exception as exc:
                print(f"  WARNING: growth rate plot failed: {exc}")

            try:
                p = plot_combined_overlay(
                    ok_labels, ok_heights, ok_areas,
                    output_path=plots_dir / "combined_overlay.png",
                    elapsed_seconds=elapsed_arg,
                )
                plot_files.append(str(p))
            except Exception as exc:
                print(f"  WARNING: combined overlay plot failed: {exc}")
        elif ok_labels:
            print("  (Skipping plots -- need at least 2 successful files)")

    # Save CSV
    csv_path = write_csv(out_dir / "results.csv", rows, columns=RESULT_COLUMNS)

    # Save summary
    summary = build_summary_payload(
        script_name="compare_timeseries.py",
        input_paths=[str(p) for p in args.paths],
        results=results,
        parameters={
            "target_ppm": cfg.target_ppm,
            "window_ppm": cfg.window_ppm,
            "plateau_threshold_pct": cfg.plateau_threshold_pct,
            "plateau_consecutive": cfg.plateau_consecutive,
            "plots_enabled": cfg.plots,
        },
        extra={
            "plateau_height": _plateau_dict(height_plateau),
            "plateau_area": _plateau_dict(area_plateau),
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
