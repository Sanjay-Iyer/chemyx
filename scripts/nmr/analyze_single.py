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
    DEFAULT_OUTPUT_DIR,
    DEFAULT_TARGET_PPM,
    DEFAULT_WINDOW_PPM,
    analyze_file,
    build_summary_payload,
    create_output_dir,
    plot_peak_review,
    write_csv,
    write_summary,
    RESULT_COLUMNS,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detailed analysis of a single NMR .dx file."
    )
    parser.add_argument("path", help="Path to a .dx file")
    parser.add_argument("--target", type=float, default=DEFAULT_TARGET_PPM,
                        help="target peak position in ppm")
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW_PPM,
                        help="detection half-window in ppm")
    parser.add_argument("--plot-window", type=float, default=0.5,
                        help="ppm half-window for review plot")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR,
                        help="base directory for analysis output")
    parser.add_argument("--run-name", help="custom folder name (default: timestamp)")
    parser.add_argument("--no-plots", action="store_true",
                        help="skip review plot generation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    dx_path = Path(args.path)

    if not dx_path.is_file():
        print(f"ERROR: file not found: {dx_path}", file=sys.stderr)
        return 1

    # Analyse
    fr = analyze_file(dx_path, target_ppm=args.target, window_ppm=args.window)

    # Create output directory
    out_dir = create_output_dir(args.output_dir, "single", run_name=args.run_name)
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
        if not args.no_plots:
            try:
                plot_file = str(plot_peak_review(
                    dx_path, peak, plots_dir,
                    target_ppm=args.target,
                    window_ppm=args.window,
                    plot_window_ppm=args.plot_window,
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
            "target_ppm": args.target,
            "window_ppm": args.window,
            "plot_window_ppm": args.plot_window,
            "plots_enabled": not args.no_plots,
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
