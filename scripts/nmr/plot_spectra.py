"""Visualise NMR spectra (intensity vs ppm), not just summary statistics.

For every ``.dx`` file this produces:

* a **full-spectrum** plot (whole ppm range) with the scipy-detected peak near
  ``target_ppm`` marked,
* a **narrow zoom** around the target region (``target_ppm ± zoom_window_ppm``), and
* a **wide zoom** over an absolute ppm range (``zoom_wide_min_ppm``..``zoom_wide_max_ppm``,
  default 5–7 ppm).

Across all files it produces:

* an **overlay** of every spectrum on one axis (colour = acquisition order),
* an **overlay zoomed** to the target region, and
* a **stacked waterfall** so the target peak's evolution is easy to read.

A ``results.csv`` peak table and ``summary.json`` are written alongside, and the
output folder is named after the input data (e.g. ``06-08-26_<ts>_spectra``).

Usage::

    python scripts/nmr/plot_spectra.py results/raw/nmr/06-08-26/
    python scripts/nmr/plot_spectra.py file1.dx file2.dx --zoom 0.3
    python scripts/nmr/plot_spectra.py dir/ --target 5.8 --no-normalize

Parameters default to configs/nmr/analysis.yaml ([common] + [plot_spectra]).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from _common import (
    RESULT_COLUMNS,
    analyze_file,
    build_summary_payload,
    collect_dx_files,
    create_output_dir,
    derive_group,
    derive_run_name,
    load_spectrum,
    plot_spectra_overlay,
    plot_spectra_stacked,
    plot_spectrum_single,
    sort_by_timestamp,
    write_csv,
    write_summary,
    _safe_name,
)
from _config import (
    add_config_argument,
    apply_cli_overrides,
    load_config,
    peak_analysis_kwargs,
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plot NMR spectra (intensity vs ppm): per-file, overlaid, "
                    "and stacked, with the ~target_ppm peak marked. Parameters "
                    "come from configs/nmr/analysis.yaml unless overridden below."
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
    parser.add_argument("--zoom", dest="zoom_window_ppm", type=float, default=None,
                        help="ppm half-window for zoomed plots (overrides config)")
    parser.add_argument("--normalize", dest="normalize_overlay",
                        action=argparse.BooleanOptionalAction, default=None,
                        help="normalize each trace in the overlay (overrides config)")
    parser.add_argument("--output-dir", dest="output_dir", type=Path, default=None,
                        help="base directory for analysis output (overrides config)")
    parser.add_argument("--run-name", dest="run_name", default=None,
                        help="custom folder name (default: named after input)")
    parser.add_argument("--group", dest="group", default=None,
                        help="output sub-folder, e.g. sample type (overrides config)")
    return parser


def _row_for(fr) -> dict:
    """Build a results.csv row (peak metrics) for one analysed file."""
    row = {col: "" for col in RESULT_COLUMNS}
    row["file"] = fr.path.name
    row["source_path"] = str(fr.path)
    row["timestamp"] = fr.timestamp.isoformat() if fr.timestamp else ""
    if fr.error or fr.peak is None:
        row["error"] = fr.error or "analysis failed"
        return row
    peak = fr.peak
    row.update({
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
    })
    return row


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    cfg = apply_cli_overrides(load_config("plot_spectra", args.config), args)

    files = collect_dx_files(args.paths)
    if not files:
        print("ERROR: no .dx files found in the given paths.", file=sys.stderr)
        return 1

    print(f"Found {len(files)} .dx file(s)")

    # Peak analysis (for the detected-peak marker + CSV) with error handling.
    results = [
        analyze_file(
            f,
            target_ppm=cfg.target_ppm,
            window_ppm=cfg.window_ppm,
            **peak_analysis_kwargs(cfg),
        )
        for f in files
    ]
    results = sort_by_timestamp(results)

    # Output dir: <output_dir>/[group/]<input>_<timestamp>_spectra
    group = derive_group([f.name for f in files], cfg.group_tokens, cfg.group)
    base_out = cfg.output_dir / group if group else cfg.output_dir
    out_dir = create_output_dir(
        base_out, "spectra",
        run_name=cfg.run_name or derive_run_name(args.paths, "spectra"),
    )
    zoom_dir = out_dir / "plots" / "zoom"
    zoom_wide_dir = out_dir / "plots" / "zoom_wide"
    full_dir = out_dir / "plots" / "full"
    wide_lo = min(cfg.zoom_wide_min_ppm, cfg.zoom_wide_max_ppm)
    wide_hi = max(cfg.zoom_wide_min_ppm, cfg.zoom_wide_max_ppm)

    # Load spectra (in timestamp order) and draw per-file plots.
    spectra: list[tuple[str, object, object]] = []
    plot_files: list[str] = []
    successes = 0
    failures = 0

    for fr in results:
        label = fr.path.stem
        peak_ppm = fr.peak.peak_ppm if fr.peak is not None else None
        try:
            ppm, magnitude = load_spectrum(
                fr.path,
                line_broadening_hz=cfg.line_broadening_hz,
                zero_fill_points=cfg.zero_fill_points,
            )
        except Exception as exc:
            failures += 1
            print(f"  ** {fr.path.name}: could not load spectrum: {exc}")
            continue

        successes += 1
        spectra.append((label, ppm, magnitude))
        safe = _safe_name(label)

        # Zoomed to target region (the primary view) — plots/zoom/<file>.png
        try:
            p = plot_spectrum_single(
                ppm, magnitude, label=label,
                output_path=zoom_dir / f"{safe}.png",
                target_ppm=cfg.target_ppm, window_ppm=cfg.window_ppm,
                peak_ppm=peak_ppm, zoom_ppm=cfg.zoom_window_ppm,
                title=f"{fr.path.name} — {cfg.target_ppm:g} ppm region",
            )
            plot_files.append(str(p))
        except Exception as exc:
            print(f"  WARNING: zoom plot failed for {fr.path.name}: {exc}")

        # Wide zoom over an absolute ppm range — plots/zoom_wide/<file>.png
        try:
            p = plot_spectrum_single(
                ppm, magnitude, label=label,
                output_path=zoom_wide_dir / f"{safe}.png",
                target_ppm=cfg.target_ppm, window_ppm=cfg.window_ppm,
                peak_ppm=peak_ppm, range_ppm=(wide_lo, wide_hi),
                title=f"{fr.path.name} — {wide_lo:g}-{wide_hi:g} ppm",
            )
            plot_files.append(str(p))
        except Exception as exc:
            print(f"  WARNING: wide-zoom plot failed for {fr.path.name}: {exc}")

        # Full spectrum (kept for later) — plots/full/<file>.png
        try:
            p = plot_spectrum_single(
                ppm, magnitude, label=label,
                output_path=full_dir / f"{safe}.png",
                target_ppm=cfg.target_ppm, window_ppm=cfg.window_ppm,
                peak_ppm=peak_ppm,
                title=f"{fr.path.name} — full spectrum",
            )
            plot_files.append(str(p))
        except Exception as exc:
            print(f"  WARNING: full plot failed for {fr.path.name}: {exc}")

        status = "ok" if fr.peak is not None else f"no peak ({fr.error})"
        print(f"  {fr.path.name:<55s} {status}")

    # Cross-file overlays / waterfall.
    if len(spectra) >= 1:
        plots_dir = out_dir / "plots"
        try:
            plot_files.append(str(plot_spectra_overlay(
                spectra, output_path=plots_dir / "overlay_full.png",
                target_ppm=cfg.target_ppm, window_ppm=cfg.window_ppm,
                normalize=cfg.normalize_overlay,
                title="Overlaid spectra — full range",
            )))
            plot_files.append(str(plot_spectra_overlay(
                spectra, output_path=plots_dir / "overlay_target.png",
                target_ppm=cfg.target_ppm, window_ppm=cfg.window_ppm,
                zoom_ppm=cfg.zoom_window_ppm, normalize=cfg.normalize_overlay,
                title=f"Overlaid spectra — {cfg.target_ppm:g} ppm region",
            )))
            plot_files.append(str(plot_spectra_stacked(
                spectra, output_path=plots_dir / "stacked_target.png",
                target_ppm=cfg.target_ppm, window_ppm=cfg.window_ppm,
                zoom_ppm=cfg.zoom_window_ppm,
                title=f"Stacked spectra — {cfg.target_ppm:g} ppm region",
            )))
        except Exception as exc:
            print(f"  WARNING: overlay/stacked plot failed: {exc}")

    # CSV + summary.
    rows = [_row_for(fr) for fr in results]
    csv_path = write_csv(out_dir / "results.csv", rows, columns=RESULT_COLUMNS)
    summary = build_summary_payload(
        script_name="plot_spectra.py",
        input_paths=[str(p) for p in args.paths],
        results=results,
        parameters={
            "target_ppm": cfg.target_ppm,
            "window_ppm": cfg.window_ppm,
            "zoom_window_ppm": cfg.zoom_window_ppm,
            "zoom_wide_ppm": [wide_lo, wide_hi],
            "normalize_overlay": cfg.normalize_overlay,
        },
        extra={"plot_files": plot_files},
    )
    summary_path = write_summary(out_dir / "summary.json", summary)

    print()
    print(f"  Zoom plots       : {zoom_dir}")
    print(f"  Wide-zoom plots  : {zoom_wide_dir}")
    print(f"  Full plots       : {full_dir}")
    print(f"  Overlay/stacked  : {out_dir / 'plots'}")
    print(f"  Results CSV      : {csv_path}")
    print(f"  Summary JSON     : {summary_path}")
    print(f"  Output dir       : {out_dir}")
    print(f"  ({successes} plotted, {failures} failed to load)")
    return 1 if successes == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
