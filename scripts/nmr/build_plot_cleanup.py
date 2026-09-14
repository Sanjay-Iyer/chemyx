"""Build grouped, single-purpose NMR review plots without touching production."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chemyx_lab.analysis.plot_titles import (  # noqa: E402
    format_dataset_plot_title,
    resolve_dataset_display_name,
)
from build_timeseries_results import local_integration_fill  # noqa: E402
from inspect_processing import (  # noqa: E402
    DEFAULT_SERIES,
    TARGET_WINDOW,
    _load_acquisition,
    _stage_bounds,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "results/nmr_processing_inspection/chemyx_demo_081026_v3_plot_cleanup_v3"
)
SOURCE_AUDIT = REPO_ROOT / "results/nmr_processing_inspection/chemyx_demo_081026_v3"
PRIMARY_TOKEN = "20260810_154822"
FIVE_FIFTEEN_TOKEN = "20260810_171806"


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("series_dir", nargs="?", type=Path, default=DEFAULT_SERIES)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--dataset-display-name")
    return parser


def _metadata(acq) -> str:
    metadata = acq.inspection.metadata
    scans = metadata.get("$NS", "unknown")
    gain = metadata.get("$RG", "unknown")
    return (
        f"{acq.dx.name} | LONG DATE {acq.timestamp:%Y-%m-%d %H:%M:%S} | "
        f"scans {scans} | gain {gain}"
    )


def _save(fig, path: Path, acq, dataset: str, title: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.set_layout_engine(None)
    fig.subplots_adjust(top=0.84, bottom=0.14)
    fig.suptitle(format_dataset_plot_title(dataset, title), fontsize=13, y=0.975)
    fig.text(0.5, 0.91, _metadata(acq), ha="center", va="top", fontsize=8)
    fig.savefig(path, dpi=190, bbox_inches="tight")
    plt.close(fig)
    return path


def _autoscale_visible_y(ax, xlim):
    visible = []
    lo, hi = sorted(xlim)
    for line in ax.lines:
        x = np.asarray(line.get_xdata(), dtype=float)
        y = np.asarray(line.get_ydata(), dtype=float)
        if x.shape != y.shape or x.ndim != 1:
            continue
        mask = (x >= lo) & (x <= hi) & np.isfinite(y)
        if np.any(mask):
            visible.append(y[mask])
    if not visible:
        return
    values = np.concatenate(visible)
    bottom, top = np.percentile(values, (0.5, 99.5))
    padding = max(0.12 * float(top - bottom), 1.0)
    ax.set_ylim(float(bottom - padding), float(top + padding))


def _style(ax, ppm, xlim=None, *, autoscale_visible=True):
    if xlim is None:
        ax.set_xlim(float(np.max(ppm)), float(np.min(ppm)))
    else:
        ax.set_xlim(max(xlim), min(xlim))
        if autoscale_visible:
            _autoscale_visible_y(ax, xlim)
    ax.set_xlabel("Chemical shift (ppm; high ppm → low ppm)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.grid(alpha=0.2)


def _single_trace(
    acq,
    dataset,
    path,
    title,
    y,
    *,
    color="#1f77b4",
    xlim=None,
    ylim=None,
    zero=False,
    annotation=None,
):
    ppm = np.asarray(acq.inspection.ppm_axis)
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(ppm, y, color=color, lw=0.75)
    if zero:
        ax.axhline(0, color="0.45", lw=0.7)
    if ylim is not None:
        ax.set_ylim(*ylim)
    if annotation:
        ax.text(
            0.02,
            0.96,
            annotation,
            transform=ax.transAxes,
            va="top",
            bbox={"facecolor": "white", "alpha": 0.85, "edgecolor": "0.8"},
        )
    _style(ax, ppm, xlim, autoscale_visible=ylim is None)
    return _save(fig, path, acq, dataset, title)


def _baseline_focus_limits(acq):
    baseline = np.asarray(acq.inspection.als_baseline)
    corrected = np.asarray(acq.inspection.corrected_real)
    scale = max(
        float(np.percentile(np.abs(baseline - np.median(baseline)), 98)),
        float(np.percentile(np.abs(corrected), 90)),
        100.0,
    )
    return -1.25 * scale, 1.25 * scale


def baseline_families(acq, output, dataset):
    ins = acq.inspection
    before = np.real(ins.phased_spectrum)
    baseline = np.asarray(ins.als_baseline)
    after = np.asarray(ins.corrected_real)
    removed = before - after
    created = []
    group = output / "01_full_spectrum_production_baseline"
    specs = (
        (
            "01a_full_spectrum_before_baseline_phased_referenced.png",
            "Full spectrum — before production baseline",
            before,
            "#1f77b4",
        ),
        (
            "01b_full_spectrum_estimated_baseline_production_asls.png",
            "Full spectrum — production AsLS baseline only",
            baseline,
            "#d95f02",
        ),
        (
            "01c_full_spectrum_after_baseline_production_asls.png",
            "Full spectrum — after production AsLS",
            after,
            "#1b9e77",
        ),
        (
            "01d_full_spectrum_removed_baseline_difference.png",
            "Full spectrum — baseline removed by production AsLS",
            removed,
            "#9467bd",
        ),
    )
    for name, title, values, color in specs:
        created.append(
            _single_trace(
                acq, dataset, group / name, title, values, color=color, zero=True
            )
        )
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(ins.ppm_axis, before, lw=0.55, label="Before")
    ax.plot(ins.ppm_axis, baseline, lw=0.8, label="Baseline")
    ax.plot(ins.ppm_axis, after, lw=0.65, label="After")
    ax.legend(frameon=False)
    _style(ax, ins.ppm_axis)
    created.append(
        _save(
            fig,
            group / "01e_full_spectrum_overlay_summary.png",
            acq,
            dataset,
            "Full spectrum — optional production baseline overlay",
        )
    )

    group = output / "02_full_spectrum_baseline_zoom"
    ylim = _baseline_focus_limits(acq)
    note = "Baseline-focused vertical magnification; large peaks intentionally clip."
    for name, title, values, color in (
        (
            "02a_full_spectrum_before_baseline_zoomed_y.png",
            "Baseline-focused full spectrum — before",
            before,
            "#1f77b4",
        ),
        (
            "02b_full_spectrum_estimated_baseline_zoomed_y.png",
            "Baseline-focused full spectrum — AsLS baseline",
            baseline,
            "#d95f02",
        ),
        (
            "02c_full_spectrum_after_baseline_zoomed_y.png",
            "Baseline-focused full spectrum — after",
            after,
            "#1b9e77",
        ),
        (
            "02d_full_spectrum_removed_baseline_zoomed_y.png",
            "Baseline-focused full spectrum — removed baseline",
            removed,
            "#9467bd",
        ),
    ):
        created.append(
            _single_trace(
                acq,
                dataset,
                group / name,
                title,
                values,
                color=color,
                ylim=ylim,
                zero=True,
                annotation=note,
            )
        )
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    for values, label in ((before, "Before"), (baseline, "Baseline"), (after, "After")):
        ax.plot(ins.ppm_axis, values, lw=0.7, label=label)
    ax.set_ylim(*ylim)
    ax.text(0.02, 0.96, note, transform=ax.transAxes, va="top")
    ax.legend(frameon=False)
    _style(ax, ins.ppm_axis)
    created.append(
        _save(
            fig,
            group / "02e_full_spectrum_baseline_zoomed_overlay.png",
            acq,
            dataset,
            "Baseline-focused full spectrum — optional overlay",
        )
    )

    group = output / "03_product_region_baseline"
    xlim = (5.60, 6.00)
    for name, title, values, color in (
        (
            "03a_product_region_before_baseline_5p60_to_6p00ppm.png",
            "Product context — before production baseline",
            before,
            "#1f77b4",
        ),
        (
            "03b_product_region_estimated_baseline_5p60_to_6p00ppm.png",
            "Product context — AsLS baseline only",
            baseline,
            "#d95f02",
        ),
        (
            "03c_product_region_after_baseline_5p60_to_6p00ppm.png",
            "Product context — after production AsLS",
            after,
            "#1b9e77",
        ),
        (
            "03d_product_region_removed_baseline_5p60_to_6p00ppm.png",
            "Product context — removed baseline",
            removed,
            "#9467bd",
        ),
    ):
        created.append(
            _single_trace(
                acq,
                dataset,
                group / name,
                title,
                values,
                color=color,
                xlim=xlim,
                zero=True,
            )
        )
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    for values, label in ((before, "Before"), (baseline, "Baseline"), (after, "After")):
        ax.plot(ins.ppm_axis, values, lw=0.8, label=label)
    ax.legend(frameon=False)
    _style(ax, ins.ppm_axis, xlim)
    created.append(
        _save(
            fig,
            group / "03e_product_region_baseline_overlay_5p60_to_6p00ppm.png",
            acq,
            dataset,
            "Product context — optional baseline overlay",
        )
    )
    return created


def reference_family(acq, output, dataset):
    ppm = np.asarray(acq.inspection.ppm_axis)
    y = np.real(acq.inspection.phased_spectrum)
    group = output / "04_reference_region"
    xlim = (4.70, 5.30)
    created = []
    for name, title in (
        (
            "04a_reference_region_before_referencing_4p70_to_5p30ppm.png",
            "Reference region — metadata axis before optional reference stage",
        ),
        (
            "04b_reference_region_after_referencing_4p70_to_5p30ppm.png",
            "Reference region — production metadata reference result",
        ),
    ):
        fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
        ax.plot(ppm, y, lw=0.8)
        ax.axvline(5.0, color="#d95f02", ls="--", label="Header reference 5.000 ppm")
        ax.text(
            0.02,
            0.96,
            "Calculated production shift: 0.000 ppm",
            transform=ax.transAxes,
            va="top",
        )
        ax.legend(frameon=False)
        _style(ax, ppm, xlim)
        created.append(_save(fig, group / name, acq, dataset, title))
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(ppm, y, lw=1.0, label="Before reference stage")
    ax.plot(ppm, y, lw=0.7, ls="--", label="After reference stage (identical)")
    ax.axvline(5.0, color="#d95f02", ls=":")
    ax.legend(frameon=False)
    _style(ax, ppm, xlim)
    created.append(
        _save(
            fig,
            group / "04c_reference_region_expected_location_and_noop_overlay.png",
            acq,
            dataset,
            "Reference region — 0.000 ppm no-op overlay",
        )
    )
    created.append(
        _single_trace(
            acq,
            dataset,
            group / "04d_reference_shift_difference_zero.png",
            "Reference operation difference — identically zero",
            np.zeros_like(y),
            xlim=xlim,
            zero=True,
            annotation="After − before = 0.000 a.u.; x-axis shift = 0.000 ppm",
        )
    )
    return created


def phase_family(acq, output, dataset):
    ppm = np.asarray(acq.inspection.ppm_axis)
    before = np.real(acq.inspection.fft_spectrum)
    after = np.real(acq.inspection.phased_spectrum)
    difference = after - before
    group = output / "05_phase_correction"
    created = [
        _single_trace(
            acq,
            dataset,
            group / "05a_full_spectrum_before_phase_correction.png",
            "Full spectrum — before stored phase correction",
            before,
            zero=True,
        ),
        _single_trace(
            acq,
            dataset,
            group / "05b_full_spectrum_after_phase_correction.png",
            "Full spectrum — after stored phase correction",
            after,
            color="#1b9e77",
            zero=True,
        ),
        _single_trace(
            acq,
            dataset,
            group / "05c_full_spectrum_phase_effect_difference.png",
            "Full spectrum — phase correction effect",
            difference,
            color="#9467bd",
            zero=True,
        ),
    ]
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(ppm, before, lw=0.6, label="Before phase")
    ax.plot(ppm, after, lw=0.7, label="After phase")
    ax.text(
        0.02,
        0.96,
        f"Stored inverse phase: p0={acq.inspection.phase0_deg:g}°, p1={acq.inspection.phase1_deg:g}°",
        transform=ax.transAxes,
        va="top",
    )
    ax.legend(frameon=False)
    _style(ax, ppm)
    created.append(
        _save(
            fig,
            group / "05d_full_spectrum_phase_settings_overlay.png",
            acq,
            dataset,
            "Stored phase correction — settings and optional overlay",
        )
    )
    return created


def integration_family(
    acq, output, dataset, prefix="06", dirname="06_integration_comparison"
):
    ppm = np.asarray(acq.inspection.ppm_axis)
    corrected = np.asarray(acq.inspection.corrected_real)
    picked_ppm = np.asarray(acq.picked.ppm_axis)
    quantitative = np.asarray(acq.picked.quantitative_corrected)
    bounds = _stage_bounds(acq)
    group = output / dirname
    focus = (ppm >= 5.60) & (ppm <= 6.00)
    created = []
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(picked_ppm, quantitative, lw=0.85)
    if bounds:
        mask = (picked_ppm >= min(bounds)) & (picked_ppm <= max(bounds))
        ax.fill_between(
            picked_ppm[mask], 0, np.maximum(quantitative[mask], 0), alpha=0.32
        )
        ax.axvline(bounds[0], color="#d95f02")
        ax.axvline(bounds[1], color="#d95f02")
    _style(ax, picked_ppm, (5.60, 6.00))
    created.append(
        _save(
            fig,
            group / f"{prefix}a_stage1_variable_width_integration_only.png",
            acq,
            dataset,
            f"Stage-1 variable-width integration — area {acq.target.positive_area if acq.target else 0:.2f}",
        )
    )
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(ppm[focus], corrected[focus], lw=0.85)
    x, _, chord, positive = local_integration_fill(ppm, corrected, *TARGET_WINDOW)
    ax.plot(x, chord, color="#d95f02", ls="--")
    ax.fill_between(x, chord, chord + positive, alpha=0.32)
    _style(ax, ppm, (5.60, 6.00))
    created.append(
        _save(
            fig,
            group / f"{prefix}b_fixed_window_integration_5p70_to_5p90ppm_only.png",
            acq,
            dataset,
            f"Fixed 5.70–5.90 ppm integration — area {acq.fixed_product.positive_area:.2f}",
        )
    )
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(ppm[focus], corrected[focus], lw=0.85)
    if acq.left_peak:
        x, _, chord, positive = local_integration_fill(
            ppm, corrected, acq.left_peak["from_ppm"], acq.left_peak["to_ppm"]
        )
        ax.plot(x, chord, color="#d95f02", ls="--")
        ax.fill_between(x, chord, chord + positive, alpha=0.32)
    _style(ax, ppm, (5.60, 6.00))
    created.append(
        _save(
            fig,
            group / f"{prefix}c_left_line_valley_to_valley_integration_only.png",
            acq,
            dataset,
            f"Left-line valley-to-valley integration — area {acq.left_peak['area'] if acq.left_peak else 0:.2f}",
        )
    )
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(ppm[focus], corrected[focus], lw=0.85)
    ax.axvline(TARGET_WINDOW[0], color="#f58518", ls="--", label="Fixed window")
    ax.axvline(TARGET_WINDOW[1], color="#f58518", ls="--")
    if bounds:
        ax.axvline(bounds[0], color="#4c78a8", label="Stage-1")
        ax.axvline(bounds[1], color="#4c78a8")
    if acq.left_peak:
        ax.axvline(
            acq.left_peak["from_ppm"], color="#54a24b", ls=":", label="Left line"
        )
        ax.axvline(acq.left_peak["to_ppm"], color="#54a24b", ls=":")
    ax.legend(frameon=False)
    _style(ax, ppm, (5.60, 6.00))
    created.append(
        _save(
            fig,
            group / f"{prefix}d_integration_boundaries_guide_no_fill.png",
            acq,
            dataset,
            "Product integration boundaries — guide without fill",
        )
    )
    fig, axes = plt.subplots(1, 3, figsize=(15, 5), layout="constrained")
    labels = ("Stage-1", "Fixed window", "Left line")
    for ax, label in zip(axes, labels):
        ax.plot(ppm[focus], corrected[focus], lw=0.75)
        ax.set_title(label)
        _style(ax, ppm, (5.60, 6.00))
    if bounds:
        axes[0].axvspan(*bounds, alpha=0.18)
    axes[1].axvspan(*TARGET_WINDOW, alpha=0.18, color="#f58518")
    if acq.left_peak:
        axes[2].axvspan(
            acq.left_peak["from_ppm"],
            acq.left_peak["to_ppm"],
            alpha=0.18,
            color="#54a24b",
        )
    created.append(
        _save(
            fig,
            group / f"{prefix}e_integration_methods_side_by_side_summary.png",
            acq,
            dataset,
            "Integration methods — side-by-side summary",
        )
    )
    return created


def worked_example(acq, output, dataset, prefix, dirname):
    group = output / dirname
    ins = acq.inspection
    ppm = np.asarray(ins.ppm_axis)
    created = []
    decimate = max(1, len(ins.raw_fid) // 4000)
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(
        ins.time_s[::decimate], np.real(ins.raw_fid)[::decimate], lw=0.7, label="Real"
    )
    ax.plot(
        ins.time_s[::decimate],
        np.imag(ins.raw_fid)[::decimate],
        lw=0.7,
        label="Imaginary",
    )
    ax.set_xlabel("Acquisition time (s)")
    ax.set_ylabel("FID amplitude (a.u.)")
    ax.legend(frameon=False)
    ax.grid(alpha=0.2)
    created.append(
        _save(
            fig,
            group / f"{prefix}a_raw_complex_fid.png",
            acq,
            dataset,
            "Worked example — raw complex FID",
        )
    )
    created.append(
        _single_trace(
            acq,
            dataset,
            group / f"{prefix}b_full_spectrum_after_stored_phase.png",
            "Worked example — spectrum after stored phase",
            np.real(ins.phased_spectrum),
            zero=True,
        )
    )
    created.append(
        _single_trace(
            acq,
            dataset,
            group / f"{prefix}c_production_asls_baseline_only.png",
            "Worked example — production AsLS baseline",
            np.asarray(ins.als_baseline),
            color="#d95f02",
            zero=True,
        )
    )
    created.append(
        _single_trace(
            acq,
            dataset,
            group / f"{prefix}d_product_region_after_baseline_5p60_to_6p00ppm.png",
            "Worked example — corrected product context",
            np.asarray(ins.corrected_real),
            color="#1b9e77",
            xlim=(5.60, 6.00),
            zero=True,
        )
    )
    corrected = np.asarray(ins.corrected_real)
    focus = (ppm >= 5.60) & (ppm <= 6.00)
    bounds = _stage_bounds(acq)
    fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
    ax.plot(ppm[focus], corrected[focus], lw=0.85)
    ax.axvline(TARGET_WINDOW[0], color="#f58518", ls="--", label="Fixed window")
    ax.axvline(TARGET_WINDOW[1], color="#f58518", ls="--")
    if bounds:
        ax.axvline(bounds[0], color="#4c78a8", label="Stage-1")
        ax.axvline(bounds[1], color="#4c78a8")
    if acq.left_peak:
        ax.axvline(
            acq.left_peak["from_ppm"], color="#54a24b", ls=":", label="Left line"
        )
        ax.axvline(acq.left_peak["to_ppm"], color="#54a24b", ls=":")
    ax.legend(frameon=False)
    _style(ax, ppm, (5.60, 6.00))
    created.append(
        _save(
            fig,
            group / f"{prefix}e_product_integration_boundaries_guide.png",
            acq,
            dataset,
            "Worked example — product integration boundaries",
        )
    )
    return created


def contact_sheet(paths, output, dataset, title, filename):
    paths = [Path(path) for path in paths if Path(path).suffix.lower() == ".png"]
    cols = min(4, len(paths))
    rows = int(np.ceil(len(paths) / cols))
    fig, axes = plt.subplots(rows, cols, figsize=(4.7 * cols, 4.0 * rows))
    axes = np.asarray(axes).reshape(-1)
    for ax, path in zip(axes, paths):
        ax.imshow(plt.imread(path))
        ax.set_title(path.name, fontsize=8)
        ax.axis("off")
    for ax in axes[len(paths) :]:
        ax.axis("off")
    fig.suptitle(format_dataset_plot_title(dataset, title), fontsize=16)
    destination = output / "09_contact_sheets" / filename
    destination.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destination, dpi=160, bbox_inches="tight")
    plt.close(fig)
    return destination


def _write_docs(output, dataset, primary, five_fifteen, created):
    readme = f"""# {dataset} plot cleanup and interactive explorer

This sibling package reorganizes the completed scientific audit into single-purpose, group-prefixed review figures. It does not replace or modify production processing or the original inspection package.

Start with `09_contact_sheets/A_groups_01_02_overview.png`, then open the individual `01a` through `01d` files. For the integration issue, use `06d_integration_boundaries_guide_no_fill.png` and the Group 08 sequence.

The full interactive viewer is available in `notebooks/interactive_nmr_processing_explorer.ipynb`; a lightweight standalone Matplotlib app is in `scripts/nmr/interactive_processing_explorer.py`. Exports are restricted to this package's `interactive/exports` directory and are marked exploratory.
"""
    index = f"""# Plot group index

| Group | Story | Acquisition |
|---|---|---|
| 01 | Full-spectrum production baseline, standard scale | `{primary.dx.name}` |
| 02 | Full-spectrum production baseline, magnified y scale | `{primary.dx.name}` |
| 03 | Product-region baseline views, 5.60–6.00 ppm | `{primary.dx.name}` |
| 04 | Metadata reference stage and 0.000 ppm no-op | `{primary.dx.name}` |
| 05 | Stored p0/p1 phase correction | `{primary.dx.name}` |
| 06 | Stage-1, fixed-window and left-line integration | `{five_fifteen.dx.name}` |
| 07 | Curated primary 3:45 pull sequence | `{primary.dx.name}` |
| 08 | Curated 5:15 pull integration sequence | `{five_fifteen.dx.name}` |
| 09 | Ordered contact sheets | Both worked examples |

Suffix convention: `a` is before/input, `b` is intermediate/baseline/reference, `c` is after/result, `d` is difference/derived/guide, and `e` is an optional summary or fifth step. The best first figure is `01a`, followed by `01b`, `01c`, and `01d`.
"""
    viewer = """# Interactive viewer

## Notebook (recommended)

From the repository root in the `ai` environment:

```powershell
conda activate ai
jupyter lab notebooks/interactive_nmr_processing_explorer.ipynb
```

The notebook provides a six-file selector; p0/p1 controls; production/manual reference selection and Δppm; none, production AsLS, ABD polynomial, and arPLS baselines; lambda, asymmetry, iteration and polynomial-degree controls; full/reference/silane/starting-material/product zooms; manual x limits; automatic or manual y limits; display-mode and boundary toggles; live Stage-1/fixed/left/starting/silane values; reset-to-production; and safe PNG/JSON export.

## Standalone app

```powershell
conda activate ai
python scripts/nmr/interactive_processing_explorer.py
```

Pass a `.dx` path as the positional argument to open another spectrum. The standalone Matplotlib app provides fast phase, baseline-method, view, zoom, boundary toggles, and production reset. Use the notebook for the complete parameter and export interface.

All outputs are exploratory only. Export paths are constrained to `results/nmr_processing_inspection/chemyx_demo_081026_v3_plot_cleanup_v3/interactive/exports`; production results and both audit packages cannot be selected as export targets.
"""
    (output / "README.md").write_text(readme, encoding="utf-8")
    (output / "PLOT_GROUP_INDEX.md").write_text(index, encoding="utf-8")
    (output / "INTERACTIVE_VIEWER_README.md").write_text(viewer, encoding="utf-8")
    table = output / "tables/acquisitions.csv"
    table.parent.mkdir(parents=True, exist_ok=True)
    with table.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "role",
                "file",
                "timestamp_source",
                "timestamp",
                "scans",
                "gain",
            ),
        )
        writer.writeheader()
        for role, acq in (("primary", primary), ("5:15 pull", five_fifteen)):
            writer.writerow(
                {
                    "role": role,
                    "file": acq.dx.name,
                    "timestamp_source": acq.timestamp_source,
                    "timestamp": acq.timestamp.isoformat(),
                    "scans": acq.inspection.metadata.get("$NS", ""),
                    "gain": acq.inspection.metadata.get("$RG", ""),
                }
            )
    manifest_files = sorted(
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path.name != "manifest.json"
    )
    manifest = {
        "dataset_display_name": dataset,
        "exploratory_only": True,
        "source_package": "results/nmr_processing_inspection/chemyx_demo_081026_v3",
        "files": manifest_files,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )


def main(argv=None):
    args = _parser().parse_args(argv)
    series = args.series_dir.resolve()
    output = args.output.resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite existing cleanup package: {output}")
    if args.dataset_display_name:
        dataset = args.dataset_display_name
    elif (SOURCE_AUDIT / "manifest.json").is_file():
        source_manifest = json.loads(
            (SOURCE_AUDIT / "manifest.json").read_text(encoding="utf-8")
        )
        dataset = source_manifest["dataset_display_name"]
    else:
        dataset = resolve_dataset_display_name(input_paths=series)
    acquisitions = [
        _load_acquisition(run)
        for run in sorted(series.iterdir())
        if (run / "raw_nmr").is_dir()
    ]
    primary = next(acq for acq in acquisitions if PRIMARY_TOKEN in acq.dx.name)
    five_fifteen = next(
        acq for acq in acquisitions if FIVE_FIFTEEN_TOKEN in acq.dx.name
    )
    output.mkdir(parents=True)
    created = []
    g010203 = baseline_families(primary, output, dataset)
    g04 = reference_family(primary, output, dataset)
    g05 = phase_family(primary, output, dataset)
    g06 = integration_family(five_fifteen, output, dataset)
    g07 = worked_example(primary, output, dataset, "07", "07_worked_example_primary")
    g08 = worked_example(
        five_fifteen, output, dataset, "08", "08_worked_example_515pull"
    )
    created += g010203 + g04 + g05 + g06 + g07 + g08
    g01 = [p for p in g010203 if p.name.startswith("01")]
    g02 = [p for p in g010203 if p.name.startswith("02")]
    g03 = [p for p in g010203 if p.name.startswith("03")]
    sheets = [
        contact_sheet(
            g01 + g02,
            output,
            dataset,
            "Contact sheet A — Groups 01 and 02",
            "A_groups_01_02_overview.png",
        ),
        contact_sheet(
            g03 + g04 + g05,
            output,
            dataset,
            "Contact sheet B — Product, reference, and phase",
            "B_product_reference_phase.png",
        ),
        contact_sheet(
            g06,
            output,
            dataset,
            "Contact sheet C — Integration comparison",
            "C_integration_comparison.png",
        ),
        contact_sheet(
            g07,
            output,
            dataset,
            "Contact sheet D — Primary worked example",
            "D_primary_worked_example.png",
        ),
        contact_sheet(
            g08,
            output,
            dataset,
            "Contact sheet E — 5:15 pull worked example",
            "E_515pull_worked_example.png",
        ),
    ]
    created += sheets
    (output / "interactive/exports").mkdir(parents=True)
    _write_docs(output, dataset, primary, five_fifteen, created)
    print(output)


if __name__ == "__main__":
    main()
