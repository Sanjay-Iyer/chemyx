"""Analyze one .dx file or a directory of .dx files near 6.1 ppm."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from chemyx_lab import config
from chemyx_lab.nmr import (
    NmrProcessingError,
    analyze_dx_peak,
    iter_dx_files,
    plot_peak_region,
)
from chemyx_lab.nmr_outputs import (
    RESULT_COLUMNS,
    create_analysis_run,
    error_row,
    peak_result_to_row,
    write_manifest,
    write_results_csv,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Find a small NMR peak near target ppm.")
    parser.add_argument("path", help="DX file or directory containing DX files")
    parser.add_argument("--target", type=float, default=config.NMR_TARGET_PPM)
    parser.add_argument("--window", type=float, default=config.NMR_PEAK_WINDOW_PPM)
    parser.add_argument(
        "--min-prominence-snr",
        type=float,
        default=0.0,
        help="minimum scipy peak prominence divided by local noise",
    )
    parser.add_argument(
        "--min-distance-ppm",
        type=float,
        default=0.01,
        help="minimum spacing between scipy peaks in ppm",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs") / "nmr_analysis",
        help="root directory for timestamped NMR analysis runs",
    )
    parser.add_argument(
        "--run-name",
        help="optional folder name under --output-dir; defaults to timestamp",
    )
    parser.add_argument(
        "--csv-file",
        type=Path,
        help="optional explicit CSV path; defaults to <run_dir>/results.csv",
    )
    parser.add_argument(
        "--plots-dir",
        "--plot-dir",
        dest="plots_dir",
        type=Path,
        help="optional explicit plot directory; defaults to <run_dir>/plots",
    )
    parser.add_argument(
        "--plot-window",
        type=float,
        default=0.5,
        help="ppm half-window for review plots around the target",
    )
    parser.add_argument("--no-plots", action="store_true", help="skip PNG review plots")
    args = parser.parse_args()

    files = list(iter_dx_files(Path(args.path)))
    if not files:
        print(f"No .dx files found under {args.path}")
        return 1

    run = create_analysis_run(
        args.output_dir,
        run_name=args.run_name,
        csv_file=args.csv_file,
        plots_dir=args.plots_dir,
    )

    print(f"Analysis run : {run.run_dir}")
    print(f"Results CSV  : {run.results_csv}")
    print(f"Plots dir    : {run.plots_dir if not args.no_plots else '(plots disabled)'}")
    print()
    print(
        ", ".join(
            column
            for column in RESULT_COLUMNS
            if column not in {"source_path", "points_in_window", "error"}
        )
    )
    exit_code = 0
    rows = []
    for dx_file in files:
        plot_file = ""
        try:
            result = analyze_dx_peak(
                dx_file,
                target_ppm=args.target,
                window_ppm=args.window,
                min_prominence_snr=args.min_prominence_snr,
                min_distance_ppm=args.min_distance_ppm,
            )
            if not args.no_plots:
                plot_file = str(
                    plot_peak_region(
                        dx_file,
                        result,
                        run.plots_dir,
                        target_ppm=args.target,
                        detection_window_ppm=args.window,
                        plot_window_ppm=args.plot_window,
                    )
                )
        except NmrProcessingError as exc:
            rows.append(error_row(dx_file, exc))
            print(f"{dx_file}, ERROR, {exc}")
            exit_code = 1
            continue
        rows.append(peak_result_to_row(result, plot_file=plot_file))
        print(
            f"{result.source.name}, {result.target_ppm:.4f}, "
            f"{result.peak_ppm:.4f}, {result.snr:.2f}, "
            f"{result.prominence_snr:.2f}, {result.prominence:.6g}, "
            f"{result.width_ppm:.4f}, {result.peaks_considered}, "
            f"{result.peak_height:.6g}, {result.baseline:.6g}, {result.noise:.6g}, "
            f"{plot_file}"
        )

    write_results_csv(run.results_csv, rows)
    write_manifest(
        run.manifest_json,
        {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "input_path": str(Path(args.path)),
            "files_processed": len(files),
            "results_csv": str(run.results_csv),
            "plots_dir": "" if args.no_plots else str(run.plots_dir),
            "parameters": {
                "target_ppm": args.target,
                "window_ppm": args.window,
                "min_prominence_snr": args.min_prominence_snr,
                "min_distance_ppm": args.min_distance_ppm,
                "plot_window_ppm": args.plot_window,
                "plots_enabled": not args.no_plots,
            },
        },
    )
    print()
    print(f"Saved results CSV: {run.results_csv}")
    if not args.no_plots:
        print(f"Saved review plots: {run.plots_dir}")
    print(f"Saved run manifest: {run.manifest_json}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
