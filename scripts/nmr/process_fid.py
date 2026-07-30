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
import statistics
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

from chemyx_lab.analysis.analysis_config import (
    ConfigError as StatisticsConfigError,
    load_statistics_config,
)
from chemyx_lab.analysis.baseline_flattening import (
    QC_COLUMNS as OVERLAY_BASELINE_QC_COLUMNS,
    FlattenedOverlayConfigError,
    flatten_residual_baseline,
    load_flattened_overlay_config,
    regional_authoritative_trace,
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
    parser.add_argument(
        "paths",
        nargs="*",
        help=(
            ".dx files or directories. Optional: falls back to input.paths in "
            "the config file, so a fully configured run needs no arguments."
        ),
    )
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
        # ALS follows a rolling solvent background (e.g. the toluene tail the
        # ~5.8 peak sits on) and removes it, giving a flat baseline like the
        # legacy/vendor plots. The polynomial/abd methods leave that tail as a
        # negative bowl. See the legacy nmr_template baseline handling.
        default="asymmetric_least_squares",
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
    # Post-detection reality gates. Detection runs on the magnitude spectrum,
    # where a broad baseline undulation can clear the prominence threshold; the
    # gates below are evaluated on the quantitative real spectrum, which is
    # what separates a resonance from a wobble. Tune in [peak_qc].
    parser.add_argument(
        "--detection-trace",
        choices=("real", "magnitude"),
        default="real",
        help=(
            "spectrum peaks are found on. 'real' is the phased, "
            "baseline-corrected trace that heights and areas are measured on; "
            "'magnitude' restores the older behaviour."
        ),
    )
    # Set from peak_qc.use_manual_thresholds; recorded for provenance only.
    parser.add_argument(
        "--qc-manual-thresholds",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--qc-min-snr", type=float, default=8.0)
    parser.add_argument("--qc-min-prominence-snr", type=float, default=3.0)
    parser.add_argument("--qc-min-width-hz", type=float, default=1.0)
    parser.add_argument("--qc-max-width-hz", type=float, default=10.0)
    parser.add_argument(
        "--qc-require-positive-area",
        action=argparse.BooleanOptionalAction,
        default=True,
    )
    # peaks_simple.csv can be narrowed to the one resonance being tracked, so
    # unrelated peaks elsewhere in the region do not clutter the time series.
    parser.add_argument(
        "--simple-restrict-to-window",
        action=argparse.BooleanOptionalAction,
        default=False,
    )
    parser.add_argument("--simple-target-ppm", type=float, default=5.8)
    parser.add_argument("--simple-window-ppm", type=float, default=0.5)
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
    parser.add_argument(
        "--statistics",
        action="store_true",
        default=False,
        help=(
            "additionally compute the publication-statistics tables (per-peak "
            "bootstrap uncertainty, run QC, fixed-window time series, rates, "
            "statistical plateau, kinetics, spectral similarity). Off by "
            "default; also enabled by statistics.enabled in the config. "
            "See docs/nmr_statistics.md."
        ),
    )
    parser.add_argument(
        "--bootstrap-iterations",
        type=int,
        default=None,
        help="override statistics.bootstrap.iterations for a faster/fuller run",
    )
    if config_defaults:
        parser.set_defaults(**config_defaults)
    return parser


def _statistics_config(argv):
    """Load and validate the ``statistics`` config section for this run."""
    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--config", type=Path)
    known, _ = preliminary.parse_known_args(argv)
    path = DEFAULT_CONFIG_PATH if known.config is None else known.config
    if not path.exists():
        return load_statistics_config(None)
    raw = read_mapping_config(path, "NMR processing config")
    return load_statistics_config(raw.get("statistics"))


def _flattened_overlay_config(argv):
    """Load and validate the optional plot-only baseline configuration."""

    preliminary = argparse.ArgumentParser(add_help=False)
    preliminary.add_argument("--config", type=Path)
    known, _ = preliminary.parse_known_args(argv)
    path = DEFAULT_CONFIG_PATH if known.config is None else known.config
    if not path.exists():
        return load_flattened_overlay_config(None)
    raw = read_mapping_config(path, "NMR processing config")
    plots = raw.get("plots")
    if plots is None:
        return load_flattened_overlay_config(None)
    if not isinstance(plots, dict):
        raise FlattenedOverlayConfigError("[plots] must be a mapping")
    # display_*_ppm are read by _config_defaults; only flattened_overlay is
    # this function's business.
    unknown = sorted(
        set(plots) - {"flattened_overlay", "display_min_ppm", "display_max_ppm"}
    )
    if unknown:
        raise FlattenedOverlayConfigError(
            "Unknown key(s) in [plots]: " + ", ".join(unknown)
        )
    return load_flattened_overlay_config(plots.get("flattened_overlay"))


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
        "input": {
            "paths": "paths",
        },
        "processing": {
            "phase_method": "phase_method",
            "baseline_method": "baseline_method",
            "reference_method": "reference_method",
            "zero_fill_points": "zero_fill_points",
            "line_broadening_hz": "line_broadening_hz",
            "normalization": "normalization",
            "truncation_window": "truncation_window",
            "baseline_polynomial_order": "baseline_order",
            "smoothing_window_ppm": "smoothing_window_ppm",
            "abd_sections": "abd_sections",
            "abd_noise_factor": "abd_noise_factor",
            "abd_window_points": "abd_window_points",
        },
        "regional_analysis": {
            "ppm_min": "region_min",
            "ppm_max": "region_max",
            "min_prominence_snr": "min_prominence_snr",
            "min_peak_distance_ppm": "min_peak_distance_ppm",
            "min_peak_width_ppm": "min_peak_width_ppm",
            "detection_trace": "detection_trace",
        },
        "peak_qc": {
            "min_snr": "qc_min_snr",
            "min_prominence_snr": "qc_min_prominence_snr",
            "min_width_hz": "qc_min_width_hz",
            "max_width_hz": "qc_max_width_hz",
            "require_positive_area": "qc_require_positive_area",
        },
        "simple_table": {
            "restrict_to_window": "simple_restrict_to_window",
            "target_ppm": "simple_target_ppm",
            "window_ppm": "simple_window_ppm",
        },
        "plots": {
            "display_min_ppm": "display_min",
            "display_max_ppm": "display_max",
        },
        "output": {
            "directory": "output_dir",
            "run_name": "run_name",
            "export_spectra_csv": "export_csv",
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
        if section_name == "plots":
            # Plot-only overlay settings are validated separately by
            # _flattened_overlay_config; tolerate them here.
            allowed.add("flattened_overlay")
        if section_name == "peak_qc":
            allowed.add("use_manual_thresholds")
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
        if section_name == "peak_qc":
            defaults["qc_manual_thresholds"] = bool(
                section.get("use_manual_thresholds", False)
            )
        if section_name == "peak_qc" and not section.get(
            "use_manual_thresholds", False
        ):
            # Switch is off: the validated built-in thresholds win and the
            # values below are ignored. They are still key-checked above, so a
            # typo is caught now rather than the first time it is switched on.
            continue
        for key, destination in mapping.items():
            if key in section:
                defaults[destination] = section[key]
    # argparse's type= is not applied to set_defaults values, so coerce the
    # ones that must not stay plain strings from YAML.
    if isinstance(defaults.get("output_dir"), str):
        defaults["output_dir"] = Path(defaults["output_dir"])
    paths = defaults.get("paths")
    if isinstance(paths, str):
        defaults["paths"] = [paths]
    elif paths is not None and not isinstance(paths, list):
        raise ConfigError(
            f"'paths' in [input] of {path} must be a string or a list of strings"
        )
    elif paths is not None:
        defaults["paths"] = [str(p) for p in paths]
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


def _plot_flattened_overlay(traces, path: Path, residual_config):
    """Plot authoritative real traces after conservative display-only flattening."""

    plt = _get_plotting()
    fig, ax = plt.subplots(figsize=(11.5, 6.3), dpi=180)
    colors = plt.get_cmap("viridis")
    flattened = []
    for index, trace in enumerate(traces):
        result = flatten_residual_baseline(
            trace["ppm"],
            trace["intensity"],
            config=residual_config,
            detected_peak_ppm=trace["detected_peak_ppm"],
        )
        color = colors(index / max(1, len(traces) - 1))
        ax.plot(
            result.ppm,
            result.after,
            color=color,
            linewidth=0.85,
            label=trace["label"],
        )
        flattened.append(trace | {"flattened": result})
    ax.set_xlim(
        max(float(max(trace["ppm"])) for trace in traces),
        min(float(min(trace["ppm"])) for trace in traces),
    )
    ax.axhline(0.0, color="#555555", linewidth=0.8)
    ax.set_title("Regional phase-corrected spectra — flattened baseline")
    ax.set_xlabel(r"$^1$H chemical shift (ppm)")
    ax.set_ylabel("Baseline-corrected real intensity")
    ax.grid(True, alpha=0.18)
    ax.legend(loc="best", fontsize=7, ncol=2)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path, flattened


def _representative_flattened_trace(flattened):
    """Prefer sequence 1030, otherwise the strongest trace near 5.785 ppm."""

    for trace in flattened:
        if "sequence-1030" in trace["label"].lower():
            return trace
    candidates = []
    for trace in flattened:
        result = trace["flattened"]
        mask = (result.ppm >= 5.70) & (result.ppm <= 5.90)
        height = float(max(result.before[mask])) if any(mask) else float("-inf")
        candidates.append((height, trace))
    return max(candidates, key=lambda item: item[0])[1]


def _plot_flattening_diagnostic(trace, path: Path):
    """Show the authoritative trace, fitted residual, and flattened trace."""

    plt = _get_plotting()
    result = trace["flattened"]
    fig, ax = plt.subplots(figsize=(11.5, 6.3), dpi=180)
    ax.plot(
        result.ppm,
        result.before,
        color="#1f77b4",
        linewidth=0.85,
        label="Before residual baseline flattening",
    )
    ax.plot(
        result.ppm,
        result.estimated_baseline,
        color="#d1495b",
        linestyle="--",
        linewidth=1.25,
        label="Estimated residual baseline",
    )
    ax.plot(
        result.ppm,
        result.after,
        color="#2a9d8f",
        linewidth=0.85,
        label="After residual baseline flattening",
    )
    ax.axvspan(
        5.70,
        5.90,
        color="#f4d35e",
        alpha=0.11,
        label="Protected 5.70–5.90 ppm region",
    )
    ax.axhline(0.0, color="#555555", linewidth=0.8)
    ax.set_xlim(6.5, 5.0)
    ax.set_title(f"Residual baseline diagnostic — {trace['label']}")
    ax.set_xlabel(r"$^1$H chemical shift (ppm)")
    ax.set_ylabel("Phase-corrected real intensity")
    ax.grid(True, alpha=0.18)
    ax.legend(loc="best", fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def _write_overlay_baseline_qc(flattened, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OVERLAY_BASELINE_QC_COLUMNS)
        writer.writeheader()
        for trace in flattened:
            writer.writerow(
                {
                    "file": trace["file"],
                    "sequence_label": trace["label"],
                    **trace["flattened"].metrics,
                }
            )
    return path


def _peak_qc(peak, width_hz: float, args) -> tuple[bool, str]:
    """Decide whether a detected feature is a real resonance.

    Peaks are found on the smoothed *magnitude* spectrum, where a broad
    baseline undulation can easily clear the prominence threshold. These gates
    are applied to the *quantitative real* spectrum instead, which is where a
    genuine resonance and a wobble actually differ:

    * ``snr`` -- height above the local baseline in units of robust noise. A
      baseline feature has a real-domain height near zero (or negative) even
      when its magnitude-domain prominence looks respectable.
    * ``prominence_snr`` -- how far the peak stands above its own flanking
      minima, so a bump riding on a broad shoulder is not credited with the
      shoulder's height.
    * linewidth -- a resonance has a physically plausible width. Narrower than
      the digital resolution means a spike or artefact; several times broader
      than the observed lines means baseline roll, not a peak.
    * positive area -- an absorption peak integrates positive. A negative area
      means the "peak" is a phasing or baseline artefact.

    Returns ``(passed, "; ".join(reasons))``.
    """
    reasons: list[str] = []
    if peak.snr < float(args.qc_min_snr):
        reasons.append(f"snr {peak.snr:.2f} < {float(args.qc_min_snr):g}")
    if peak.prominence_snr < float(args.qc_min_prominence_snr):
        reasons.append(
            f"prominence_snr {peak.prominence_snr:.2f} < "
            f"{float(args.qc_min_prominence_snr):g}"
        )
    if width_hz < float(args.qc_min_width_hz):
        reasons.append(
            f"width {width_hz:.2f} Hz < {float(args.qc_min_width_hz):g} Hz"
        )
    if width_hz > float(args.qc_max_width_hz):
        reasons.append(
            f"width {width_hz:.2f} Hz > {float(args.qc_max_width_hz):g} Hz"
        )
    if args.qc_require_positive_area and peak.positive_area <= 0:
        reasons.append(f"positive_area {peak.positive_area:.3f} <= 0")
    return (not reasons), "; ".join(reasons)


def _prefix_output_files(out_dir: Path) -> dict[str, str]:
    """Rename every file under *out_dir* to ``<run>_<original name>``.

    Output files get shared one at a time, and a bare ``peaks_simple.csv`` or
    ``run_qc.csv`` says nothing about which run produced it once it is out of
    its folder. Prefixing with the run name (the input folder's name) makes
    every file self-identifying.

    Applied as one pass after everything is written, so files produced by the
    statistics library are covered too. Returns ``{old: new}`` absolute paths
    so recorded provenance can be rewritten to match.
    """
    prefix = f"{out_dir.name}_"
    renamed: dict[str, str] = {}
    # Materialise the listing first: renaming while walking is undefined.
    for path in sorted(out_dir.rglob("*")):
        if not path.is_file() or path.name.startswith(prefix):
            continue
        target = path.with_name(prefix + path.name)
        path.rename(target)
        renamed[str(path)] = str(target)
    return renamed


def _remap_path(value, renamed: dict[str, str]):
    """Rewrite one recorded output path through the rename map."""
    if not value:
        return value
    return renamed.get(str(value), str(value))


SIMPLE_PEAK_COLUMNS = [
    "file", "timestamp", "peak_ppm", "integrated_area", "intensity",
    "snr", "prominence_snr", "width_hz",
]


def _write_simple_peaks(records, peak_rows, path: Path, args=None):
    """Minimal per-peak table: which file, where the peak is, how big it is.

    Ordered by acquisition timestamp rather than filename, and every processed
    file keeps a row even when no peak was detected -- zero-filled, so a gap in
    a time course plots as a real point instead of dropping out of the series.
    Values are the same baseline-corrected quantities written to ``peaks.csv``,
    only rounded for reading.

    Only QC-passing peaks are reported. A detection that failed the reality
    gates in :func:`_peak_qc` is noise, so it is zero-filled exactly like a
    non-detection rather than contributing a fictitious area to the series.
    ``peaks.csv`` still carries every detection with its failure reasons.

    With ``simple_table.restrict_to_window`` enabled the table is narrowed to a
    single tracked resonance: only peaks within ``window_ppm`` of
    ``target_ppm`` are eligible, and the strongest of those is the one row for
    that spectrum. A real peak elsewhere in the analysed region is legitimate
    and stays in ``peaks.csv``; it simply is not the peak this series is about.
    """
    restrict = bool(getattr(args, "simple_restrict_to_window", False))
    target_ppm = float(getattr(args, "simple_target_ppm", 5.8))
    window_ppm = abs(float(getattr(args, "simple_window_ppm", 0.5)))

    by_file: dict[str, list[dict]] = {}
    for row in peak_rows:
        if not row.get("qc_pass"):
            continue
        if restrict and abs(float(row["interpolated_ppm"]) - target_ppm) > window_ppm:
            continue
        by_file.setdefault(row["file"], []).append(row)
    if restrict:
        # One resonance, one row per spectrum: keep the strongest candidate in
        # the window rather than the nearest, so a small blip closer to the
        # nominal centre cannot displace the actual peak.
        by_file = {
            name: [max(rows, key=lambda r: float(r["snr"]))]
            for name, rows in by_file.items()
        }

    # A spectrum with no usable peak still belongs on the same trace, so hold it
    # at the ppm of the resonance being tracked (the most-observed family's
    # median) and report zero area/intensity.  Parking it at 0 ppm instead
    # would wreck the y-axis of any ppm-versus-time plot.
    by_family: dict[str, list[float]] = {}
    for rows in by_file.values():
        for row in rows:
            by_family.setdefault(row.get("peak_family_id") or "", []).append(
                float(row["interpolated_ppm"])
            )
    if by_family:
        tracked_ppm = float(statistics.median(max(by_family.values(), key=len)))
    elif restrict:
        # Nothing landed in the window anywhere: hold the trace at the ppm the
        # window is centred on rather than wherever the rejected peaks sat.
        tracked_ppm = target_ppm
    elif peak_rows:
        # Nothing passed QC anywhere. Fall back to where the rejected features
        # sat, which is still the right neighbourhood of the spectrum.
        tracked_ppm = float(
            statistics.median([float(r["interpolated_ppm"]) for r in peak_rows])
        )
    else:
        # Nothing was detected at all: hold the trace at the middle of the
        # analysed region so the placeholder is never a misleading 0 ppm.
        bounds = [
            (float(r["region_min_ppm"]), float(r["region_max_ppm"]))
            for r in records
            if r.get("region_min_ppm") is not None
        ]
        tracked_ppm = (
            float(statistics.median([0.5 * (lo + hi) for lo, hi in bounds]))
            if bounds
            else 0.0
        )

    def _acquisition_order(record):
        # ISO-8601 sorts correctly as text; undated files go last.
        timestamp = record.get("timestamp") or ""
        return (not timestamp, timestamp, record.get("file", ""))

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=SIMPLE_PEAK_COLUMNS)
        writer.writeheader()
        for record in sorted(records, key=_acquisition_order):
            name = record.get("file", "")
            peaks = sorted(
                by_file.get(name, []),
                key=lambda peak: peak["interpolated_ppm"],
                reverse=True,
            )
            if not peaks:
                writer.writerow(
                    {
                        "file": name,
                        "timestamp": record.get("timestamp", ""),
                        "peak_ppm": f"{tracked_ppm:.3f}",
                        "integrated_area": f"{0.0:.2f}",
                        "intensity": f"{0.0:.2f}",
                        "snr": f"{0.0:.2f}",
                        "prominence_snr": f"{0.0:.2f}",
                        "width_hz": f"{0.0:.2f}",
                    }
                )
                continue
            for peak in peaks:
                writer.writerow(
                    {
                        "file": name,
                        "timestamp": record.get("timestamp", ""),
                        "peak_ppm": f"{peak['interpolated_ppm']:.3f}",
                        "integrated_area": f"{peak['positive_area']:.2f}",
                        "intensity": f"{peak['height']:.2f}",
                        "snr": f"{peak['snr']:.2f}",
                        "prominence_snr": f"{peak['prominence_snr']:.2f}",
                        "width_hz": f"{peak['width_hz']:.2f}",
                    }
                )
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


def _run_statistics(stat_spectra_raw, assignments, stats_config, out_dir: Path) -> dict:
    """Build and write the optional statistics tables/plots for this run.

    Consumes the per-spectrum data collected during processing plus the peak
    family assignments, delegates every calculation to
    :mod:`chemyx_lab.analysis.statistics_report`, and writes the returned tables
    under ``<out_dir>/statistics/`` and plots under ``<out_dir>/plots/statistics/``.
    All heavy lifting lives in the library; this function is only glue + I/O.
    """
    import numpy as np

    from chemyx_lab.analysis.statistics_report import (
        PeakObservation,
        SpectrumStat,
        build_statistics_report,
    )
    from chemyx_lab.analysis.statistics_plots import render_plots

    spectra = []
    for index, raw in enumerate(stat_spectra_raw):
        picked = raw["picked"]
        family_ids = assignments[index] if index < len(assignments) else ()
        observe = float(raw["observe_frequency_mhz"] or 0.0)
        peaks = []
        for peak_number, peak in enumerate(picked.peaks, 1):
            fid = (
                family_ids[peak_number - 1]
                if peak_number - 1 < len(family_ids)
                else ""
            )
            peaks.append(
                PeakObservation(
                    peak_number=peak_number,
                    peak_family_id=fid,
                    center_ppm=peak.peak_ppm,
                    height=peak.peak_height,
                    width_ppm=peak.width_ppm,
                    width_hz=peak.width_ppm * observe,
                    signed_area=peak.signed_area,
                    positive_area=peak.positive_area,
                    snr=peak.snr,
                    prominence_snr=peak.prominence_snr,
                    classification=peak.classification,
                )
            )
        quant = picked.quantitative_corrected
        spectra.append(
            SpectrumStat(
                file=raw["file"],
                source_path=raw["source_path"],
                spectrum_index=index,
                timestamp=raw["timestamp"],
                observe_frequency_mhz=observe,
                region_noise=float(picked.noise),
                reference_shift_ppm=float(raw["reference_shift_ppm"]),
                reference_qc_pass=bool(raw["reference_qc_pass"]),
                phase0_deg=raw["phase0_deg"],
                phase1_deg=raw["phase1_deg"],
                peaks=peaks,
                region_ppm=np.asarray(picked.ppm_axis, dtype=float),
                region_quant_corrected=(
                    np.asarray(quant, dtype=float) if quant is not None else None
                ),
                full_ppm=np.asarray(raw["full_ppm"], dtype=float),
                full_intensity=np.asarray(raw["full_intensity"], dtype=float),
            )
        )

    report = build_statistics_report(spectra, stats_config)

    stats_dir = out_dir / "statistics"
    stats_dir.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for filename, (columns, rows) in report.tables.items():
        path = stats_dir / filename
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        written.append(str(path))

    plots_written = render_plots(report, out_dir / "plots")

    (stats_dir / "statistics_summary.json").write_text(
        json.dumps(
            {
                "provenance": report.provenance,
                "warnings": report.warnings,
                "tables": [Path(p).name for p in written],
                "plots": [Path(p).name for p in plots_written],
            },
            indent=2,
            sort_keys=True,
            default=str,
        ),
        encoding="utf-8",
    )
    return {
        "tables_written": written,
        "plots_written": plots_written,
        "warnings": report.warnings,
        "provenance": report.provenance,
    }


def main(argv: list[str] | None = None) -> int:
    effective_argv = sys.argv[1:] if argv is None else argv
    try:
        defaults = _config_defaults(effective_argv)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    args = _parser(defaults).parse_args(effective_argv)
    if not args.paths:
        print(
            "ERROR: no input given. Pass a .dx file or directory on the command "
            "line, or set input.paths in the config file "
            f"({DEFAULT_CONFIG_PATH}).",
            file=sys.stderr,
        )
        return 2
    try:
        stats_config = _statistics_config(effective_argv)
        flattened_overlay_config = _flattened_overlay_config(effective_argv)
    except (
        ConfigError,
        StatisticsConfigError,
        FlattenedOverlayConfigError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    from dataclasses import replace

    if args.bootstrap_iterations is not None:
        stats_config = replace(
            stats_config,
            bootstrap=replace(
                stats_config.bootstrap, iterations=int(args.bootstrap_iterations)
            ),
        )
    statistics_enabled = bool(args.statistics or stats_config.enabled)
    # Keep the recorded provenance consistent when enabled via the CLI flag.
    stats_config = replace(stats_config, enabled=statistics_enabled)
    files = collect_dx_files(args.paths)
    if not files:
        print("ERROR: no .dx files found.", file=sys.stderr)
        return 1
    # State the gates in force: silently running on the wrong thresholds is the
    # expensive mistake here, not a line of output.
    print(
        f"  peak QC ({'manual' if args.qc_manual_thresholds else 'default'}): "
        f"snr>={args.qc_min_snr:g}, prominence_snr>={args.qc_min_prominence_snr:g}, "
        f"width {args.qc_min_width_hz:g}-{args.qc_max_width_hz:g} Hz, "
        f"positive_area={bool(args.qc_require_positive_area)}; "
        f"detection on {args.detection_trace} trace"
    )
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
    authoritative_overlay_traces: list[dict] = []
    # Per-spectrum inputs for the optional statistics pipeline, appended in
    # lock-step with plot_results so their order matches the family assignment.
    stat_spectra_raw: list[dict] = []

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
                # "abd-linear" keeps the conservative straight-line baseline;
                # "polynomial" follows the curved solvent tail (e.g. toluene's
                # wing under the ~5.8 peak) with the configured order so the
                # displayed baseline sits near zero instead of bowing negative.
                display_order = 1 if args.baseline_method == "abd-linear" else args.baseline_order
                quantitative_real, _, _, _ = subtract_abd_polynomial_baseline(
                    spectrum.real,
                    sections=args.abd_sections,
                    noise_factor=args.abd_noise_factor,
                    window_points=args.abd_window_points,
                    polynomial_order=display_order,
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
            # Which trace peaks are FOUND on. The magnitude spectrum is always
            # positive and never needs phasing, but a real resonance can sit on
            # its rising background as a mere shoulder -- so peaks get missed
            # outright, or located on the flank instead of the apex, which then
            # corrupts every height, width, and area derived from them. The
            # phased, baseline-corrected real trace is where a peak actually
            # looks like a peak, so detection defaults to it.
            detection_trace = (
                diagnostic_magnitude
                if args.detection_trace == "magnitude"
                else quantitative_real
            )
            picked = pick_spectrum_region(
                analysis_ppm,
                detection_trace,
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
                width_hz = peak.width_ppm * spectrum.observe_frequency_mhz
                qc_pass, qc_reasons = _peak_qc(peak, width_hz, args)
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
                        "prominence_snr": peak.prominence_snr,
                        "snr": peak.snr,
                        "width_ppm": peak.width_ppm,
                        "width_hz": width_hz,
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
                        "qc_pass": qc_pass,
                        "qc_failure_reasons": qc_reasons,
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
            regional_ppm, regional_real = regional_authoritative_trace(
                analysis_ppm,
                quantitative_real,
                args.region_min,
                args.region_max,
            )
            authoritative_overlay_traces.append(
                {
                    "file": path.name,
                    "label": path.stem,
                    "ppm": regional_ppm,
                    "intensity": regional_real,
                    "detected_peak_ppm": positions,
                }
            )
            if statistics_enabled:
                stat_spectra_raw.append(
                    {
                        "file": path.name,
                        "source_path": str(path),
                        "timestamp": timestamp,
                        "observe_frequency_mhz": spectrum.observe_frequency_mhz,
                        "region_noise": picked.noise,
                        "reference_shift_ppm": reference_shift,
                        "reference_qc_pass": reference_qc_pass,
                        "phase0_deg": spectrum.phase0_deg,
                        "phase1_deg": spectrum.phase1_deg,
                        "picked": picked,
                        "full_ppm": analysis_ppm,
                        "full_intensity": quantitative_real,
                    }
                )
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
    flattened_overlay = flattened_diagnostic = flattened_qc = ""
    family_rows: list[dict] = []
    statistics_info: dict | None = None
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
        if (
            flattened_overlay_config.enabled
            and authoritative_overlay_traces
        ):
            flattened_path, flattened_results = _plot_flattened_overlay(
                authoritative_overlay_traces,
                out_dir / "plots" / "overlay_region_baseline_flattened.png",
                flattened_overlay_config.residual_baseline,
            )
            flattened_overlay = str(flattened_path)
            representative = _representative_flattened_trace(flattened_results)
            flattened_diagnostic = str(
                _plot_flattening_diagnostic(
                    representative,
                    out_dir
                    / "plots"
                    / "overlay_baseline_diagnostic_sequence_1030.png",
                )
            )
            flattened_qc = str(
                _write_overlay_baseline_qc(
                    flattened_results,
                    out_dir / "statistics" / "overlay_baseline_qc.csv",
                )
            )
        if statistics_enabled and stat_spectra_raw:
            try:
                statistics_info = _run_statistics(
                    stat_spectra_raw, assignments, stats_config, out_dir
                )
                n_tables = len(statistics_info["tables_written"])
                print(
                    f"  statistics: wrote {n_tables} table(s) and "
                    f"{len(statistics_info['plots_written'])} plot(s) to "
                    f"{out_dir / 'statistics'}"
                )
                for warning in statistics_info["warnings"]:
                    print(f"    statistics warning: {warning}", file=sys.stderr)
            except Exception as exc:  # never fail the core run on statistics
                print(f"  WARNING: statistics pipeline failed: {exc}", file=sys.stderr)
                statistics_info = {"error": str(exc), "warnings": [str(exc)]}

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
        "integration_left_ppm", "integration_right_ppm", "prominence",
        "prominence_snr", "snr",
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

    simple_peaks = str(
        _write_simple_peaks(records, peak_rows, out_dir / "peaks_simple.csv", args)
    )

    # Every artefact is on disk by now, so one rename pass makes them all
    # self-identifying; recorded paths are rewritten to match.
    renamed = _prefix_output_files(out_dir)
    for row in records:
        for key in ("full_plot", "region_plot", "spectrum_csv"):
            if key in row:
                row[key] = _remap_path(row[key], renamed)
    overlay = _remap_path(overlay, renamed)
    stacked = _remap_path(stacked, renamed)
    flattened_overlay = _remap_path(flattened_overlay, renamed)
    flattened_diagnostic = _remap_path(flattened_diagnostic, renamed)
    flattened_qc = _remap_path(flattened_qc, renamed)
    simple_peaks = _remap_path(simple_peaks, renamed)
    if statistics_info is not None:
        for key in ("tables_written", "plots_written"):
            if key in statistics_info:
                statistics_info[key] = [
                    _remap_path(p, renamed) for p in statistics_info[key]
                ]

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
            (
                "plot-only robust linear residual flattening of the authoritative "
                "baseline-corrected real trace, with peak/exclusion masks"
            ),
        ],
        "overlay_region_corrected": overlay,
        "stacked_region_corrected": stacked,
        "overlay_region_baseline_flattened": flattened_overlay,
        "overlay_baseline_diagnostic": flattened_diagnostic,
        "overlay_baseline_qc": flattened_qc,
        "simple_peaks": simple_peaks,
        "flattened_overlay_source_array": (
            "quantitative_real: phase-corrected real spectrum after reference/"
            "alignment and configured primary baseline correction; identical "
            "array passed to individual full and regional plots"
        ),
        "records": records,
    }
    if statistics_info is not None:
        stats_block = {"enabled": True, **statistics_info.get("provenance", {})}
        stats_block["warnings"] = statistics_info.get("warnings", [])
        stats_block["tables"] = [
            Path(p).name for p in statistics_info.get("tables_written", [])
        ]
        stats_block["plots"] = [
            Path(p).name for p in statistics_info.get("plots_written", [])
        ]
        if "error" in statistics_info:
            stats_block["error"] = statistics_info["error"]
        summary["statistics"] = stats_block
    else:
        summary["statistics"] = {"enabled": statistics_enabled}
    (out_dir / f"{out_dir.name}_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str), encoding="utf-8"
    )
    print(f"Output: {out_dir}")
    return 1 if summary["files_succeeded"] == 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())
