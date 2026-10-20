"""Inspectable static figures for the offline real-data boundary benchmark."""
from __future__ import annotations

import json
import math
import textwrap
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np

from .plot_titles import format_dataset_plot_title
from .scipy_peak_comparison import METHODS, METHOD_LABELS, integration_trace

COLORS = dict(zip(METHODS, ["black", "#d55e00", "#e69f00", "#009e73", "#0072b2", "#cc79a7", "#56b4e9", "#6f4e37"]))


class FigureWriter:
    def __init__(self, root, *, dpi=120):
        self.root, self.dpi, self.manifest = Path(root), dpi, []

    def save(self, fig, relative, dataset, description, **provenance):
        dataset = format_dataset_plot_title(dataset, "")
        title = format_dataset_plot_title(dataset, description)
        fig.suptitle(title, fontsize=12, fontweight="bold", y=.995)
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=self.dpi, metadata={"Title": title, "Description": json.dumps(provenance, default=str)})
        self.manifest.append({"file": str(path), "dataset": dataset, "visible_title": title,
                              "format": "png", **provenance})
        plt.close(fig)
        return str(path)


def finite(value):
    return isinstance(value, (int, float)) and math.isfinite(value)


def draw_integration(ax, spectrum, row, *, small=False):
    x, y = spectrum["ppm"], spectrum["intensity"]
    production = row["method"] == "production_variable"
    if production:
        x, y = spectrum["regional_ppm"], spectrum["regional_intensity"]
    ax.plot(x, y, color="#222222", lw=1, label="Processed real signal" if not production else "Production regional-corrected signal")
    expected = json.loads(row.get("parameters") or "{}").get("expected_ppm", 5.79)
    ax.axvline(expected, color="#999999", linestyle=":", lw=1, label=f"Expected {expected:.3f}")
    if finite(row.get("peak_center_ppm")):
        center = row["peak_center_ppm"]
        ax.axvline(center, color="#0072b2", linestyle="-.", label=f"Center {center:.5f}")
    low, high = row.get("boundary_low_ppm"), row.get("boundary_high_ppm")
    if finite(low) and finite(high) and row.get("integration_valid"):
        if production:
            mask = (x >= low) & (x <= high)
            xx, yy, baseline = x[mask], y[mask], np.zeros(np.count_nonzero(mask))
        else:
            xx, yy, baseline = integration_trace(x, y, low, high, interpolate=row["method"] != "fixed")
        ax.axvline(low, color="#009e73", linestyle="--", label=f"Low boundary {low:.5f}")
        ax.axvline(high, color="#d55e00", linestyle="--", label=f"High boundary {high:.5f}")
        ax.plot(xx, baseline, color="#cc79a7", linestyle="--", label="Integration baseline")
        ax.fill_between(xx, baseline, baseline + np.maximum(yy - baseline, 0),
                        color="#56b4e9", alpha=.45, label="Integrated positive area")
    flags = row.get("qc_flags", [])
    if isinstance(flags, str):
        flags = flags.split("; ") if flags else []
    status = row["detection_status"]
    area = row.get("area_dynamic", float("nan"))
    note = f"Area={area:.5g}; signed={row.get('area_signed', float('nan')):.5g}\n{status}"
    if flags:
        note += "\n" + "\n".join(textwrap.wrap("; ".join(flags), width=68 if not small else 43))
    ax.text(.015, .97, note, transform=ax.transAxes, va="top", fontsize=7 if small else 8,
            bbox={"facecolor": "white", "alpha": .87, "edgecolor": "#dddddd"})
    ax.set_xlim(6.12, 5.48)
    view = (x >= 5.48) & (x <= 6.12)
    yy = y[view]
    span = max(float(np.ptp(yy)), 1.)
    ax.set_ylim(float(yy.min() - .06 * span), float(yy.max() + .43 * span))
    ax.set(xlabel="Chemical shift (ppm)", ylabel="Intensity (a.u.)")
    ax.grid(alpha=.17)
    ax.legend(fontsize=5.5 if small else 7, loc="upper right")


def individual(writer, dataset, spectrum, row):
    fig, ax = plt.subplots(figsize=(10, 6.1))
    draw_integration(ax, spectrum, row)
    cfg = json.loads(row.get("parameters") or "{}")
    details = (f"search {cfg.get('search_low', '—')}–{cfg.get('search_high', '—')} ppm; "
        f"prom≥{cfg.get('prominence_snr', '—')}σ; height≥{cfg.get('height_snr', '—')}σ; "
        f"width {cfg.get('minimum_width_ppm', '—')}–{cfg.get('maximum_width_ppm', '—')} ppm; "
        f"distance {cfg.get('distance_ppm', '—')} ppm\n"
        f"max shift {cfg.get('maximum_shift_ppm', '—')} ppm; smoothing {cfg.get('smoothing_ppm', '—')} ppm; "
        f"noise={spectrum['noise']:.4g}; acquired {spectrum['acquisition_time']}")
    ax.set_title(details, fontsize=8, pad=7)
    fig.tight_layout(rect=[0, 0, 1, .93])
    return writer.save(fig, Path(dataset) / "individual_spectra" / spectrum["observation"] /
        row["configuration"] / f"{row['method']}.png", dataset,
        f"{spectrum['observation']} {METHOD_LABELS[row['method']]} [{row['configuration']}]",
        observation=spectrum["observation"], method=row["method"], configuration=row["configuration"],
        area=row.get("area_dynamic"), boundary_low_ppm=row.get("boundary_low_ppm"), boundary_high_ppm=row.get("boundary_high_ppm"))


def observation_comparison(writer, dataset, spectrum, rows, configuration="reference"):
    fig, axes = plt.subplots(2, 3, figsize=(17, 10.5))
    methods = ["fixed", "width_50", "width_95", "prominence_bases", "local_minimum", "adaptive_noise"]
    for ax, method in zip(axes.flat, methods):
        row = next(r for r in rows if r["configuration"] == configuration and r["method"] == method)
        draw_integration(ax, spectrum, row, small=True)
        ax.set_title(METHOD_LABELS[method], fontsize=10)
    fig.tight_layout(rect=[0, 0, 1, .95])
    suffix = "" if configuration == "reference" else "_" + configuration
    writer.save(fig, Path(dataset) / "parameter_comparisons" / f"{spectrum['observation']}_methods{suffix}.png", dataset,
        f"{spectrum['observation']} Boundary Method Comparison [{configuration}]", observation=spectrum["observation"], configuration=configuration)


def dataset_figures(writer, dataset, spectra, rows):
    base = Path(dataset)
    reference = [r for r in rows if r["configuration"] == "reference"]
    for kind, description, field in [
        ("area_vs_time", "Peak Area vs Time", "area_dynamic"),
        ("center_vs_time", "Peak Center vs Time", "peak_center_ppm"),
        ("peak_width_vs_time", "Measured Half-Prominence Width vs Time", "peak_width_ppm")]:
        fig, ax = plt.subplots(figsize=(11, 6))
        methods = METHODS if field == "area_dynamic" else ("width_95",)
        for method in methods:
            rr = [r for r in reference if r["method"] == method]
            ax.plot([r["elapsed_hours"] for r in rr], [r.get(field, np.nan) for r in rr], "o-",
                    color=COLORS[method], label=METHOD_LABELS[method], lw=1, ms=4)
        if field == "area_dynamic":
            rr = [r for r in rows if r["method"] == "production_variable"]
            ax.plot([r["elapsed_hours"] for r in rr], [r.get(field, np.nan) for r in rr], "x--",
                    color="#888888", label="Existing variable-width comparator")
        ax.set(xlabel="Hours from first JCAMP acquisition", ylabel={"area_dynamic": "Positive area (a.u.·ppm)",
            "peak_center_ppm": "Peak center (ppm)", "peak_width_ppm": "Half-prominence width (ppm)"}[field])
        ax.grid(alpha=.2)
        ax.legend(fontsize=8, ncol=2)
        fig.tight_layout(rect=[0, 0, 1, .94])
        writer.save(fig, base / "time_series" / f"{kind}.png", dataset, description,
                    timing_source="acquisition_metadata.csv: JCAMP metadata only", configuration="reference")
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8))
    for method in METHODS[1:]:
        rr = [r for r in reference if r["method"] == method]
        xx = [r["elapsed_hours"] for r in rr]
        for ax, field in zip(axes, ("area_difference", "relative_difference_percent")):
            ax.plot(xx, [r.get(field, np.nan) for r in rr], "o-", color=COLORS[method], label=METHOD_LABELS[method], lw=1)
    for ax, label in zip(axes, ("Dynamic − fixed (a.u.·ppm)", "Difference (%; omitted if fixed ≤1)")):
        ax.axhline(0, color="gray", lw=.7)
        ax.set(xlabel="Hours from first JCAMP acquisition", ylabel=label)
        ax.grid(alpha=.2)
    axes[0].legend(fontsize=7)
    fig.tight_layout(rect=[0, 0, 1, .94])
    writer.save(fig, base / "time_series/dynamic_vs_fixed.png", dataset, "Dynamic vs Fixed Area", configuration="reference")
    fig, axes = plt.subplots(2, 4, figsize=(18, 8), sharex=True, sharey=True)
    for ax, method in zip(axes.flat, METHODS):
        rr = [r for r in reference if r["method"] == method]
        for field, label in (("boundary_low_ppm", "Low ppm"), ("boundary_high_ppm", "High ppm")):
            ax.plot([r["elapsed_hours"] for r in rr], [r.get(field, np.nan) for r in rr], "o-", label=label, lw=1)
        ax.set(title=METHOD_LABELS[method], xlabel="Elapsed hours", ylabel="Boundary (ppm)")
        ax.grid(alpha=.2)
        ax.legend(fontsize=7)
    fig.tight_layout(rect=[0, 0, 1, .94])
    writer.save(fig, base / "time_series/boundaries_vs_time.png", dataset, "Integration Boundaries vs Time", configuration="reference")
    fig, ax = plt.subplots(figsize=(11, 6.5))
    for s in spectra:
        ax.plot(s["ppm"], s["intensity"], label=f"{s['observation']} {s['acquisition_time'][11:19]}", lw=1)
    ax.axvspan(5.7, 5.9, color="gray", alpha=.08, label="Fixed window")
    ax.set(xlim=(6.12, 5.48), xlabel="Chemical shift (ppm)", ylabel="Processed real intensity (a.u.)")
    ax.legend(fontsize=8, ncol=2)
    ax.grid(alpha=.2)
    fig.tight_layout(rect=[0, 0, 1, .94])
    writer.save(fig, base / "time_series/spectrum_overlays.png", dataset, "ACME and ALS Spectrum Overlays")
    # Every detection profile is visible here, beyond the default-method time series.
    for method in METHODS[1:]:
        fig, axes = plt.subplots(2, 1, figsize=(12, 8.5), sharex=True)
        profiles = sorted(set(r["configuration"] for r in rows if r["method"] == method))
        for cfg in profiles:
            rr = [r for r in rows if r["method"] == method and r["configuration"] == cfg]
            axes[0].plot([r["elapsed_hours"] for r in rr], [r["area_dynamic"] for r in rr], "o-", lw=.8, ms=3, label=cfg)
            axes[1].plot([r["elapsed_hours"] for r in rr], [r["peak_center_ppm"] for r in rr], "o-", lw=.8, ms=3, label=cfg)
        axes[0].set(ylabel="Positive area (a.u.·ppm)")
        axes[1].set(xlabel="Hours from first JCAMP acquisition", ylabel="Peak center (ppm)")
        axes[0].legend(fontsize=7, ncol=3)
        for ax in axes:
            ax.grid(alpha=.2)
        fig.tight_layout(rect=[0, 0, 1, .94])
        writer.save(fig, base / "parameter_comparisons" / f"{method}_parameter_sweep.png", dataset,
                    f"{METHOD_LABELS[method]} Parameter Sweep")
