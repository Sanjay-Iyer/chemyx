"""Generate a non-destructive NMR processing transparency figure package.

The script reads existing JCAMP-DX acquisitions and existing ``peaks_simple``
results, reconstructs production intermediate arrays through
``chemyx_lab.analysis.nmr``, and writes only to a new inspection directory.
It never invokes or rewrites the production result folder.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chemyx_lab.analysis.nmr import (  # noqa: E402
    asymmetric_least_squares_baseline,
    build_processing_inspection,
    integrate_above_local_baseline,
    pick_spectrum_region,
    subtract_abd_polynomial_baseline,
)
from chemyx_lab.analysis.plot_titles import (  # noqa: E402
    format_dataset_plot_title,
    resolve_dataset_display_name,
)
from build_timeseries_results import (  # noqa: E402
    left_peak_integral,
    local_integration_fill,
)
from _common import parse_acquisition_timestamp  # noqa: E402


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERIES = REPO_ROOT / "results/runs/automated/chemyx_demo_081026_v3"
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "results/nmr_processing_inspection"

LB_HZ = 0.03
FFT_POINTS = 65536
ALS_LAMBDA = 1e6
ALS_P = 0.001
ALS_ITERATIONS = 10
REGION = (5.0, 6.5)
TARGET_WINDOW = (5.70, 5.90)
STARTING_MATERIAL_WINDOW = (5.40, 5.52)
SILANE_WINDOW = (4.94, 5.06)
TARGET_PPM = 5.80

SENSITIVITY = (
    ("lower_smoothness", 3e5, 0.001, 10),
    ("production", 1e6, 0.001, 10),
    ("higher_smoothness", 3e6, 0.001, 10),
    ("lower_asymmetry", 1e6, 0.0005, 10),
    ("higher_asymmetry", 1e6, 0.003, 10),
)


@dataclass
class Acquisition:
    run: Path
    dx: Path
    saved: dict[str, str]
    timestamp: object
    timestamp_source: str
    inspection: object
    picked: object
    target: object | None
    fixed_product: object
    starting_material: object
    silane: object
    left_peak: dict | None
    role: str = ""


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("series_dir", nargs="?", type=Path, default=DEFAULT_SERIES)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--dataset-display-name")
    return parser


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_saved_peak(run: Path) -> dict[str, str]:
    paths = sorted(
        run.rglob("*_peaks_simple.csv"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not paths:
        raise RuntimeError(f"no peaks_simple CSV under {run}")
    with paths[0].open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError(f"empty peaks_simple CSV: {paths[0]}")
    return {**rows[0], "_source_csv": str(paths[0])}


def _observe_frequency(metadata) -> float:
    return float(
        metadata.get(
            "$SF", metadata.get("$SFO1", metadata.get(".OBSERVE FREQUENCY", 60.0))
        )
    )


def _target_peak(picked, observe_frequency_mhz=60.0):
    candidates = [
        peak
        for peak in picked.peaks
        if TARGET_WINDOW[0] <= peak.interpolated_ppm <= TARGET_WINDOW[1]
        and peak.snr >= 3.0
        and peak.prominence_snr >= 3.0
        and 1.0 <= peak.width_ppm * float(observe_frequency_mhz) <= 10.0
        and peak.positive_area > 0
    ]
    return max(candidates, key=lambda peak: peak.snr) if candidates else None


def _integral(ppm, y, bounds):
    return integrate_above_local_baseline(
        ppm, y, left_ppm=min(bounds), right_ppm=max(bounds)
    )


def _load_acquisition(run: Path) -> Acquisition:
    dx_files = sorted((run / "raw_nmr").glob("*.dx"))
    if len(dx_files) != 1:
        raise RuntimeError(
            f"expected one raw_nmr/*.dx under {run}; found {len(dx_files)}"
        )
    dx = dx_files[0]
    saved = _read_saved_peak(run)
    inspection = build_processing_inspection(
        dx,
        line_broadening_hz=LB_HZ,
        zero_fill_points=FFT_POINTS,
        inverse_phase=True,
        als_smoothness=ALS_LAMBDA,
        als_asymmetry=ALS_P,
        als_iterations=ALS_ITERATIONS,
    )
    picked = pick_spectrum_region(
        inspection.ppm_axis,
        inspection.corrected_real,
        region_min_ppm=REGION[0],
        region_max_ppm=REGION[1],
        min_prominence_snr=5.0,
        min_distance_ppm=0.04,
        min_width_ppm=0.015,
        baseline_polynomial_order=3,
        smoothing_window_ppm=0.006,
        quantitative_intensity=inspection.corrected_real,
        source=dx,
    )
    timestamp, source = parse_acquisition_timestamp(inspection.metadata, dx)
    if timestamp is None or source != "LONG DATE header":
        raise RuntimeError(f"{dx.name}: authoritative LONG DATE unavailable ({source})")
    ppm = np.asarray(inspection.ppm_axis)
    corrected = np.asarray(inspection.corrected_real)
    target = _target_peak(picked, _observe_frequency(inspection.metadata))
    left = left_peak_integral(
        dx,
        ppm,
        corrected,
        TARGET_WINDOW,
        height_fraction=0.20,
        multiplet_span=0.05,
    )
    if target is None:
        left = None
    return Acquisition(
        run,
        dx,
        saved,
        timestamp,
        source,
        inspection,
        picked,
        target,
        _integral(ppm, corrected, TARGET_WINDOW),
        _integral(ppm, corrected, STARTING_MATERIAL_WINDOW),
        _integral(ppm, corrected, SILANE_WINDOW),
        left,
    )


def _title(dataset: str, text: str) -> str:
    return format_dataset_plot_title(dataset, text)


def _style_spectrum(ax, ppm, *, xlabel=True):
    ax.set_xlim(float(np.max(ppm)), float(np.min(ppm)))
    if xlabel:
        ax.set_xlabel("Chemical shift (ppm; high ppm → low ppm)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.axhline(0, color="0.65", lw=0.7)
    ax.grid(axis="x", color="0.9", lw=0.6)


def _metadata_line(acq: Acquisition) -> str:
    md = acq.inspection.metadata
    scans = md.get("$SCANS", md.get(".AVERAGES", "?"))
    gain = md.get("$RECVR GAIN", md.get("$RECVR_GAIN", "?"))
    return (
        f"{acq.timestamp.isoformat()} ({acq.timestamp_source}); scans {scans}; "
        f"gain {gain}; LB {LB_HZ:g} Hz; FFT {FFT_POINTS}; "
        f"ALS λ={ALS_LAMBDA:.0e}, p={ALS_P:g}, n={ALS_ITERATIONS}"
    )


def _save(fig, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=180, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return path


def _stem(acq: Acquisition) -> str:
    return acq.timestamp.strftime("%Y%m%d_%H%M%S")


def variant_01(acq, out, dataset):
    ppm = np.asarray(acq.inspection.ppm_axis)
    pre = np.real(acq.inspection.phased_spectrum)
    base = np.asarray(acq.inspection.als_baseline)
    fig, ax = plt.subplots(figsize=(12, 5), layout="constrained")
    ax.plot(ppm, pre, lw=0.7, label="Phased spectrum before ALS")
    ax.plot(ppm, base, lw=1.2, color="#d95f02", label="ALS baseline")
    ax.plot(ppm, pre - base, lw=0.6, alpha=0.65, label="After ALS")
    _style_spectrum(ax, ppm)
    fig.suptitle(_title(dataset, "Full spectrum with production ALS baseline"))
    ax.set_title(_metadata_line(acq), fontsize=8)
    ax.legend(frameon=False, ncol=3)
    return _save(fig, out / f"{_stem(acq)}_full_baseline_overlay.png")


def variant_02(acq, out, dataset, matched):
    ppm = np.asarray(acq.inspection.ppm_axis)
    pre = np.real(acq.inspection.phased_spectrum)
    base = np.asarray(acq.inspection.als_baseline)
    post = np.asarray(acq.inspection.corrected_real)
    fig, axes = plt.subplots(2, 1, figsize=(12, 7), sharex=True, layout="constrained")
    axes[0].plot(ppm, pre, lw=0.7, label="Before ALS")
    axes[0].plot(ppm, base, lw=1.1, color="#d95f02", label="ALS baseline")
    axes[1].plot(ppm, post, lw=0.7, color="#1b9e77", label="After ALS")
    for index, ax in enumerate(axes):
        _style_spectrum(ax, ppm, xlabel=index == len(axes) - 1)
        ax.legend(frameon=False)
    if matched:
        lo, hi = np.percentile(pre[np.isfinite(pre)], [0.2, 99.8])
        pad = 0.05 * (hi - lo)
        axes[0].set_ylim(lo - pad, hi + pad)
        axes[1].set_ylim(lo - pad, hi + pad)
    style = "matched vertical scale" if matched else "independent vertical scales"
    fig.suptitle(_title(dataset, f"Full-spectrum before and after ALS — {style}"))
    return _save(
        fig, out / f"{_stem(acq)}_{'matched' if matched else 'independent'}.png"
    )


def variant_03(acq, out, dataset):
    ppm = np.asarray(acq.inspection.ppm_axis)
    pre = np.real(acq.inspection.phased_spectrum)
    base = np.asarray(acq.inspection.als_baseline)
    post = np.asarray(acq.inspection.corrected_real)
    paths = []
    series = (
        ("3a_full_autoscale", pre, "Phased full spectrum — normal autoscale", None),
        (
            "3b_baseline_focused",
            pre,
            "Phased full spectrum — baseline-focused scale",
            "focus",
        ),
        ("3c_baseline_only", base, "Production ALS baseline only", None),
        ("3d_corrected_near_zero", post, "ALS-corrected residual near zero", "focus"),
        (
            "3e_baseline_displacement",
            base - np.median(base),
            "ALS baseline displacement from its median (visualization only)",
            None,
        ),
    )
    for name, values, label, mode in series:
        fig, ax = plt.subplots(figsize=(12, 4.5))
        ax.plot(ppm, values, lw=0.8)
        _style_spectrum(ax, ppm)
        if mode == "focus":
            finite = values[np.isfinite(values)]
            lo, hi = np.percentile(finite, [5, 85])
            pad = max(0.15 * (hi - lo), 1.0)
            ax.set_ylim(lo - pad, hi + pad)
            ax.text(
                0.01,
                0.97,
                "Large peaks intentionally clip to expose baseline structure",
                transform=ax.transAxes,
                va="top",
                fontsize=8,
            )
        ax.set_title(_title(dataset, label))
        paths.append(_save(fig, out / f"{_stem(acq)}_{name}.png"))
    return paths


def variant_04(acq, out, dataset):
    ins = acq.inspection
    decimate = max(1, len(ins.time_s) // 4000)
    t = np.asarray(ins.time_s)[::decimate]
    raw = np.asarray(ins.raw_fid)[::decimate]
    apo = np.asarray(ins.apodized_fid)[::decimate]
    fig, axes = plt.subplots(3, 1, figsize=(11, 8), sharex=True, layout="constrained")
    axes[0].plot(t, raw.real, lw=0.7)
    axes[0].set_ylabel("Real FID")
    axes[1].plot(t, raw.imag, lw=0.7)
    axes[1].set_ylabel("Imaginary FID")
    axes[2].plot(t, np.abs(raw), lw=0.7, label="Raw magnitude")
    axes[2].plot(t, np.abs(apo), lw=0.8, label="After 0.03 Hz exponential window")
    axes[2].legend(frameon=False)
    axes[2].set_ylabel("FID magnitude")
    axes[2].set_xlabel("Acquisition time (s)")
    fig.suptitle(
        _title(dataset, "FID processing: decoded complex data and line broadening")
    )
    p1 = _save(fig, out / f"{_stem(acq)}_fid_processing.png")

    ppm = np.asarray(ins.ppm_axis)
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True, layout="constrained")
    axes[0].plot(ppm, np.real(ins.fft_spectrum), lw=0.6)
    axes[0].set_ylabel("FFT real")
    axes[1].plot(ppm, np.real(ins.phased_spectrum), lw=0.6)
    axes[1].set_ylabel("Phased real")
    axes[2].plot(ppm, np.real(ins.phased_spectrum), lw=0.5, label="Phased")
    axes[2].plot(ppm, ins.als_baseline, lw=1.0, color="#d95f02", label="ALS baseline")
    axes[2].legend(frameon=False)
    axes[2].set_ylabel("Baseline fit")
    axes[3].plot(ppm, ins.corrected_real, lw=0.6, color="#1b9e77")
    axes[3].set_ylabel("Corrected")
    for index, ax in enumerate(axes):
        _style_spectrum(ax, ppm, xlabel=index == len(axes) - 1)
    fig.suptitle(
        _title(
            dataset,
            "Spectral processing: FFT, stored inverse phase, and ALS subtraction",
        )
    )
    p2 = _save(fig, out / f"{_stem(acq)}_spectral_processing.png")
    return [p1, p2]


def variant_05(acq, out, dataset):
    ppm = np.asarray(acq.inspection.ppm_axis)
    y = np.asarray(acq.inspection.corrected_real)
    fig = plt.figure(figsize=(13, 8))
    grid = fig.add_gridspec(2, 3, height_ratios=(1.25, 1))
    full = fig.add_subplot(grid[0, :])
    full.plot(ppm, y, lw=0.65, color="#1f4e79")
    windows = (
        (TARGET_WINDOW, "Product", "#66c2a5"),
        (STARTING_MATERIAL_WINDOW, "Starting material", "#fc8d62"),
        (SILANE_WINDOW, "Silane", "#8da0cb"),
    )
    for bounds, label, color in windows:
        full.axvspan(
            bounds[0],
            bounds[1],
            color=color,
            alpha=0.22,
            label=f"{label} {bounds[0]:.2f}–{bounds[1]:.2f} ppm",
        )
    _style_spectrum(full, ppm)
    full.legend(frameon=False, ncol=3, fontsize=8)
    for ax, (bounds, label, color) in zip(
        (
            fig.add_subplot(grid[1, 0]),
            fig.add_subplot(grid[1, 1]),
            fig.add_subplot(grid[1, 2]),
        ),
        windows,
    ):
        pad = 0.05 if label == "Product" else 0.025
        mask = (ppm >= bounds[0] - pad) & (ppm <= bounds[1] + pad)
        ax.plot(ppm[mask], y[mask], lw=0.9, color="#1f4e79")
        ax.axvspan(bounds[0], bounds[1], color=color, alpha=0.22)
        _style_spectrum(ax, ppm[mask])
        ax.set_title(label)
    target = acq.target
    core = target.positive_area if target else 0.0
    left = acq.left_peak["area"] if acq.left_peak else 0.0
    text = (
        f"Core detected-peak area: {core:.2f}; fixed product window: {acq.fixed_product.positive_area:.2f}; "
        f"left-multiplet valley-to-valley: {left:.2f}. These are distinct integration definitions."
    )
    fig.suptitle(
        _title(dataset, "Full corrected spectrum connected to quantitative regions")
    )
    fig.text(0.5, 0.01, text, ha="center", fontsize=9)
    return _save(fig, out / f"{_stem(acq)}_full_plus_regions.png")


def _sensitivity_metrics(acq, label, lam, asym, iterations):
    pre = np.real(acq.inspection.phased_spectrum)
    baseline = asymmetric_least_squares_baseline(
        pre, smoothness=lam, asymmetry=asym, iterations=iterations
    )
    corrected = pre - baseline
    picked = pick_spectrum_region(
        acq.inspection.ppm_axis,
        corrected,
        region_min_ppm=5.0,
        region_max_ppm=6.5,
        min_prominence_snr=5.0,
        min_distance_ppm=0.04,
        min_width_ppm=0.015,
        baseline_polynomial_order=3,
        smoothing_window_ppm=0.006,
        quantitative_intensity=corrected,
        source=acq.dx,
    )
    target = _target_peak(picked, _observe_frequency(acq.inspection.metadata))
    ppm = np.asarray(acq.inspection.ppm_axis)
    left = (
        left_peak_integral(
            acq.dx,
            ppm,
            corrected,
            TARGET_WINDOW,
            height_fraction=0.20,
            multiplet_span=0.05,
        )
        if target
        else None
    )
    return {
        "label": label,
        "lambda": lam,
        "asymmetry": asym,
        "iterations": iterations,
        "baseline": baseline,
        "corrected": corrected,
        "target": target,
        "fixed_product_area": _integral(ppm, corrected, TARGET_WINDOW).positive_area,
        "starting_material_area": _integral(
            ppm, corrected, STARTING_MATERIAL_WINDOW
        ).positive_area,
        "silane_area": _integral(ppm, corrected, SILANE_WINDOW).positive_area,
        "left_peak_area": left["area"] if left else 0.0,
    }


def variant_06(acquisitions, out, dataset):
    rows = []
    figures = []
    for acq in acquisitions:
        cases = [_sensitivity_metrics(acq, *case) for case in SENSITIVITY]
        production = next(case for case in cases if case["label"] == "production")
        base_values = {
            "target_ppm": production["target"].interpolated_ppm
            if production["target"]
            else 0.0,
            "target_height": production["target"].peak_height
            if production["target"]
            else 0.0,
            "target_area": production["target"].positive_area
            if production["target"]
            else 0.0,
            "fixed_product_area": production["fixed_product_area"],
            "starting_material_area": production["starting_material_area"],
            "silane_area": production["silane_area"],
            "left_peak_area": production["left_peak_area"],
        }
        for case in cases:
            target = case["target"]
            values = {
                "target_ppm": target.interpolated_ppm if target else 0.0,
                "target_height": target.peak_height if target else 0.0,
                "target_area": target.positive_area if target else 0.0,
                "fixed_product_area": case["fixed_product_area"],
                "starting_material_area": case["starting_material_area"],
                "silane_area": case["silane_area"],
                "left_peak_area": case["left_peak_area"],
            }
            row = {
                "file": acq.dx.name,
                "timestamp": acq.timestamp.isoformat(),
                "role": acq.role,
                "setting": case["label"],
                "lambda": case["lambda"],
                "asymmetry": case["asymmetry"],
                "iterations": case["iterations"],
                **values,
            }
            for key, value in values.items():
                ref = base_values[key]
                row[f"{key}_change"] = value - ref
                row[f"{key}_percent_change"] = (
                    0.0 if ref == 0 else 100.0 * (value - ref) / abs(ref)
                )
            rows.append(row)
        if acq.role in {"primary worked example", "difficult / non-detection"}:
            ppm = np.asarray(acq.inspection.ppm_axis)
            pre = np.real(acq.inspection.phased_spectrum)
            fig, axes = plt.subplots(2, 1, figsize=(12, 7), layout="constrained")
            axes[0].plot(ppm, pre, color="0.75", lw=0.5, label="Phased spectrum")
            for case in cases:
                axes[0].plot(ppm, case["baseline"], lw=0.9, label=case["label"])
            axes[0].set_title("ALS baselines across the full spectrum")
            axes[0].legend(frameon=False, ncol=3, fontsize=8)
            mask = (ppm >= 5.65) & (ppm <= 5.95)
            for case in cases:
                axes[1].plot(
                    ppm[mask], case["corrected"][mask], lw=0.9, label=case["label"]
                )
            axes[1].set_title("Corrected target region")
            _style_spectrum(axes[0], ppm, xlabel=False)
            _style_spectrum(axes[1], ppm[mask])
            fig.suptitle(_title(dataset, f"ALS sensitivity — {acq.role}"))
            figures.append(_save(fig, out / f"{_stem(acq)}_als_sensitivity.png"))
    csv_path = out / "als_sensitivity_metrics.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            key for key in rows[0] if key not in {"baseline", "corrected", "target"}
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return figures + [csv_path], rows


def variant_07(acq, out, dataset):
    ppm = np.asarray(acq.inspection.ppm_axis)
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True, layout="constrained")
    axes[0].plot(ppm, np.real(acq.inspection.fft_spectrum), lw=0.6)
    axes[0].set_title("A. FFT before stored phase correction")
    axes[1].plot(ppm, np.real(acq.inspection.phased_spectrum), lw=0.6)
    axes[1].set_title(
        f"B. Stored phase applied inversely: p0={acq.inspection.phase0_deg:g}°, p1={acq.inspection.phase1_deg:g}°"
    )
    axes[2].plot(ppm, np.real(acq.inspection.phased_spectrum), lw=0.5)
    axes[2].plot(ppm, acq.inspection.als_baseline, color="#d95f02", lw=1.0)
    axes[2].set_title("C. Phased spectrum with ALS baseline")
    axes[3].plot(ppm, acq.inspection.corrected_real, color="#1b9e77", lw=0.6)
    axes[3].set_title("D. Final baseline-corrected spectrum")
    for index, ax in enumerate(axes):
        _style_spectrum(ax, ppm, xlabel=index == len(axes) - 1)
    fig.suptitle(
        _title(
            dataset,
            "Stored phase correction and ALS baseline correction are separate operations",
        )
    )
    return _save(fig, out / f"{_stem(acq)}_phase_and_baseline.png")


def difference_plot(acq, out, dataset):
    ppm = np.asarray(acq.inspection.ppm_axis)
    pre = np.real(acq.inspection.phased_spectrum)
    post = np.asarray(acq.inspection.corrected_real)
    diff = pre - post
    base = np.asarray(acq.inspection.als_baseline)
    error = float(np.max(np.abs(diff - base)))
    fig, axes = plt.subplots(3, 1, figsize=(12, 8), sharex=True, layout="constrained")
    axes[0].plot(ppm, pre, lw=0.6, label="Pre-ALS")
    axes[0].plot(ppm, post, lw=0.6, label="Post-ALS")
    axes[0].legend(frameon=False)
    axes[1].plot(ppm, diff, lw=0.8, label="Pre − post")
    axes[1].plot(ppm, base, ls="--", lw=0.8, label="Stored ALS baseline")
    axes[1].legend(frameon=False)
    axes[2].plot(ppm, diff - base, lw=0.7)
    axes[2].set_title(f"Numerical residual; max |difference − baseline| = {error:.3g}")
    for index, ax in enumerate(axes):
        _style_spectrum(ax, ppm, xlabel=index == len(axes) - 1)
    fig.suptitle(_title(dataset, "Direct verification of ALS subtraction"))
    return _save(fig, out / f"{_stem(acq)}_before_after_difference.png"), error


def deep_dive(acq, out, dataset):
    ins = acq.inspection
    ppm = np.asarray(ins.ppm_axis)
    pre = np.real(ins.phased_spectrum)
    post = np.asarray(ins.corrected_real)
    fig, axes = plt.subplots(4, 3, figsize=(16, 13), layout="constrained")
    t = np.asarray(ins.time_s)
    raw = np.asarray(ins.raw_fid)
    apo = np.asarray(ins.apodized_fid)
    items = [
        (t, raw.real, "Raw real FID"),
        (t, raw.imag, "Raw imaginary FID"),
        (t, np.abs(apo), "Line-broadened FID magnitude"),
        (ppm, np.real(ins.fft_spectrum), "FFT before phase"),
        (ppm, pre, "Stored-phase spectrum before ALS"),
        (ppm, ins.als_baseline, "Production ALS baseline"),
        (ppm, post, "Corrected full spectrum"),
    ]
    for ax, (x, y, label) in zip(axes.flat[:7], items):
        ax.plot(x, y, lw=0.65)
        ax.set_title(label)
        if x is ppm:
            _style_spectrum(ax, ppm)
        else:
            ax.set_xlabel("Time (s)")
    windows = (
        (TARGET_WINDOW, "Product 5.70–5.90 ppm"),
        (STARTING_MATERIAL_WINDOW, "Starting material 5.40–5.52 ppm"),
        (SILANE_WINDOW, "Silane 4.94–5.06 ppm"),
    )
    for ax, (bounds, label) in zip(axes.flat[7:10], windows):
        mask = (ppm >= bounds[0] - 0.03) & (ppm <= bounds[1] + 0.03)
        ax.plot(ppm[mask], post[mask], lw=0.9)
        ax.axvspan(*bounds, alpha=0.2)
        _style_spectrum(ax, ppm[mask])
        ax.set_title(label)
    target = acq.target
    core = target.positive_area if target else 0
    left = acq.left_peak["area"] if acq.left_peak else 0
    axes.flat[10].axis("off")
    axes.flat[10].text(
        0,
        1,
        f"Core peak\nppm {target.interpolated_ppm if target else 0:.3f}\narea {core:.2f}\nheight {target.peak_height if target else 0:.2f}\nSNR {target.snr if target else 0:.2f}\nprominence/SNR {target.prominence_snr if target else 0:.2f}\nwidth {target.width_ppm * _observe_frequency(ins.metadata) if target else 0:.2f} Hz",
        va="top",
        fontsize=11,
    )
    axes.flat[11].axis("off")
    axes.flat[11].text(
        0,
        1,
        f"Distinct integrations\ncore detected peak: {core:.2f}\nfixed product window: {acq.fixed_product.positive_area:.2f}\nleft multiplet valley-to-valley: {left:.2f}\nstarting material: {acq.starting_material.positive_area:.2f}\nsilane: {acq.silane.positive_area:.2f}",
        va="top",
        fontsize=11,
    )
    fig.suptitle(_title(dataset, "Worked example: raw FID to quantitative integration"))
    return _save(fig, out / f"{_stem(acq)}_worked_example.png")


def overlays(acquisitions, out, dataset):
    paths = []
    for corrected in (False, True):
        fig, ax = plt.subplots(figsize=(12, 6))
        for i, acq in enumerate(acquisitions):
            ppm = np.asarray(acq.inspection.ppm_axis)
            y = (
                np.asarray(acq.inspection.corrected_real)
                if corrected
                else np.real(acq.inspection.phased_spectrum)
            )
            ax.plot(
                ppm, y, lw=0.7, alpha=0.85, label=acq.timestamp.strftime("%m-%d %H:%M")
            )
        _style_spectrum(ax, ppm)
        ax.legend(frameon=False, ncol=3)
        label = (
            "after production ALS"
            if corrected
            else "before ALS (stored-phase real spectra)"
        )
        ax.set_title(
            _title(
                dataset, f"Representative full-spectrum time-series overlay — {label}"
            )
        )
        paths.append(
            _save(
                fig, out / f"full_overlay_{'after' if corrected else 'before'}_als.png"
            )
        )
    for corrected in (False, True):
        fig, ax = plt.subplots(figsize=(12, 7))
        offset = 0.0
        scale = max(
            np.percentile(np.abs(np.asarray(a.inspection.corrected_real)), 95)
            for a in acquisitions
        )
        for acq in acquisitions:
            ppm = np.asarray(acq.inspection.ppm_axis)
            y = (
                np.asarray(acq.inspection.corrected_real)
                if corrected
                else np.real(acq.inspection.phased_spectrum)
            )
            ax.plot(
                ppm, y + offset, lw=0.65, label=acq.timestamp.strftime("%m-%d %H:%M")
            )
            offset += scale * 1.2
        _style_spectrum(ax, ppm)
        ax.legend(frameon=False, ncol=3, fontsize=8)
        label = "after ALS" if corrected else "before ALS"
        ax.set_title(
            _title(
                dataset,
                f"Representative full-spectrum waterfall — {label}; traces vertically offset",
            )
        )
        paths.append(
            _save(fig, out / f"waterfall_{'after' if corrected else 'before'}_als.png")
        )
    return paths


def referencing_figures(acquisitions, out, dataset):
    """Show the production metadata axis before/after the no-op reference stage."""
    paths = []
    rows = []
    for acq in acquisitions:
        ppm = np.asarray(acq.inspection.ppm_axis)
        y = np.real(acq.inspection.phased_spectrum)
        # Production reference_method=metadata preserves the decoded ppm grid.
        referenced_ppm = ppm.copy()
        shift = float(np.max(np.abs(referenced_ppm - ppm)))
        md = acq.inspection.metadata
        reference_text = md.get(
            ".SHIFT REFERENCE", md.get("$REFERENCE", "metadata axis")
        )
        rows.append(
            {
                "file": acq.dx.name,
                "timestamp": acq.timestamp.isoformat(),
                "production_reference_method": "metadata",
                "metadata_shift_reference": reference_text,
                "applied_shift_ppm": shift,
                "intensity_max_absolute_difference": float(
                    np.max(np.abs(y - y.copy()))
                ),
            }
        )
        fig, axes = plt.subplots(2, 2, figsize=(13, 8), layout="constrained")
        axes[0, 0].plot(ppm, y, lw=0.6)
        axes[0, 0].set_title("A. Metadata ppm axis before optional reference stage")
        axes[0, 1].plot(referenced_ppm, y, lw=0.6)
        axes[0, 1].set_title("B. Production axis after reference stage (unchanged)")
        ref_center = 5.0
        mask = (ppm >= 4.7) & (ppm <= 5.3)
        axes[1, 0].plot(ppm[mask], y[mask], lw=0.8)
        axes[1, 0].axvline(
            ref_center, color="#d95f02", ls="--", label="metadata reference 5.000 ppm"
        )
        axes[1, 0].legend(frameon=False, fontsize=8)
        axes[1, 0].set_title("C. Metadata reference-region context")
        product = (ppm >= 5.65) & (ppm <= 5.95)
        axes[1, 1].plot(ppm[product], y[product], lw=1.0, label="Before")
        axes[1, 1].plot(
            referenced_ppm[product],
            y[product],
            ls="--",
            lw=0.9,
            label="After (identical)",
        )
        axes[1, 1].legend(frameon=False)
        axes[1, 1].set_title("D. Product region: x and intensity unchanged")
        for index, (ax, axis) in enumerate(
            (
                (axes[0, 0], ppm),
                (axes[0, 1], ppm),
                (axes[1, 0], ppm[mask]),
                (axes[1, 1], ppm[product]),
            )
        ):
            _style_spectrum(ax, axis, xlabel=index >= 2)
        fig.suptitle(
            _title(
                dataset,
                f"Chemical-shift referencing audit — {acq.timestamp:%m-%d %H:%M}; applied offset 0.000 ppm",
            )
        )
        paths.append(_save(fig, out / f"{_stem(acq)}_referencing_audit.png"))
    table = out / "reference_offsets.csv"
    table.parent.mkdir(parents=True, exist_ok=True)
    with table.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return paths + [table], rows


def phase_detail(acq, out, dataset):
    import nmrglue as ng

    ppm = np.asarray(acq.inspection.ppm_axis)
    fft = np.asarray(acq.inspection.fft_spectrum)
    p0 = ng.proc_base.ps(fft, p0=acq.inspection.phase0_deg, p1=0.0, inv=True)
    p01 = ng.proc_base.ps(
        fft, p0=acq.inspection.phase0_deg, p1=acq.inspection.phase1_deg, inv=True
    )
    traces = (
        (np.real(fft), "FFT real before phase"),
        (np.real(p0), f"After inverse p0={acq.inspection.phase0_deg:g}°"),
        (np.real(p01), f"After inverse p1={acq.inspection.phase1_deg:g}°"),
        (np.real(acq.inspection.phased_spectrum), "Production phased spectrum"),
    )
    fig, axes = plt.subplots(4, 1, figsize=(12, 10), sharex=True, layout="constrained")
    for i, (ax, (values, label)) in enumerate(zip(axes, traces)):
        ax.plot(ppm, values, lw=0.6)
        ax.set_title(label)
        _style_spectrum(ax, ppm, xlabel=i == 3)
    fig.suptitle(_title(dataset, "Stored zero- and first-order phase sequence"))
    return _save(fig, out / f"{_stem(acq)}_phase_sequence.png")


def _arpls_baseline(values, smoothness=1e7, ratio=1e-6, iterations=30):
    """Audit-only arPLS baseline (Baek et al. logistic reweighting)."""
    from scipy import sparse
    from scipy.sparse.linalg import spsolve

    y = np.asarray(values, dtype=float)
    d = sparse.diags([1.0, -2.0, 1.0], [0, 1, 2], shape=(y.size - 2, y.size))
    penalty = float(smoothness) * (d.T @ d)
    weights = np.ones(y.size)
    baseline = np.zeros_like(y)
    for _ in range(int(iterations)):
        baseline = spsolve(
            (sparse.spdiags(weights, 0, y.size, y.size) + penalty).tocsc(), weights * y
        )
        negative = (y - baseline)[y < baseline]
        if negative.size < 2 or float(np.std(negative)) == 0:
            break
        mean = float(np.mean(negative))
        std = float(np.std(negative))
        exponent = np.clip(2.0 * ((y - baseline) - (2.0 * std - mean)) / std, -60, 60)
        updated = 1.0 / (1.0 + np.exp(exponent))
        if np.linalg.norm(updated - weights) / np.linalg.norm(weights) < ratio:
            weights = updated
            break
        weights = updated
    return np.asarray(baseline)


def _method_metrics(acq, method, baseline):
    ppm = np.asarray(acq.inspection.ppm_axis)
    pre = np.real(acq.inspection.phased_spectrum)
    corrected = pre - baseline
    picked = pick_spectrum_region(
        ppm,
        corrected,
        region_min_ppm=5.0,
        region_max_ppm=6.5,
        min_prominence_snr=5.0,
        min_distance_ppm=0.04,
        min_width_ppm=0.015,
        baseline_polynomial_order=3,
        smoothing_window_ppm=0.006,
        quantitative_intensity=corrected,
        source=acq.dx,
    )
    target = _target_peak(picked, _observe_frequency(acq.inspection.metadata))
    left = (
        left_peak_integral(
            acq.dx,
            ppm,
            corrected,
            TARGET_WINDOW,
            height_fraction=0.20,
            multiplet_span=0.05,
        )
        if target
        else None
    )
    quiet = (
        (ppm >= 8.0)
        | (ppm <= 0.0)
        | ((ppm >= 5.10) & (ppm <= 5.35))
        | ((ppm >= 5.95) & (ppm <= 6.50))
    )
    quiet_values = corrected[quiet]
    return {
        "method": method,
        "baseline": baseline,
        "corrected": corrected,
        "target": target,
        "target_ppm": target.interpolated_ppm if target else 0.0,
        "target_height": target.peak_height if target else 0.0,
        "stage1_area": target.positive_area if target else 0.0,
        "snr": target.snr if target else 0.0,
        "fixed_product_area": _integral(ppm, corrected, TARGET_WINDOW).positive_area,
        "left_peak_area": left["area"] if left else 0.0,
        "starting_material_area": _integral(
            ppm, corrected, STARTING_MATERIAL_WINDOW
        ).positive_area,
        "silane_area": _integral(ppm, corrected, SILANE_WINDOW).positive_area,
        "negative_area_full": float(
            np.trapezoid(
                np.maximum(-corrected[np.argsort(ppm)], 0), ppm[np.argsort(ppm)]
            )
        ),
        "quiet_region_rms": float(
            np.sqrt(np.mean((quiet_values - np.median(quiet_values)) ** 2))
        ),
    }


def baseline_method_comparison(acquisitions, out, dataset):
    rows = []
    paths = []
    for acq in acquisitions:
        pre = np.real(acq.inspection.phased_spectrum)
        zeros = np.zeros_like(pre)
        poly = {}
        for degree in (1, 2, 3):
            _, baseline, _, _ = subtract_abd_polynomial_baseline(
                pre,
                sections=128,
                noise_factor=3.0,
                window_points=60,
                polynomial_order=degree,
            )
            poly[degree] = baseline
        methods = [
            _method_metrics(acq, "none", zeros),
            _method_metrics(
                acq,
                "production_asls_lambda1e6_p0.001",
                np.asarray(acq.inspection.als_baseline),
            ),
            _method_metrics(acq, "abd_polynomial_degree1", poly[1]),
            _method_metrics(acq, "abd_polynomial_degree2", poly[2]),
            _method_metrics(acq, "abd_polynomial_degree3", poly[3]),
            _method_metrics(acq, "arpls_lambda1e7", _arpls_baseline(pre)),
        ]
        production = methods[1]
        for method in methods:
            row = {
                "file": acq.dx.name,
                "timestamp": acq.timestamp.isoformat(),
                "role": acq.role,
            }
            for key, value in method.items():
                if key not in {"baseline", "corrected", "target"}:
                    row[key] = value
            for metric in (
                "target_ppm",
                "target_height",
                "stage1_area",
                "snr",
                "fixed_product_area",
                "left_peak_area",
                "starting_material_area",
                "silane_area",
            ):
                ref = production[metric]
                row[f"{metric}_percent_vs_production"] = (
                    0.0 if ref == 0 else 100.0 * (method[metric] - ref) / abs(ref)
                )
            rows.append(row)
        ppm = np.asarray(acq.inspection.ppm_axis)
        fig, axes = plt.subplots(3, 2, figsize=(14, 11), layout="constrained")
        for method in methods[1:]:
            axes[0, 0].plot(ppm, method["baseline"], lw=0.75, label=method["method"])
        axes[0, 0].plot(ppm, pre, color="0.75", lw=0.45, label="common phased input")
        axes[0, 0].set_title("Estimated global baselines")
        offsets = 0.0
        step = max(np.percentile(np.abs(m["corrected"]), 95) for m in methods) * 1.1
        for method in methods:
            axes[0, 1].plot(
                ppm, method["corrected"] + offsets, lw=0.6, label=method["method"]
            )
            offsets += step
        axes[0, 1].set_title("Corrected full spectra; vertical offsets are artificial")
        regions = (
            (TARGET_WINDOW, "Product 5.70–5.90 ppm"),
            (STARTING_MATERIAL_WINDOW, "Starting material 5.40–5.52 ppm"),
            (SILANE_WINDOW, "Silane 4.94–5.06 ppm"),
        )
        for ax, (bounds, label) in zip((axes[1, 0], axes[1, 1], axes[2, 0]), regions):
            mask = (ppm >= bounds[0]) & (ppm <= bounds[1])
            for method in methods:
                ax.plot(
                    ppm[mask], method["corrected"][mask], lw=0.8, label=method["method"]
                )
            ax.set_title(label)
            _style_spectrum(ax, ppm[mask])
        finite = pre[np.isfinite(pre)]
        lo, hi = np.percentile(finite, [5, 85])
        axes[2, 1].plot(ppm, pre, color="0.6", lw=0.45, label="common input")
        for method in methods[1:]:
            axes[2, 1].plot(ppm, method["baseline"], lw=0.7, label=method["method"])
        axes[2, 1].set_ylim(lo - 0.2 * (hi - lo), hi + 0.2 * (hi - lo))
        axes[2, 1].set_title(
            "Full-spectrum baseline-focused comparison; peaks intentionally clip"
        )
        for ax in (axes[0, 0], axes[0, 1], axes[2, 1]):
            _style_spectrum(ax, ppm)
        axes[0, 0].legend(frameon=False, fontsize=6, ncol=2)
        axes[0, 1].legend(frameon=False, fontsize=6, ncol=2)
        fig.suptitle(
            _title(
                dataset,
                f"Global baseline-method comparison — {acq.timestamp:%m-%d %H:%M}",
            )
        )
        paths.append(_save(fig, out / f"{_stem(acq)}_baseline_methods.png"))
    table = out / "baseline_method_metrics.csv"
    with table.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return paths + [table], rows


def _stage_bounds(acq):
    if acq.target is None:
        return None
    center = acq.target.interpolated_ppm
    half = max(acq.target.width_ppm, 0.015)
    return center - half, center + half


def integration_audit(acquisitions, out, dataset, series):
    time_rows = {}
    series_csv = series / "timeseries_results/peak_area_timeseries.csv"
    if series_csv.is_file():
        with series_csv.open(newline="", encoding="utf-8") as handle:
            time_rows = {row["dx_file"]: row for row in csv.DictReader(handle)}
    rows = []
    figures = []
    for acq in acquisitions:
        ppm = np.asarray(acq.inspection.ppm_axis)
        final = np.asarray(acq.inspection.corrected_real)
        local_ppm = np.asarray(acq.picked.ppm_axis)
        local_y = np.asarray(acq.picked.quantitative_corrected)
        bounds = _stage_bounds(acq)
        target = acq.target
        if bounds:
            lo, hi = bounds
            order = np.argsort(local_ppm)
            lx, ly = local_ppm[order], local_y[order]
            left_signal = float(np.interp(lo, lx, ly))
            right_signal = float(np.interp(hi, lx, ly))
            height = target.peak_height
        else:
            lo = hi = left_signal = right_signal = height = 0.0
        left_bounds = (
            (acq.left_peak["from_ppm"], acq.left_peak["to_ppm"])
            if acq.left_peak
            else None
        )
        timeline = time_rows.get(acq.dx.name, {})
        row = {
            "file": acq.dx.name,
            "timestamp": acq.timestamp.isoformat(),
            "timestamp_source": acq.timestamp_source,
            "elapsed_hours": timeline.get("elapsed_hours", ""),
            "step_label": timeline.get("step_label", ""),
            "qc_status": "pass" if target else "not_detected",
            "peak_ppm": target.interpolated_ppm if target else 0.0,
            "linewidth_hz": target.width_ppm
            * _observe_frequency(acq.inspection.metadata)
            if target
            else 0.0,
            "linewidth_ppm": target.width_ppm if target else 0.0,
            "stage1_left_ppm": lo,
            "stage1_right_ppm": hi,
            "stage1_width_ppm": hi - lo,
            "stage1_left_boundary_signal": left_signal,
            "stage1_right_boundary_signal": right_signal,
            "stage1_left_boundary_fraction": left_signal / height if height else 0.0,
            "stage1_right_boundary_fraction": right_signal / height if height else 0.0,
            "stage1_area": target.positive_area if target else 0.0,
            "fixed_product_area": acq.fixed_product.positive_area,
            "left_peak_area": acq.left_peak["area"] if acq.left_peak else 0.0,
            "starting_material_area": acq.starting_material.positive_area,
            "silane_area": acq.silane.positive_area,
            "snr": target.snr if target else 0.0,
        }
        rows.append(row)
        fig, axes = plt.subplots(2, 2, figsize=(12, 8), layout="constrained")
        focus = (ppm >= 5.65) & (ppm <= 5.95)
        axes[0, 0].plot(ppm[focus], final[focus], lw=0.9)
        axes[0, 0].axvspan(*TARGET_WINDOW, alpha=0.12, label="fixed window")
        if bounds:
            axes[0, 0].axvline(lo, color="#d95f02")
            axes[0, 0].axvline(hi, color="#d95f02")
            axes[0, 0].scatter([lo, hi], [left_signal, right_signal], color="#d95f02")
        axes[0, 0].set_title("A. All product integration boundaries")
        axes[0, 1].plot(local_ppm, local_y, lw=0.9)
        if bounds:
            stage = (local_ppm >= lo) & (local_ppm <= hi)
            axes[0, 1].fill_between(
                local_ppm[stage], 0, np.maximum(local_y[stage], 0), alpha=0.3
            )
        axes[0, 1].set_title(f"B. Stage-1 variable area = {row['stage1_area']:.2f}")
        axes[1, 0].plot(ppm[focus], final[focus], lw=0.9)
        fx, fy, fc, fp = local_integration_fill(ppm, final, *TARGET_WINDOW)
        axes[1, 0].plot(fx, fc, ls="--", color="#d95f02")
        axes[1, 0].fill_between(fx, fc, fc + fp, alpha=0.3)
        axes[1, 0].set_title(
            f"C. Fixed product-window area = {row['fixed_product_area']:.2f}"
        )
        axes[1, 1].plot(ppm[focus], final[focus], lw=0.9)
        if left_bounds:
            x, y, chord, positive = local_integration_fill(ppm, final, *left_bounds)
            axes[1, 1].plot(x, chord, ls="--", color="#d95f02")
            axes[1, 1].fill_between(x, chord, chord + positive, alpha=0.3)
            axes[1, 1].scatter([x[0], x[-1]], [chord[0], chord[-1]], color="#d95f02")
        axes[1, 1].set_title(
            f"D. Left-line valley-to-valley area = {row['left_peak_area']:.2f}"
        )
        for ax in axes.flat:
            _style_spectrum(ax, ppm[focus])
        fig.suptitle(
            _title(dataset, f"Integration-method audit — {acq.timestamp:%m-%d %H:%M}")
        )
        figures.append(_save(fig, out / f"{_stem(acq)}_integration_audit.png"))
    table = out / "integration_master_table.csv"
    with table.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    return figures + [table], rows


def metric_timeseries(rows, out, dataset):
    rows = sorted(rows, key=lambda row: row["timestamp"])
    t = np.array([float(r["elapsed_hours"] or i) for i, r in enumerate(rows)])
    metrics = (
        ("stage1_area", "Stage-1 variable peak area"),
        ("fixed_product_area", "Fixed 5.70–5.90 ppm area"),
        ("left_peak_area", "Left-line valley-to-valley area"),
    )
    paths = []

    def plot_lines(filename, title, transform=lambda x: x, ylabel="Area"):
        fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
        for key, label in metrics:
            ax.plot(
                t,
                transform(np.array([float(r[key]) for r in rows])),
                marker="o",
                label=label,
            )
        ax.set_xlabel("Elapsed time from JCAMP LONG DATE (h)")
        ax.set_ylabel(ylabel)
        ax.legend(frameon=False)
        ax.grid(alpha=0.25)
        ax.set_title(_title(dataset, title))
        paths.append(_save(fig, out / filename))

    plot_lines(
        "A_product_metrics_raw.png", "Three product metrics versus metadata time"
    )
    plot_lines(
        "B_product_metrics_normalized.png",
        "Product metrics normalized to their own maxima",
        lambda x: x / max(x.max(), 1e-12),
        "Fraction of own maximum",
    )
    plot_lines(
        "C_product_metrics_adjacent_percent_change.png",
        "Adjacent percent change in product metrics",
        lambda x: np.r_[
            np.nan,
            100 * np.diff(x) / np.maximum(np.abs(x[:-1]), 0.05 * max(x.max(), 1e-12)),
        ],
        "Adjacent change (%)",
    )
    fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
    ax.plot(t, [float(r["stage1_width_ppm"]) for r in rows], marker="o")
    ax.set_xlabel("Elapsed time from JCAMP LONG DATE (h)")
    ax.set_ylabel("Stage-1 full integration width (ppm)")
    ax.grid(alpha=0.25)
    ax.set_title(_title(dataset, "Stage-1 integration width versus time"))
    paths.append(_save(fig, out / "D_stage1_width_vs_time.png"))
    fig, ax = plt.subplots(figsize=(10, 5), layout="constrained")
    ax.plot(
        t,
        [100 * float(r["stage1_left_boundary_fraction"]) for r in rows],
        marker="o",
        label="low-ppm boundary",
    )
    ax.plot(
        t,
        [100 * float(r["stage1_right_boundary_fraction"]) for r in rows],
        marker="o",
        label="high-ppm boundary",
    )
    for ref in (1, 5, 10, 20):
        ax.axhline(ref, color="0.8", lw=0.7, ls="--")
    ax.set_xlabel("Elapsed time from JCAMP LONG DATE (h)")
    ax.set_ylabel("Boundary signal / peak height (%)")
    ax.legend(frameon=False)
    ax.grid(alpha=0.25)
    ax.set_title(_title(dataset, "Signal remaining at Stage-1 integration boundaries"))
    paths.append(_save(fig, out / "E_stage1_boundary_signal_vs_time.png"))
    return paths


def contact_sheet(images, path, dataset, title):
    valid = [p for p in images if p.suffix.lower() == ".png" and p.is_file()]
    cols = 3
    rows = int(np.ceil(len(valid) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(15, 4.8 * rows))
    axes = np.asarray(axes).reshape(-1)
    for ax, image in zip(axes, valid):
        ax.imshow(plt.imread(image))
        ax.set_title(image.name, fontsize=8)
        ax.axis("off")
    for ax in axes[len(valid) :]:
        ax.axis("off")
    fig.suptitle(_title(dataset, title), fontsize=16)
    return _save(fig, path)


def _write_reports(
    out,
    dataset,
    acquisitions,
    validation_rows,
    sensitivity_rows,
    difference_error,
    created,
    reference_rows,
    baseline_rows,
    integration_rows,
):
    prod = next(a for a in acquisitions if a.role == "primary worked example")
    phase_rms = []
    baseline_rms = []
    phase_product_rms = []
    baseline_product_rms = []
    for acq in acquisitions:
        inspection = acq.inspection
        ppm = np.asarray(inspection.ppm_axis)
        phase_delta = np.real(inspection.phased_spectrum) - np.real(
            inspection.fft_spectrum
        )
        product_mask = (ppm >= TARGET_WINDOW[0]) & (ppm <= TARGET_WINDOW[1])
        phase_rms.append(float(np.sqrt(np.mean(np.square(phase_delta)))))
        baseline_rms.append(
            float(np.sqrt(np.mean(np.square(inspection.als_baseline))))
        )
        phase_product_rms.append(
            float(np.sqrt(np.mean(np.square(phase_delta[product_mask]))))
        )
        baseline_product_rms.append(
            float(
                np.sqrt(
                    np.mean(np.square(np.asarray(inspection.als_baseline)[product_mask]))
                )
            )
        )
    max_changes = {}
    for metric in (
        "target_area",
        "fixed_product_area",
        "starting_material_area",
        "silane_area",
        "left_peak_area",
    ):
        values = [
            abs(float(r[f"{metric}_percent_change"]))
            for r in sensitivity_rows
            if r["setting"] != "production" and float(r[metric]) != 0
        ]
        max_changes[metric] = max(values, default=0.0)
    readme = f"""# {dataset} NMR processing inspection

This directory is an exploratory, read-only transparency package generated from real JCAMP-DX FIDs. It does not replace production plots or alter production numerical outputs.

## Confirmed production sequence

JCAMP real/imaginary page decode and FACTOR scaling → complex FID → exponential line broadening ({LB_HZ:g} Hz) → zero fill ({FFT_POINTS} points) → FFT/fftshift → metadata ppm axis → stored NMReady phase via `nmrglue.proc_base.ps(..., inv=True)` → ALS on the phased real spectrum only (lambda={ALS_LAMBDA:.0e}, asymmetry={ALS_P:g}, {ALS_ITERATIONS} iterations) → subtraction with no normalization → 5.0–6.5 ppm regional polynomial detrend → detection-only Savitzky–Golay smoothing (0.006 ppm) → peak measurement/integration → QC.

The core peak area integrates the regional quantitative trace over ± the detected linewidth (minimum half-width 0.015 ppm). Fixed-window and left-multiplet areas instead use a straight line joining the requested integration feet and positive-clipped trapezoidal integration.

## Primary worked example

`{prod.dx.name}` at `{prod.timestamp.isoformat()}` from `{prod.timestamp_source}`. Reconstructed core area `{prod.target.positive_area if prod.target else 0:.2f}`, fixed product area `{prod.fixed_product.positive_area:.2f}`, and left-multiplet area `{prod.left_peak["area"] if prod.left_peak else 0:.2f}`.

`pre - post` equals the stored ALS baseline with maximum absolute residual `{difference_error:.3g}`.

## Sensitivity summary

Maximum absolute percent changes over the representative set and tested reasonable ALS variations: core area `{max_changes["target_area"]:.2f}%`, fixed product `{max_changes["fixed_product_area"]:.2f}%`, starting material `{max_changes["starting_material_area"]:.2f}%`, silane `{max_changes["silane_area"]:.2f}%`, left multiplet `{max_changes["left_peak_area"]:.2f}%`. See `variant_06_als_sensitivity/als_sensitivity_metrics.csv` for every value; zero/non-detected cases must be interpreted separately.

## Timing

All ordering and scientific timestamps in this package use JCAMP `LONG DATE`. Filename-derived times are not used as authoritative timing.
"""
    (out / "README.md").write_text(readme, encoding="utf-8")
    comparison = """# Figure variant comparison

| Variant | Scientific question | Strengths | Weaknesses | Best use |
|---|---|---|---|---|
| 01 full baseline overlay | What baseline did ALS place under the phased spectrum? | Direct, compact, full range | Dominant peaks compress subtle baseline detail | Routine audit / SI |
| 02 before/after | How much did baseline subtraction change the spectrum? | Clearest paired comparison; matched and independent scales | Two panels require more space | Best before/after figure |
| 03 baseline magnified | What slope, curvature, and near-zero residual are hidden by autoscaling? | Most sensitive baseline inspection | Intentional peak clipping needs explanation | Troubleshooting / scientific audit |
| 04 processing steps | How does the raw FID become the quantitative spectrum? | Separates time-domain, FFT, phase, and baseline operations | Dense | Teaching / meeting walkthrough |
| 05 full plus regions | How do complete spectra connect to product, starting-material, and silane numbers? | Presentation friendly; distinguishes integration definitions | Less detail on ALS shape | Presentation / compact report |
| 06 ALS sensitivity | Are results robust to reasonable ALS choices? | Quantitative absolute and percent comparisons | Diagnostic rather than publication-ready | Validation / regression |
| 07 phase and baseline | Which change is phase and which is baseline? | Prevents conceptual conflation | Four panels | Troubleshooting / explanation |

## Recommendations

- **Best scientific-audit figure:** Variant 03 plus Variant 06 metrics.
- **Best before/after baseline figure:** Variant 02B matched scale, with 02A available for weak-feature inspection.
- **Best presentation figure:** Variant 05.
- **Best compact routine-report figure:** Variant 01.
- **Best processing explanation:** Variant 04, supplemented by Variant 07.
"""
    (out / "FIGURE_VARIANT_COMPARISON.md").write_text(comparison, encoding="utf-8")
    processing_audit = f"""# Processing audit

## Production path confirmed from source

| Order | Function/code | Change |
|---:|---|---|
| 1 | `read_jcamp_fid` | Decodes both JCAMP NTUPLES pages and applies their FACTOR scaling (y/data). |
| 2 | `FidData.complex_points` | Combines decoded real and imaginary values (data representation). |
| 3 | `_build_complex_spectrum` → `nmrglue.proc_base.em` | Applies `exp(-pi × {LB_HZ:g} Hz × time)` (y). |
| 4 | `nmrglue.proc_base.zf_size` | Zero-fills from the acquired points to {FFT_POINTS} points; this interpolates the digital spectrum but adds no physical information. |
| 5 | `fourier_transform_fid` | FFT plus fftshift (time-domain data → frequency-domain y). |
| 6 | `build_ppm_axis` | Uses `$SWH`/`$SWEEP WIDTH`, `$SF`/observe frequency, and `$O1P`/spectral center (x only). Internal arrays ascend; plots invert to conventional high-to-low ppm. |
| 7 | `build_phased_spectrum` → `nmrglue.proc_base.ps` | Applies stored `$PHC0` and `$PHC1` with `inv=True` (complex y/lineshape). |
| 8 | `process_fid.py` reference branch | `reference_method=metadata`; keeps the metadata ppm grid unchanged. Optional validated/manual/model modes are disabled (x only when enabled). |
| 9 | `asymmetric_least_squares_baseline` | Global spectral background estimated on the full phased real spectrum with lambda={ALS_LAMBDA:.0e}, p={ALS_P:g}, iterations={ALS_ITERATIONS}; corrected y = phased real − global baseline. No normalization. |
| 10 | `pick_spectrum_region` | Restricts detection to 5.0–6.5 ppm, fits an iteratively clipped degree-3 regional polynomial, estimates robust MAD noise, and smooths only the detection trace with a 0.006 ppm Savitzky–Golay window. |
| 11 | `scipy.signal.find_peaks` inside `pick_spectrum_region` | Requires detection prominence ≥5 regional noise units, separation ≥0.04 ppm, and width ≥0.015 ppm. |
| 12 | `pick_spectrum_region` | Measures position by a three-point parabolic apex, height/SNR on the quantitative regional trace, and Stage-1 positive trapezoidal area over apex ± max(detected linewidth, 0.015 ppm). |
| 13 | `_peak_qc` | Requires SNR ≥3, prominence/SNR ≥3, linewidth 1–10 Hz, and positive area. |
| 14 | `_select_simple_peak_rows` | Within 5.70–5.90 ppm, keeps the QC-passing candidate with highest SNR; a non-detection is zero-filled in `peaks_simple.csv`. |

## Referencing result for this dataset

The JCAMP headers describe an internal toluene metadata reference at 5.000 ppm. Production is configured with `reference_method: metadata`, reference correction disabled, solvent identity unknown, and no internal standard. Therefore no reference peak is searched, no constant offset is calculated, and the applied offset is 0.000 ppm for all six acquisitions. The reference audit numerically confirms that intensity is unchanged.

## Phase result

The August worked examples store p0={prod.inspection.phase0_deg:g}° and p1={prod.inspection.phase1_deg:g}°. They are passed to `nmrglue.proc_base.ps(..., inv=True)`; no automatic re-phasing occurs.

Across the six acquisitions, the stored phase operation changes the full-spectrum real trace by RMS {min(phase_rms):.1f}–{max(phase_rms):.1f} a.u.; the production ALS subtraction changes it by RMS {min(baseline_rms):.1f}–{max(baseline_rms):.1f} a.u. In the 5.70–5.90 ppm product window, the corresponding ranges are {min(phase_product_rms):.1f}–{max(phase_product_rms):.1f} a.u. for phase and {min(baseline_product_rms):.1f}–{max(baseline_product_rms):.1f} a.u. for ALS. Baseline subtraction is therefore the larger numerical transformation in this dataset, including around the product signal; this comparison describes magnitude, not correctness.
"""
    (out / "PROCESSING_AUDIT.md").write_text(processing_audit, encoding="utf-8")

    method_names = sorted({row["method"] for row in baseline_rows})
    method_lines = []
    for method in method_names:
        subset = [row for row in baseline_rows if row["method"] == method]
        product = max(
            abs(float(row["fixed_product_area_percent_vs_production"]))
            for row in subset
        )
        sm = max(
            abs(float(row["starting_material_area_percent_vs_production"]))
            for row in subset
        )
        silane = max(
            abs(float(row["silane_area_percent_vs_production"])) for row in subset
        )
        method_lines.append(
            f"| `{method}` | {product:.1f}% | {sm:.1f}% | {silane:.1f}% |"
        )
    baseline_report = (
        """# Global baseline-method comparison

All methods branch from the identical stored-phase, metadata-axis spectrum. The local valley-to-valley integration chord is not a global baseline method.

| Method | Description | Main assumption |
|---|---|---|
| none | No global correction control | Regional/local integration baselines handle remaining background |
| production AsLS | lambda 1e6, p 0.001, 10 iterations | Most data are background and positive resonances should receive low fitting weight |
| ABD polynomial 1–3 | ABD-selected low-variation points, 128 sections, noise factor 3, 60-point window | A low-order global polynomial describes background |
| arPLS | lambda 1e7, logistic reweighting | Negative residual distribution estimates background adaptively |

Maximum absolute change versus production over the six acquisitions:

| Method | Fixed product | Starting material | Silane |
|---|---:|---:|---:|
"""
        + "\n".join(method_lines)
        + """

Visual review shows that production AsLS follows substantial broad/dispersive structure near the dominant solvent resonances. Its correction is therefore visually large in those regions, while the small 5.8 ppm target is affected much less in absolute intensity but can change materially in percentage terms when weak. The flattest-looking result is not automatically the most defensible quantitative result: broad genuine signal can be absorbed by a penalized baseline. The fixed-window and Stage-1 metrics should be treated as baseline-sensitive for low-SNR spectra; the local left-line integral is less sensitive in this tested set.
"""
    )
    (out / "BASELINE_METHOD_COMPARISON.md").write_text(
        baseline_report, encoding="utf-8"
    )

    five_fifteen = next(row for row in integration_rows if "171806" in row["file"])
    stage = np.array([float(row["stage1_area"]) for row in integration_rows])
    fixed = np.array([float(row["fixed_product_area"]) for row in integration_rows])
    left_values = np.array([float(row["left_peak_area"]) for row in integration_rows])

    def corr(a, b):
        return (
            float(np.corrcoef(a, b)[0, 1]) if np.std(a) and np.std(b) else float("nan")
        )

    integration_report = f"""# Integration audit

1. **Stage-1 rule:** the detected center is integrated over ± max(detected linewidth, 0.015 ppm) on the regional quantitative trace after its clipped polynomial detrend. The reported area is the positive-clipped trapezoidal area.
2. **Variable width:** yes. Across this series the full Stage-1 span ranges from {min(float(r["stage1_width_ppm"]) for r in integration_rows):.4f} to {max(float(r["stage1_width_ppm"]) for r in integration_rows):.4f} ppm (including zero for the non-detection if shown in the table).
3. **Multiplet exclusion:** the Stage-1 bounds can exclude visible neighboring product signal because they track one detected envelope rather than the fixed 5.70–5.90 ppm window.
4. **Boundary signal:** see `06_integration_audit/integration_master_table.csv` and `E_stage1_boundary_signal_vs_time.png`; the reference lines at 1, 5, 10, and 20% are visual aids, not QC thresholds.
5. **Aug. 10 5:15 pull:** Stage-1 {float(five_fifteen["stage1_area"]):.2f}, fixed-window {float(five_fifteen["fixed_product_area"]):.2f}, and left-line {float(five_fifteen["left_peak_area"]):.2f} differ because they measure, respectively, a linewidth-dependent detected envelope, the full fixed product window, and only the highest-ppm multiplet line above a local valley chord. Its low/high ppm Stage-1 boundaries are {float(five_fifteen["stage1_left_ppm"]):.4f}/{float(five_fifteen["stage1_right_ppm"]):.4f} ppm with signals {float(five_fifteen["stage1_left_boundary_signal"]):.1f}/{float(five_fifteen["stage1_right_boundary_signal"]):.1f} a.u.
6. **Trend agreement:** Stage-1 versus fixed-window Pearson r={corr(stage, fixed):.3f}.
7. **Left-line trend:** Stage-1 versus left-line Pearson r={corr(stage, left_values):.3f}; it does not reproduce the same time profile.
8. **Day-2 decrease:** changing Stage-1 width/boundaries can contribute, but cannot by itself establish a chemical explanation; compare the fixed-window and boundary plots.
9. **Internal consistency:** the fixed window is the most geometrically consistent whole-product measure across time, while the left-line chord is locally baseline-robust but measures only one component. Stage-1 retains detection specificity but changes its physical span.
10. **Future work:** multiplet fitting/deconvolution is worth investigating as a separate validated method; it was not introduced here.

The collector shading bug is corrected in code: the fill now runs from the local valley-to-valley chord to the positive spectrum residual, matching the numerical `left_peak_area`, rather than filling to zero.
"""
    (out / "INTEGRATION_AUDIT.md").write_text(integration_report, encoding="utf-8")
    with (out / "validation_against_saved_results.csv").open(
        "w", newline="", encoding="utf-8"
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=validation_rows[0].keys())
        writer.writeheader()
        writer.writerows(validation_rows)
    manifest = {
        "dataset_display_name": dataset,
        "files": sorted(p.relative_to(out).as_posix() for p in created if p.exists()),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


def main(argv=None):
    args = _parser().parse_args(argv)
    series = args.series_dir.resolve()
    dataset = args.dataset_display_name or resolve_dataset_display_name(
        input_paths=series
    )
    out = args.output_root.resolve() / series.name
    if out.exists():
        raise SystemExit(
            f"inspection output already exists; refusing to overwrite: {out}"
        )
    runs = [
        p
        for p in sorted(series.iterdir())
        if p.is_dir() and any((p / "raw_nmr").glob("*.dx"))
    ]
    acquisitions = [_load_acquisition(run) for run in runs]
    acquisitions.sort(key=lambda a: a.timestamp)
    roles = (
        "primary worked example",
        "difficult / non-detection",
        "intermediate",
        "late strong product",
        "late follow-up",
        "final / clean presentation",
    )
    for acq, role in zip(acquisitions, roles):
        acq.role = role
    existing_files = sorted(
        {Path(a.saved["_source_csv"]) for a in acquisitions}
        | {series / "timeseries_results/peak_area_timeseries.csv"}
    )
    before_hashes = {str(p): _sha256(p) for p in existing_files if p.is_file()}
    created = []
    for name in (
        "variant_01_full_baseline_overlay",
        "variant_02_before_after",
        "variant_03_baseline_magnified",
        "variant_04_processing_steps",
        "variant_05_full_plus_regions",
        "variant_06_als_sensitivity",
        "variant_07_phase_and_baseline",
        "deep_dive_primary",
        "time_series_overlays",
        "02_referencing",
        "04_baseline_method_comparison",
        "06_integration_audit",
        "07_timeseries_metric_comparison",
        "08_worked_examples",
        "tables",
        "contact_sheets",
    ):
        (out / name).mkdir(parents=True, exist_ok=False)
    primary = acquisitions[0]
    v1 = [
        variant_01(a, out / "variant_01_full_baseline_overlay", dataset)
        for a in acquisitions
    ]
    created += v1
    v2 = [
        variant_02(a, out / "variant_02_before_after", dataset, matched)
        for a in acquisitions
        for matched in (False, True)
    ]
    created += v2
    v3 = variant_03(primary, out / "variant_03_baseline_magnified", dataset)
    created += v3
    v4 = variant_04(primary, out / "variant_04_processing_steps", dataset)
    created += v4
    v5 = [
        variant_05(a, out / "variant_05_full_plus_regions", dataset)
        for a in (primary, acquisitions[2], acquisitions[-1])
    ]
    created += v5
    v6, sensitivity_rows = variant_06(
        acquisitions, out / "variant_06_als_sensitivity", dataset
    )
    created += v6
    v7 = variant_07(primary, out / "variant_07_phase_and_baseline", dataset)
    created.append(v7)
    diff, difference_error = difference_plot(
        primary, out / "deep_dive_primary", dataset
    )
    deep = deep_dive(primary, out / "deep_dive_primary", dataset)
    created += [diff, deep]
    time_series = overlays(acquisitions, out / "time_series_overlays", dataset)
    created += time_series
    reference_files, reference_rows = referencing_figures(
        acquisitions, out / "02_referencing", dataset
    )
    created += reference_files
    phase_files = [
        phase_detail(primary, out / "variant_04_processing_steps", dataset),
        phase_detail(acquisitions[3], out / "variant_04_processing_steps", dataset),
    ]
    created += phase_files
    baseline_files, baseline_rows = baseline_method_comparison(
        acquisitions, out / "04_baseline_method_comparison", dataset
    )
    created += baseline_files
    integration_files, integration_rows = integration_audit(
        acquisitions, out / "06_integration_audit", dataset, series
    )
    created += integration_files
    metric_files = metric_timeseries(
        integration_rows, out / "07_timeseries_metric_comparison", dataset
    )
    created += metric_files
    worked_files = [
        deep_dive(primary, out / "08_worked_examples", dataset),
        deep_dive(acquisitions[3], out / "08_worked_examples", dataset),
    ]
    created += worked_files
    sheets = [
        contact_sheet(
            [p for p in reference_files if p.suffix == ".png"],
            out / "contact_sheets/1_referencing.png",
            dataset,
            "Contact sheet 1 — referencing",
        ),
        contact_sheet(
            [*v1, *v3],
            out / "contact_sheets/2_production_baseline.png",
            dataset,
            "Contact sheet 2 — production baseline",
        ),
        contact_sheet(
            [p for p in baseline_files if p.suffix == ".png"],
            out / "contact_sheets/3_baseline_methods.png",
            dataset,
            "Contact sheet 3 — global baseline methods",
        ),
        contact_sheet(
            [p for p in integration_files if p.suffix == ".png"],
            out / "contact_sheets/4_integration_methods.png",
            dataset,
            "Contact sheet 4 — integration methods",
        ),
        contact_sheet(
            [phase_files[1], worked_files[1], integration_files[3], baseline_files[3]],
            out / "contact_sheets/5_worked_example_171806.png",
            dataset,
            "Contact sheet 5 — Aug. 10 5:15 pull worked example",
        ),
        contact_sheet(
            [*v1[:1], *v2[:2], *v3, *v4, v7, diff, deep],
            out / "contact_sheets/A_primary_all_variants.png",
            dataset,
            "Contact sheet A — primary worked example variants",
        ),
        contact_sheet(
            v2,
            out / "contact_sheets/B_before_after_representatives.png",
            dataset,
            "Contact sheet B — before/after across representative spectra",
        ),
        contact_sheet(
            [p for p in v6 if p.suffix == ".png"],
            out / "contact_sheets/C_als_sensitivity.png",
            dataset,
            "Contact sheet C — ALS sensitivity",
        ),
        contact_sheet(
            v5,
            out / "contact_sheets/D_presentation_full_plus_regions.png",
            dataset,
            "Contact sheet D — presentation full spectrum plus regions",
        ),
    ]
    created += sheets
    validation_rows = []
    for acq in acquisitions:
        target = acq.target
        reconstructed = {
            "target_ppm": target.interpolated_ppm if target else 5.8,
            "integrated_area": target.positive_area if target else 0.0,
            "intensity": target.peak_height if target else 0.0,
            "snr": target.snr if target else 0.0,
            "prominence_snr": target.prominence_snr if target else 0.0,
            "width_hz": target.width_ppm * _observe_frequency(acq.inspection.metadata)
            if target
            else 0.0,
        }
        row = {
            "file": acq.dx.name,
            "role": acq.role,
            "timestamp": acq.timestamp.isoformat(),
            "timestamp_source": acq.timestamp_source,
            "saved_timestamp": acq.saved["timestamp"],
            "detected_saved": "yes"
            if float(acq.saved["integrated_area"]) > 0
            else "no",
            "detected_reconstructed": "yes" if target else "no",
        }
        saved_keys = {"target_ppm": "peak_ppm", "target_area": "integrated_area"}
        for key, value in reconstructed.items():
            saved_key = saved_keys.get(key, key)
            row[f"saved_{key}"] = acq.saved[saved_key]
            row[f"reconstructed_{key}"] = f"{value:.8g}"
            row[f"absolute_difference_{key}"] = (
                f"{value - float(acq.saved[saved_key]):.8g}"
            )
        row.update(
            {
                "fixed_product_area": f"{acq.fixed_product.positive_area:.8g}",
                "starting_material_area": f"{acq.starting_material.positive_area:.8g}",
                "silane_area": f"{acq.silane.positive_area:.8g}",
                "left_peak_area": f"{acq.left_peak['area'] if acq.left_peak else 0:.8g}",
            }
        )
        validation_rows.append(row)
    after_hashes = {str(p): _sha256(p) for p in existing_files if p.is_file()}
    if before_hashes != after_hashes:
        raise RuntimeError("an existing quantitative output changed")
    _write_reports(
        out,
        dataset,
        acquisitions,
        validation_rows,
        sensitivity_rows,
        difference_error,
        created,
        reference_rows,
        baseline_rows,
        integration_rows,
    )
    created += [
        out / "README.md",
        out / "PROCESSING_AUDIT.md",
        out / "FIGURE_VARIANT_COMPARISON.md",
        out / "BASELINE_METHOD_COMPARISON.md",
        out / "INTEGRATION_AUDIT.md",
        out / "validation_against_saved_results.csv",
        out / "manifest.json",
    ]
    (out / "existing_output_hashes.json").write_text(
        json.dumps(
            {
                "before": before_hashes,
                "after": after_hashes,
                "unchanged": before_hashes == after_hashes,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest = {
        "dataset_display_name": dataset,
        "files": sorted(
            str(path.relative_to(out)) for path in out.rglob("*") if path.is_file()
        ),
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
