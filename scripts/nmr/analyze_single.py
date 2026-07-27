"""Detailed single-file NMR peak analysis.

Analyses one .dx file, prints a comprehensive report with all peak metrics
(intensity, area, SNR, prominence, etc.) plus file metadata, generates a
review plot, and saves results to a timestamped directory.

Usage::

    python scripts/nmr/analyze_single.py <path-to-dx-file>
    python scripts/nmr/analyze_single.py <path> --target 6.1 --window 0.12
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    analyze_file,
    build_summary_payload,
    create_output_dir,
    derive_group,
    derive_run_name,
    plot_peak_review,
    write_csv,
    write_summary,
    RESULT_COLUMNS,
)
from _config import (
    add_config_argument,
    apply_cli_overrides,
    load_config,
    peak_analysis_kwargs,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detailed analysis of a single NMR .dx file. "
                    "Parameters come from configs/nmr/analysis.yaml unless "
                    "overridden by the flags below."
    )
    parser.add_argument("path", help="Path to a .dx file")
    add_config_argument(parser)
    # Flags default to None so an unset flag falls back to the config file.
    parser.add_argument("--target", dest="target_ppm", type=float, default=None,
                        help="target peak position in ppm (overrides config)")
    parser.add_argument("--window", dest="window_ppm", type=float, default=None,
                        help="detection half-window in ppm (overrides config)")
    parser.add_argument("--plot-window", dest="plot_window_ppm", type=float, default=None,
                        help="ppm half-window for review plot (overrides config)")
    parser.add_argument("--output-dir", dest="output_dir", type=Path, default=None,
                        help="base directory for analysis output (overrides config)")
    parser.add_argument("--run-name", dest="run_name", default=None,
                        help="custom folder name (default: timestamp)")
    parser.add_argument("--group", dest="group", default=None,
                        help="output sub-folder, e.g. sample type (overrides config)")
    parser.add_argument("--plots", dest="plots", action=argparse.BooleanOptionalAction,
                        default=None, help="generate review plot (overrides config)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = apply_cli_overrides(load_config("analyze_single", args.config), args)
    dx_path = Path(args.path)

    if not dx_path.is_file():
        print(f"ERROR: file not found: {dx_path}", file=sys.stderr)
        return 1

    # Analyse
    fr = analyze_file(
        dx_path,
        target_ppm=cfg.target_ppm,
        window_ppm=cfg.window_ppm,
        **peak_analysis_kwargs(cfg),
    )

    # Create output directory: <output_dir>/[group/]<input>_<timestamp>_single
    group = derive_group([dx_path.name], cfg.group_tokens, cfg.group)
    base_out = cfg.output_dir / group if group else cfg.output_dir
    out_dir = create_output_dir(
        base_out, "single",
        run_name=cfg.run_name or derive_run_name([dx_path], "single"),
    )
    plots_dir = out_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)

    # Print report
    print("=" * 70)
    print("  NMR Single-File Analysis Report")
    print("=" * 70)
    print(f"  File      : {dx_path.name}")
    print(f"  Full path : {dx_path.resolve()}")
    print()

    # Metadata section
    if fr.metadata:
        print("  --- Acquisition Metadata ---")
        for k, v in fr.metadata.items():
            print(f"    {k:.<30s} {v}")
        print()

    if fr.timestamp:
        print(f"  Timestamp : {fr.timestamp.isoformat()} ({fr.timestamp_source})")
    else:
        print("  Timestamp : not available")

    if fr.warnings:
        print()
        print("  --- Warnings ---")
        for w in fr.warnings:
            print(f"    WARNING: {w}")

    plot_file = ""
    if fr.error:
        print()
        print(f"  ANALYSIS FAILED: {fr.error}")
        row = {col: "" for col in RESULT_COLUMNS}
        row["file"] = dx_path.name
        row["source_path"] = str(dx_path)
        row["error"] = fr.error
        rows = [row]
        exit_code = 1
    else:
        peak = fr.peak
        assert peak is not None
        print()
        print("  --- Peak Detection Results ---")
        print(f"    Target ppm          : {peak.target_ppm:.4f}")
        print(f"    Detected peak ppm   : {peak.peak_ppm:.4f}")
        print(f"    Peak height (intens): {peak.peak_height:.6g}")
        print(f"    Peak area (integr)  : {peak.peak_area:.6g}")
        print(f"    SNR                 : {peak.snr:.4f}")
        print(f"    Prominence          : {peak.prominence:.6g}")
        print(f"    Prominence SNR      : {peak.prominence_snr:.4f}")
        print(f"    Width (ppm)         : {peak.width_ppm:.6f}")
        print(f"    Baseline            : {peak.baseline:.6g}")
        print(f"    Noise               : {peak.noise:.6g}")
        print(f"    Peaks considered    : {peak.peaks_considered}")
        print(f"    Points in window    : {peak.points_in_window}")

        # Generate plot
        if cfg.plots:
            try:
                plot_file = str(plot_peak_review(
                    dx_path, peak, plots_dir,
                    target_ppm=cfg.target_ppm,
                    window_ppm=cfg.window_ppm,
                    plot_window_ppm=cfg.plot_window_ppm,
                    line_broadening_hz=cfg.line_broadening_hz,
                    zero_fill_points=cfg.zero_fill_points,
                ))
                print(f"\n  Plot saved: {plot_file}")
            except Exception as exc:
                print(f"\n  WARNING: Plot generation failed: {exc}")

        row = {
            "file": dx_path.name,
            "source_path": str(dx_path),
            "timestamp": fr.timestamp.isoformat() if fr.timestamp else "",
            "elapsed_seconds": 0,
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
            "delta_height": "",
            "delta_area": "",
            "pct_change_height": "",
            "pct_change_area": "",
            "error": "",
        }
        rows = [row]
        exit_code = 0

    # Save outputs
    csv_path = write_csv(out_dir / "results.csv", rows)
    summary = build_summary_payload(
        script_name="analyze_single.py",
        input_paths=[str(dx_path)],
        results=[fr],
        parameters={
            "target_ppm": cfg.target_ppm,
            "window_ppm": cfg.window_ppm,
            "plot_window_ppm": cfg.plot_window_ppm,
            "plots_enabled": cfg.plots,
        },
        extra={"plot_file": plot_file},
    )
    summary_path = write_summary(out_dir / "summary.json", summary)

    print()
    print(f"  Results CSV : {csv_path}")
    print(f"  Summary     : {summary_path}")
    print(f"  Output dir  : {out_dir}")
    print("=" * 70)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
