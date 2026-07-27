"""Process NMReady JCAMP-DX FIDs and discover all peaks in a ppm region.

Automated picking is performed on a baseline-corrected magnitude diagnostic,
while plots and quantitative peak heights remain tied to the phase-corrected
real spectrum. The default 5.0--6.5 ppm search does not assume a target peak.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import _bootstrap  # noqa: F401

from chemyx_lab.config import ConfigError, read_mapping_config
from chemyx_lab.analysis.nmr import (
    RegionPeakPickingResult,
    align_validated_reference,
    align_solvent_axis,
    asymmetric_least_squares_baseline,
    build_phased_spectrum,
    evaluate_reference_model,
    integrate_above_local_baseline,
    pick_spectrum_region,
    subtract_abd_polynomial_baseline,
    track_peak_families,
)

from _common import (
    _safe_name,
    collect_dx_files,
    parse_acquisition_timestamp,
)


def _default_run_name(paths) -> str:
    """Output-folder name defaults to the input's own name (e.g. 06-09-26).

    A directory contributes its own name; a single file contributes its stem.
    """
    first = Path(paths[0])
    return first.name if first.is_dir() else first.stem
from _config import DEFAULT_CONFIG_PATH


def _parser(config_defaults=None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Process NMReady JCAMP-DX FIDs and pick all regional peaks."
    )
    parser.add_argument("paths", nargs="+", help=".dx files or directories")
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help=f"processing YAML (default: {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("results") / "processed" / "nmr",
    )
    parser.add_argument("--run-name")
    parser.add_argument("--line-broadening-hz", type=float, default=None)
    parser.add_argument("--zero-fill-points", type=int, default=None)
    parser.add_argument("--phase0", type=float, default=None)
    parser.add_argument("--phase1", type=float, default=None)
    parser.add_argument("--direct-phase", action="store_true")
    parser.add_argument(
        "--phase-method",
        choices=(
            "stored",
            "automatic_peak_minima",
            "peak_minima",
            "none",
            "manual",
        ),
        default="stored",
    )
    parser.add_argument(
        "--truncation-window",
        choices=("none", "half-cosine"),
        default="none",
    )
    parser.add_argument("--region-min", type=float, default=5.0)
    parser.add_argument("--region-max", type=float, default=6.5)
    # Wider ppm window shown in the region plot for context (peaks are still
    # only detected within --region-min/--region-max). The y-axis is scaled to
    # the detected peak, so a taller neighbour (e.g. toluene ~7 ppm) is allowed
    # to clip off the top rather than flatten the target peak.
    parser.add_argument("--display-min", type=float, default=4.0)
    parser.add_argument("--display-max", type=float, default=7.0)
    parser.add_argument("--min-prominence-snr", type=float, default=5.0)
    parser.add_argument("--min-peak-distance-ppm", type=float, default=0.04)
    parser.add_argument("--min-peak-width-ppm", type=float, default=0.015)
    parser.add_argument("--baseline-order", type=int, default=3)
    parser.add_argument(
        "--baseline-method",
        choices=(
            "asymmetric_least_squares",
            "polynomial",
            "none",
            "regional-polynomial",
            "abd-linear",
        ),
        default="polynomial",
    )
    parser.add_argument("--abd-sections", type=int, default=128)
    parser.add_argument("--abd-noise-factor", type=float, default=3.0)
    parser.add_argument("--abd-window-points", type=int, default=60)
    parser.add_argument("--smoothing-window-ppm", type=float, default=0.006)
    parser.add_argument("--reference-observed-ppm", type=float, default=None)
    parser.add_argument("--reference-expected-ppm", type=float, default=None)
    parser.add_argument(
        "--reference-method",
        choices=("metadata", "validated_peak", "manual_shift", "none"),
        default="metadata",
    )
    parser.add_argument(
        "--reference-model",
        choices=(
            "metadata",
            "protonated_toluene_low_field_neat",
            "protonated_toluene_dilute_cdcl3",
            "toluene_d8_residual",
        ),
        default="metadata",
    )
    parser.add_argument("--solvent-identity", default="unknown")
    parser.add_argument("--solvent-isotopic-form", default="unknown")
    parser.add_argument(
        "--maximum-reference-disagreement-ppm",
        type=float,
        default=0.05,
    )
    parser.add_argument("--reference-search-window-ppm", type=float)
    parser.add_argument("--reference-minimum-snr", type=float)
    parser.add_argument("--reference-minimum-prominence-snr", type=float)
    parser.add_argument("--reference-minimum-width-ppm", type=float, default=0.002)
    parser.add_argument("--reference-maximum-width-ppm", type=float, default=0.30)
    parser.add_argument("--reference-maximum-shift-ppm", type=float, default=0.20)
    parser.add_argument(
        "--reference-confidence",
        choices=("none", "low", "medium", "high"),
        default="none",
    )
    parser.add_argument("--export-csv", action="store_true")
    parser.add_argument(
        "--normalization",
        choices=("none", "max"),
        default="none",
    )
    parser.add_argument(
        "--solvent",
        choices=("none", "toluene", "dmf", "dmac"),
        default="none",
    )
    parser.add_argument("--solvent-resonance", default=None)
    parser.add_argument("--solvent-validation-resonance", default=None)
    if config_defaults:
        parser.set_defaults(**config_defaults)
    return parser


def _config_defaults(argv):
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--config", type=Path)
    known, _ = preliminary.parse_known_args(argv)
    path = DEFAULT_CONFIG_PATH if known.config is None else known.config
    if not path.exists():
        if known.config is not None:
            raise ConfigError(f"NMR analysis config not found: {path}")
        return {}
    raw = read_mapping_config(path, "NMR processing config")
    sections = {
        "processing": {
            "phase_method": "phase_method",
            "baseline_method": "baseline_method",
            "reference_method": "reference_method",
            "zero_fill_points": "zero_fill_points",
            "line_broadening_hz": "line_broadening_hz",
            "normalization": "normalization",
        },
        "regional_analysis": {
            "ppm_min": "region_min",
            "ppm_max": "region_max",
        },
        "reference": {
            "method": "reference_model",
            "solvent_identity": "solvent_identity",
            "solvent_isotopic_form": "solvent_isotopic_form",
            "expected_ppm": "reference_expected_ppm",
            "search_window_ppm": "reference_search_window_ppm",
            "minimum_snr": "reference_minimum_snr",
            "minimum_prominence_snr": "reference_minimum_prominence_snr",
            "minimum_width_ppm": "reference_minimum_width_ppm",
            "maximum_width_ppm": "reference_maximum_width_ppm",
            "maximum_allowed_shift_ppm": "reference_maximum_shift_ppm",
            "maximum_reference_disagreement_ppm": (
                "maximum_reference_disagreement_ppm"
            ),
        },
    }
    defaults = {}
    for section_name, mapping in sections.items():
        section = raw.get(section_name, {})
        if section is None:
            continue
        if not isinstance(section, dict):
            raise ConfigError(f"[{section_name}] in {path} must be a mapping")
        allowed = set(mapping)
        if section_name == "regional_analysis":
            allowed.add("detect_all_peaks")
        if section_name == "reference":
            allowed.update(
                {
                    "enabled",
                    "internal_standard",
                    "preserve_original_axis",
                    "require_multiple_reference_regions",
                }
            )
        unknown = sorted(set(section) - allowed)
        if unknown:
            raise ConfigError(
                f"Unknown key(s) in [{section_name}] of {path}: "
                + ", ".join(unknown)
            )
        for key, destination in mapping.items():
            if key in section:
                defaults[destination] = section[key]
    defaults["config"] = known.config
    return defaults


def _get_plotting():
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    return plt


def _plot_real(
    ppm,
    real,
    path: Path,
    title: str,
    region: tuple[float, float],
    peaks: tuple[float, ...],
    *,
    zoom: bool,
    display: tuple[float, float] | None = None,
) -> Path:
    import numpy as np

    plt = _get_plotting()
    ppm = np.asarray(ppm)
    real = np.asarray(real)
    fig, ax = plt.subplots(figsize=(10.5, 5.8), dpi=180)
    ax.plot(ppm, real, color="#173f5f", linewidth=0.85)
    ax.axhline(0, color="#777777", linewidth=0.7)
    lo, hi = sorted(region)
    ax.axvspan(lo, hi, color="#f4d35e", alpha=0.08, label="search region")
    if zoom:
        # x-axis: wider display window for context (default 4-7 ppm).
        dlo, dhi = sorted(display) if display else (lo, hi)
        ax.set_xlim(dhi, dlo)
        # y-axis: scale to the detected peak so a taller neighbour (e.g. the
        # toluene aromatic peak near 7 ppm) clips off the top instead of
        # flattening the target peak. Baseline floor comes from the search
        # region so the local slope stays visible.
        in_region = (ppm >= lo) & (ppm <= hi)
        region_y = real[in_region]
        if peaks:
            ymax = max(
                float(real[int(np.argmin(np.abs(ppm - p)))]) for p in peaks
            )
        elif region_y.size:
            ymax = float(np.max(region_y))
        else:
            ymax = 1.0
        ymin = float(np.min(region_y)) if region_y.size else -1.0
        span = (ymax - ymin) if ymax > ymin else 1.0
        ax.set_ylim(ymin - 0.08 * span, ymax + 0.15 * span)
    else:
        ax.set_xlim(float(np.max(ppm)), float(np.min(ppm)))
    for index, peak_ppm in enumerate(peaks):
        ax.axvline(
            peak_ppm,
            color="#d1495b",
            linestyle="--",
            linewidth=1,
            label="QC-passed peak" if index == 0 else None,
        )
        if zoom:
            point = int(np.argmin(np.abs(np.asarray(ppm) - peak_ppm)))
            ax.annotate(
                f"{peak_ppm:.3f}",
                (peak_ppm, float(np.asarray(real)[point])),
                xytext=(0, 8),
                textcoords="offset points",
                ha="center",
                fontsize=8,
            )
    ax.set(title=title, xlabel=r"$^1$H chemical shift (ppm)")
    ax.set_ylabel("Phase-corrected real intensity (a.u.)")
    ax.grid(True, alpha=0.18)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def _plot_overlay(
    results: list[tuple[str, RegionPeakPickingResult]],
    path: Path,
    *,
    stacked: bool,
) -> Path:
    import numpy as np

    plt = _get_plotting()
    fig, ax = plt.subplots(figsize=(11.5, 7 if stacked else 6.3), dpi=180)
    colors = plt.get_cmap("viridis")
    scale = (
        max(float(np.percentile(np.abs(item.smoothed), 99)) for _, item in results)
        if stacked
        else 1.0
    ) or 1.0
    for index, (label, result) in enumerate(results):
        color = colors(index / max(1, len(results) - 1))
        offset = index * 0.8 if stacked else 0.0
        y = result.smoothed / scale + offset if stacked else result.smoothed
        ax.plot(result.ppm_axis, y, color=color, linewidth=0.85, label=label)
        if not stacked:
            ax.scatter(
                [peak.peak_ppm for peak in result.peaks],
                [peak.peak_height for peak in result.peaks],
                s=18,
                color=[color],
                edgecolor="white",
                linewidth=0.4,
                zorder=3,
            )
    ax.set_xlim(
        max(item.region_max_ppm for _, item in results),
        min(item.region_min_ppm for _, item in results),
    )
    ax.axhline(0, color="#777777", linewidth=0.7)
    ax.set_title(
        "Stacked regional spectra"
        if stacked
        else "Regional magnitude spectra — baseline removed"
    )
    ax.set_xlabel(r"$^1$H chemical shift (ppm)")
    ax.set_ylabel("Normalized intensity + offset" if stacked else "Corrected magnitude")
    ax.grid(True, alpha=0.18)
    if not stacked:
        ax.legend(loc="best", fontsize=7, ncol=2)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def _write_spectrum_csv(path, original_ppm, referenced_ppm, spectrum):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["original_ppm", "referenced_ppm", "real", "imaginary", "magnitude"]
        )
        writer.writerows(
            zip(
                original_ppm,
                referenced_ppm,
                spectrum.real,
                spectrum.imaginary,
                spectrum.magnitude,
            )
        )
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _versions() -> dict[str, str]:
    versions = {"python": sys.version.split()[0]}
    for package in ("numpy", "scipy", "matplotlib", "nmrglue"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    return versions


def _error(args) -> str | None:
    if args.line_broadening_hz is not None and args.line_broadening_hz < 0:
        return "--line-broadening-hz must be non-negative"
    if args.zero_fill_points is not None and args.zero_fill_points <= 0:
        return "--zero-fill-points must be positive"
    if args.region_min == args.region_max:
        return "--region-min and --region-max must differ"
    if min(
        args.min_peak_distance_ppm,
        args.min_peak_width_ppm,
        args.smoothing_window_ppm,
    ) <= 0:
        return "peak distance, width, and smoothing window must be positive"
    if args.min_prominence_snr < 0 or args.baseline_order < 0:
        return "prominence SNR and baseline order must be non-negative"
    one_reference = (
        args.reference_observed_ppm is None
    ) != (args.reference_expected_ppm is None)
    if one_reference:
        return "reference observed and expected ppm must be supplied together"
    if args.solvent != "none" and args.solvent_resonance is None:
        return "--solvent-resonance is required when --solvent is used"
    if args.solvent != "none" and args.reference_observed_ppm is not None:
        return "manual and solvent-derived referencing cannot be combined"
    if args.reference_model != "metadata" and args.solvent != "none":
        return "reference models and legacy solvent alignment cannot be combined"
    if (
        args.reference_model != "metadata"
        and args.reference_method != "metadata"
    ):
        return "reference models and single-peak reference methods cannot be combined"
    if args.phase_method == "manual" and (
        args.phase0 is None or args.phase1 is None
    ):
        return "manual phasing requires --phase0 and --phase1"
    if args.reference_method == "validated_peak":
        required = (
            args.reference_expected_ppm,
            args.reference_search_window_ppm,
            args.reference_minimum_snr,
            args.reference_minimum_prominence_snr,
        )
        if any(value is None for value in required):
            return (
                "validated_peak referencing requires expected ppm, search "
                "window, minimum SNR, and minimum prominence SNR"
            )
    if args.reference_method == "manual_shift" and one_reference:
        return "manual_shift requires both observed and expected ppm"
    if args.reference_method == "manual_shift" and args.reference_observed_ppm is None:
        return "manual_shift requires observed and expected ppm"
    return None


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    try:
        defaults = _config_defaults(effective_argv)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    args = _parser(defaults).parse_args(effective_argv)
    files = collect_dx_files(args.paths)
    if not files:
        print("ERROR: no .dx files found.", file=sys.stderr)
        return 1
    if message := _error(args):
        print(f"ERROR: {message}.", file=sys.stderr)
        return 2
    # Output folder is named after the input (e.g. 06-09-26 -> 06-09-26),
    # unless --run-name overrides it. Re-running refreshes the folder in place
    # instead of piling up 06-09-26_2, _3, ... copies.
    run_name = args.run_name or _default_run_name(args.paths)
    out_dir = Path(args.output_dir) / _safe_name(run_name)
    if out_dir.exists():
        print(f"  (refreshing existing output: {out_dir})")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    manual_reference_shift = (
        0.0
        if args.reference_method != "manual_shift"
        else args.reference_expected_ppm - args.reference_observed_ppm
    )
    records: list[dict] = []
    peak_rows: list[dict] = []
    plot_results: list[tuple[str, RegionPeakPickingResult]] = []

    for spectrum_index, path in enumerate(files):
        try:
            spectrum = build_phased_spectrum(
                path,
                line_broadening_hz=args.line_broadening_hz,
                zero_fill_points=args.zero_fill_points,
                phase0_deg=args.phase0,
                phase1_deg=args.phase1,
                inverse_phase=not args.direct_phase,
                phase_method=args.phase_method,
                truncation_window=args.truncation_window,
            )
            reference_shift = manual_reference_shift
            reference_observed = args.reference_observed_ppm
            reference_expected = args.reference_expected_ppm
            reference_method = args.reference_method
            reference_confidence = args.reference_confidence
            reference_disagreement = ""
            reference_model = args.reference_model
            reference_region_count = 0
            reference_qc_pass = True
            reference_qc_failure_reasons = ""
            proposed_shift_ppm = reference_shift
            applied_shift_hz = (
                reference_shift * spectrum.observe_frequency_mhz
            )
            analysis_ppm = spectrum.ppm_axis
            if args.reference_model != "metadata":
                model_result = evaluate_reference_model(
                    spectrum.ppm_axis,
                    spectrum.magnitude,
                    reference_model=args.reference_model,
                    observe_frequency_mhz=spectrum.observe_frequency_mhz,
                    solvent_identity=args.solvent_identity,
                    solvent_isotopic_form=args.solvent_isotopic_form,
                    minimum_snr=(
                        5.0
                        if args.reference_minimum_snr is None
                        else args.reference_minimum_snr
                    ),
                    minimum_prominence_snr=(
                        5.0
                        if args.reference_minimum_prominence_snr is None
                        else args.reference_minimum_prominence_snr
                    ),
                    minimum_width_ppm=args.reference_minimum_width_ppm,
                    maximum_width_ppm=args.reference_maximum_width_ppm,
                    maximum_shift_ppm=args.reference_maximum_shift_ppm,
                    maximum_reference_disagreement_ppm=(
                        args.maximum_reference_disagreement_ppm
                    ),
                )
                analysis_ppm = model_result.referenced_ppm
                reference_shift = model_result.applied_shift_ppm
                proposed_shift_ppm = model_result.proposed_shift_ppm
                applied_shift_hz = model_result.applied_shift_hz
                reference_method = "multi-region reference model"
                reference_confidence = model_result.reference_confidence
                reference_disagreement = (
                    ""
                    if model_result.reference_region_agreement is None
                    else model_result.reference_region_agreement
                )
                reference_region_count = model_result.reference_region_count
                reference_qc_pass = model_result.reference_qc_pass
                reference_qc_failure_reasons = "; ".join(
                    model_result.reference_qc_failure_reasons
                )
            elif args.solvent != "none":
                alignment = align_solvent_axis(
                    spectrum.ppm_axis,
                    spectrum.magnitude,
                    solvent=args.solvent,
                    resonance=args.solvent_resonance,
                    validation_resonance=args.solvent_validation_resonance,
                    minimum_snr=(
                        5.0
                        if args.reference_minimum_snr is None
                        else args.reference_minimum_snr
                    ),
                    minimum_prominence_snr=(
                        5.0
                        if args.reference_minimum_prominence_snr is None
                        else args.reference_minimum_prominence_snr
                    ),
                    minimum_width_ppm=args.reference_minimum_width_ppm,
                    maximum_width_ppm=args.reference_maximum_width_ppm,
                    maximum_shift_ppm=args.reference_maximum_shift_ppm,
                    observe_frequency_mhz=spectrum.observe_frequency_mhz,
                )
                analysis_ppm = alignment.referenced_ppm
                reference_shift = alignment.applied_shift_ppm
                reference_observed = alignment.reference_peak_observed_ppm
                reference_expected = alignment.reference_peak_expected_ppm
                reference_method = alignment.reference_method
                reference_confidence = alignment.reference_confidence
                reference_disagreement = alignment.shift_disagreement_ppm
                applied_shift_hz = (
                    reference_shift * spectrum.observe_frequency_mhz
                )
            elif args.reference_method == "validated_peak":
                alignment = align_validated_reference(
                    spectrum.ppm_axis,
                    spectrum.magnitude,
                    expected_ppm=args.reference_expected_ppm,
                    search_window_ppm=args.reference_search_window_ppm,
                    minimum_snr=args.reference_minimum_snr,
                    minimum_prominence_snr=(
                        args.reference_minimum_prominence_snr
                    ),
                    minimum_width_ppm=args.reference_minimum_width_ppm,
                    maximum_width_ppm=args.reference_maximum_width_ppm,
                    maximum_shift_ppm=args.reference_maximum_shift_ppm,
                    observe_frequency_mhz=spectrum.observe_frequency_mhz,
                )
                analysis_ppm = alignment.referenced_ppm
                reference_shift = alignment.applied_shift_ppm
                reference_observed = alignment.reference_peak_observed_ppm
                reference_expected = alignment.reference_peak_expected_ppm
                reference_method = alignment.reference_method
                reference_confidence = alignment.reference_confidence
                applied_shift_hz = (
                    reference_shift * spectrum.observe_frequency_mhz
                )
            quantitative_real = spectrum.real
            if args.baseline_method in {"abd-linear", "polynomial"}:
                quantitative_real, _, _, _ = subtract_abd_polynomial_baseline(
                    spectrum.real,
                    sections=args.abd_sections,
                    noise_factor=args.abd_noise_factor,
                    window_points=args.abd_window_points,
                    polynomial_order=1,
                )
            elif args.baseline_method == "asymmetric_least_squares":
                quantitative_real = (
                    spectrum.real
                    - asymmetric_least_squares_baseline(spectrum.real)
                )
            diagnostic_magnitude = spectrum.magnitude
            if args.normalization == "max":
                scale = float(max(abs(quantitative_real)))
                if not scale > 0:
                    raise ValueError("maximum normalization scale is not positive")
                quantitative_real = quantitative_real / scale
                diagnostic_magnitude = diagnostic_magnitude / scale
            picked = pick_spectrum_region(
                analysis_ppm,
                diagnostic_magnitude,
                region_min_ppm=args.region_min,
                region_max_ppm=args.region_max,
                min_prominence_snr=args.min_prominence_snr,
                min_distance_ppm=args.min_peak_distance_ppm,
                min_width_ppm=args.min_peak_width_ppm,
                baseline_polynomial_order=args.baseline_order,
                smoothing_window_ppm=args.smoothing_window_ppm,
                quantitative_intensity=quantitative_real,
                source=path,
            )
            timestamp, timestamp_source = parse_acquisition_timestamp(
                spectrum.metadata, path
            )
            timestamp_text = timestamp.isoformat() if timestamp else ""
            safe = _safe_name(path.stem)
            positions = tuple(peak.peak_ppm for peak in picked.peaks)
            full_plot = _plot_real(
                analysis_ppm,
                quantitative_real,
                out_dir / "plots" / "full" / f"{safe}.png",
                f"{path.name} — phase-corrected real spectrum",
                (args.region_min, args.region_max),
                positions,
                zoom=False,
            )
            region_plot = _plot_real(
                analysis_ppm,
                quantitative_real,
                out_dir / "plots" / "region" / f"{safe}.png",
                f"{path.name} — QC-passed regional peaks",
                (args.region_min, args.region_max),
                positions,
                zoom=True,
                display=(args.display_min, args.display_max),
            )
            spectrum_csv = ""
            if args.export_csv:
                spectrum_csv = str(
                    _write_spectrum_csv(
                        out_dir / "spectra_csv" / f"{safe}.csv",
                        spectrum.ppm_axis,
                        analysis_ppm,
                        spectrum,
                    )
                )
            for peak_number, peak in enumerate(picked.peaks, 1):
                local_integral = integrate_above_local_baseline(
                    analysis_ppm,
                    quantitative_real,
                    left_ppm=peak.interpolated_ppm - peak.width_ppm,
                    right_ppm=peak.interpolated_ppm + peak.width_ppm,
                )
                peak_rows.append(
                    {
                        "file": path.name,
                        "timestamp": timestamp_text,
                        "spectrum_index": spectrum_index,
                        "peak_number": peak_number,
                        "peak_family_id": "",
                        "observed_ppm": peak.peak_ppm - reference_shift,
                        "aligned_ppm": peak.peak_ppm,
                        "referenced_ppm": peak.peak_ppm,
                        "discrete_ppm": peak.peak_ppm - reference_shift,
                        "interpolated_ppm": (
                            peak.interpolated_ppm - reference_shift
                        ),
                        "height": peak.peak_height,
                        "signed_area": peak.signed_area,
                        "positive_area": peak.positive_area,
                        "local_feet_signed_area": local_integral.signed_area,
                        "local_feet_positive_area": local_integral.positive_area,
                        "integration_left_ppm": local_integral.left_ppm,
                        "integration_right_ppm": local_integral.right_ppm,
                        "prominence": peak.prominence,
                        "snr": peak.snr,
                        "width_ppm": peak.width_ppm,
                        "width_hz": peak.width_ppm * spectrum.observe_frequency_mhz,
                        "fit_model": "parabolic maximum",
                        "fit_quality": peak.interpolation_quality,
                        "classification": peak.classification,
                        "baseline_method": args.baseline_method,
                        "noise_region": (
                            f"{picked.region_min_ppm:g}.."
                            f"{picked.region_max_ppm:g} ppm"
                        ),
                        "reference_shift_ppm": reference_shift,
                        "alignment_shift_ppm": 0.0,
                        "qc_pass": True,
                        "qc_failure_reasons": "",
                    }
                )
            records.append(
                {
                    "file": path.name,
                    "source_path": str(path),
                    "raw_sha256": _sha256(path),
                    "timestamp": timestamp_text,
                    "timestamp_source": timestamp_source,
                    "processed_points": spectrum.processed_points,
                    "line_broadening_hz": spectrum.line_broadening_hz,
                    "phase0_deg": spectrum.phase0_deg,
                    "phase1_deg": spectrum.phase1_deg,
                    "phase_direction": (
                        "automatic"
                        if args.phase_method
                        in {"peak_minima", "automatic_peak_minima"}
                        else ("direct" if args.direct_phase else "inverse")
                    ),
                    "phase_method": args.phase_method,
                    "truncation_window": args.truncation_window,
                    "baseline_method": args.baseline_method,
                    "region_min_ppm": picked.region_min_ppm,
                    "region_max_ppm": picked.region_max_ppm,
                    "peak_count": len(picked.peaks),
                    "region_noise": picked.noise,
                    "peak_ppm_values": ";".join(f"{p:.6f}" for p in positions),
                    "reference_peak_observed_ppm": reference_observed,
                    "reference_peak_expected_ppm": reference_expected,
                    "reference_model": reference_model,
                    "solvent_identity": args.solvent_identity,
                    "solvent_isotopic_form": args.solvent_isotopic_form,
                    "proposed_shift_ppm": proposed_shift_ppm,
                    "applied_shift_ppm": reference_shift,
                    "applied_shift_hz": applied_shift_hz,
                    "reference_method": reference_method,
                    "reference_confidence": reference_confidence,
                    "reference_region_count": reference_region_count,
                    "reference_region_agreement": reference_disagreement,
                    "reference_qc_pass": reference_qc_pass,
                    "reference_qc_failure_reasons": (
                        reference_qc_failure_reasons
                    ),
                    "reference_shift_disagreement_ppm": reference_disagreement,
                    "alignment_shift_ppm": 0.0,
                    "full_plot": str(full_plot),
                    "region_plot": str(region_plot),
                    "spectrum_csv": spectrum_csv,
                    "error": "",
                }
            )
            plot_results.append((path.stem, picked))
            text = ", ".join(f"{ppm:.3f}" for ppm in positions)
            print(
                f"  processed {path.name}: {len(positions)} peak(s)"
                + (f" at {text} ppm" if text else "")
            )
        except Exception as exc:
            records.append(
                {"file": path.name, "source_path": str(path), "error": str(exc)}
            )
            print(f"  ERROR {path.name}: {exc}", file=sys.stderr)

    overlay = stacked = ""
    family_rows: list[dict] = []
    if plot_results:
        assignments, families = track_peak_families(
            [result.peaks for _, result in plot_results]
        )
        family_lookup = {
            (spectrum_index, peak_number): family_id
            for spectrum_index, family_ids in enumerate(assignments)
            for peak_number, family_id in enumerate(family_ids, 1)
        }
        for row in peak_rows:
            row["peak_family_id"] = family_lookup.get(
                (row["spectrum_index"], row["peak_number"]), ""
            )
        family_rows = [
            {
                "peak_family_id": family.family_id,
                "observations": family.observations,
                "first_spectrum_index": family.first_spectrum_index,
                "last_spectrum_index": family.last_spectrum_index,
                "median_ppm": family.median_ppm,
                "ppm_range": family.ppm_range,
                "median_width_ppm": family.median_width_ppm,
                "reproducible": family.reproducible,
            }
            for family in families
        ]
        overlay = str(
            _plot_overlay(
                plot_results,
                out_dir / "plots" / "overlay_region_corrected.png",
                stacked=False,
            )
        )
        stacked = str(
            _plot_overlay(
                plot_results,
                out_dir / "plots" / "stacked_region_corrected.png",
                stacked=True,
            )
        )

    result_columns = [
        "file", "source_path", "raw_sha256", "timestamp", "timestamp_source",
        "processed_points", "line_broadening_hz", "phase0_deg", "phase1_deg",
        "phase_direction", "phase_method", "truncation_window", "baseline_method",
        "region_min_ppm", "region_max_ppm", "peak_count",
        "region_noise", "peak_ppm_values", "reference_peak_observed_ppm",
        "reference_peak_expected_ppm", "reference_model", "solvent_identity",
        "solvent_isotopic_form", "proposed_shift_ppm", "applied_shift_ppm",
        "applied_shift_hz", "reference_method", "reference_confidence",
        "reference_region_count", "reference_region_agreement",
        "reference_qc_pass", "reference_qc_failure_reasons",
        "reference_shift_disagreement_ppm",
        "alignment_shift_ppm", "full_plot", "region_plot", "spectrum_csv", "error",
    ]
    peak_columns = [
        "file", "timestamp", "spectrum_index", "peak_number", "peak_family_id",
        "observed_ppm", "aligned_ppm", "referenced_ppm", "discrete_ppm",
        "interpolated_ppm", "height", "signed_area", "positive_area",
        "local_feet_signed_area", "local_feet_positive_area",
        "integration_left_ppm", "integration_right_ppm", "prominence", "snr",
        "width_ppm", "width_hz", "fit_model", "fit_quality",
        "classification", "baseline_method", "noise_region",
        "reference_shift_ppm", "alignment_shift_ppm", "qc_pass",
        "qc_failure_reasons",
    ]
    for filename, columns, rows in (
        ("results.csv", result_columns, records),
        ("peaks.csv", peak_columns, peak_rows),
        (
            "peak_families.csv",
            [
                "peak_family_id", "observations", "first_spectrum_index",
                "last_spectrum_index", "median_ppm", "ppm_range",
                "median_width_ppm", "reproducible",
            ],
            family_rows,
        ),
    ):
        with (out_dir / filename).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)

    summary = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "script": "process_fid.py",
        "git_commit": _git_commit(),
        "dependencies": _versions(),
        "inputs": [str(path) for path in args.paths],
        "files_found": len(files),
        "files_succeeded": sum(not row.get("error") for row in records),
        "files_failed": sum(bool(row.get("error")) for row in records),
        "peaks_found": len(peak_rows),
        "parameters": vars(args) | {"paths": [str(path) for path in args.paths]},
        "processing_order": [
            "nmrglue.jcampdx.read",
            "real + 1j * imaginary",
            "exp(-pi * line_broadening_hz * time_s)",
            "zero fill, FFT, metadata-derived ppm axis",
            "stored inverse phase for real-spectrum plots",
            "regional polynomial baseline on magnitude diagnostic",
            "Savitzky-Golay smoothing for detection only",
            "prominence, distance, width, and positive-area QC",
        ],
        "overlay_region_corrected": overlay,
        "stacked_region_corrected": stacked,
        "records": records,
    }
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    print(f"Output: {out_dir}")
    return 1 if summary["files_succeeded"] == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
