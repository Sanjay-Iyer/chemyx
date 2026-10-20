"""Dataset-labelled 08/09 plots; primary dynamic area and explicit QC markers."""
from __future__ import annotations

import csv
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
from matplotlib import pyplot as plt
import numpy as np

from .plot_titles import format_dataset_plot_title
from .scipy_peak_benchmark_plots import draw_integration


def render(spectra, rows, rates, normalized, dataset, raw_config, output):
    output = Path(output)
    dataset = format_dataset_plot_title(dataset, "")
    figures = raw_config.get("target_peak", {}).get("figures", {})
    formats = tuple(figures.get("formats", ["png", "svg", "pdf"]))
    if not formats or set(formats) - {"png", "svg", "pdf"}:
        raise ValueError("Plot formats must be PNG, SVG, or PDF")
    dpi = int(figures.get("dpi", 200))
    manifest = []

    def save(fig, name, description, *, observation="", role="primary_dynamic"):
        title = format_dataset_plot_title(dataset, description)
        fig.suptitle(title, fontsize=12, fontweight="bold", y=.995)
        fig.tight_layout(rect=[0, .025, 1, .94])
        for fmt in formats:
            path = output / "plots" / (name + "." + fmt)
            path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(path, dpi=dpi, metadata={"Title": title})
            manifest.append({"figure": str(path), "dataset": dataset, "visible_title": title,
                "format": fmt, "observation": observation, "role": role,
                "primary_area_method": "scipy_width95_prominence8"})
        plt.close(fig)

    def individual(s, row, name):
        fig, ax = plt.subplots(figsize=(10.5, 6.2))
        display = dict(row)
        if math.isfinite(row.get("candidate_area", float("nan"))) and not math.isfinite(row["area"]):
            display.update(area_dynamic=row["candidate_area"], area_signed=row.get("candidate_signed_area", float("nan")))
        draw_integration(ax, s, display)
        ax.set_title(f"8σ prominence; rel_height=0.95; acquired {s['acquisition_time']}", fontsize=9)
        fig.text(.5, .012, "Shading uses original unsmoothed ACME/ALS data; warnings describe analysis only.", ha="center", fontsize=8)
        save(fig, name, f"{s['observation']} Primary Dynamic Peak Integration", observation=s["observation"])

    for s, row in zip(spectra, rows):
        individual(s, row, f"observations/{s['observation']}_region_dynamic_width95")
        fig, ax = plt.subplots(figsize=(11, 5.5))
        ax.plot(s["ppm"], s["intensity"], lw=.8)
        ax.axvspan(5.6, 6.0, color="#56b4e9", alpha=.15, label="Target-center search region")
        ax.set(xlim=(12, 0), xlabel="Chemical shift (ppm)", ylabel="ACME/ALS real intensity (a.u.)")
        ax.legend(fontsize=8)
        save(fig, f"observations/{s['observation']}_full_spectrum", f"{s['observation']} Full Processed Spectrum", observation=s["observation"])
    # The inherited dashboard selects this before older observation files.
    individual(spectra[-1], rows[-1], "000_latest_region")
    times = [r["elapsed_hours"] for r in rows]
    areas = [r["area"] for r in rows]
    bad = [i for i, r in enumerate(rows) if math.isfinite(r["area"]) and not r["analysis_quality_pass"]]

    def time_axis(ax):
        ax.set_xlabel("Hours from first JCAMP acquisition (metadata only)")
        ax.grid(alpha=.2)
        span = max(times, default=0.)
        if span > 0:
            ax.set_xlim(-.02 * span, 1.02 * span)

    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    ax.plot(times, areas, "o-", color="#0072b2", label="Primary dynamic area: width 0.95 / prominence 8σ")
    if bad:
        ax.plot([times[i] for i in bad], [areas[i] for i in bad], "x", color="#d55e00", ms=10, label="Ambiguous/flagged candidate area")
    missing = [i for i, r in enumerate(rows) if not math.isfinite(r["area"])]
    if missing:
        ax.scatter([times[i] for i in missing], [.035] * len(missing), marker="|", s=90, color="gray",
                   transform=ax.get_xaxis_transform(), label="Missing dynamic result (no area value)")
    ax.set_ylabel("Primary positive area (a.u.·ppm)")
    ax.legend(fontsize=8)
    time_axis(ax)
    save(fig, "area_vs_time", "Primary Dynamic Peak Area vs Time")

    fig, axes = plt.subplots(2, 1, figsize=(10.5, 8), sharex=True)
    axes[0].plot(times, areas, "o-", label="Primary dynamic area")
    axes[0].plot(times, [s["fixed_area"] for s in spectra], "s--", color="gray", label="Fixed 5.70–5.90 comparison")
    axes[0].set_ylabel("Positive area (a.u.·ppm)")
    axes[0].legend(fontsize=8)
    axes[1].plot(times, [r["area"] - s["fixed_area"] for r, s in zip(rows, spectra)], "o-")
    axes[1].axhline(0, lw=.8, color="gray")
    axes[1].set_ylabel("Dynamic − fixed (a.u.·ppm)")
    for ax in axes:
        time_axis(ax)
    save(fig, "dynamic_vs_fixed", "Dynamic Area and Fixed-Window Comparison", role="comparison")

    fig, axes = plt.subplots(2, 1, figsize=(10.5, 8), sharex=True)
    axes[0].plot(times, [r["peak_center_ppm"] for r in rows], "o-", label="Selected center")
    axes[0].plot(times, [r["boundary_low_ppm"] for r in rows], "--", label="Low-ppm boundary")
    axes[0].plot(times, [r["boundary_high_ppm"] for r in rows], "--", label="High-ppm boundary")
    axes[0].set_ylabel("Chemical shift (ppm)")
    axes[0].legend(fontsize=8)
    axes[1].plot(times, [r["peak_width_ppm"] for r in rows], "o-", label="Half-prominence detection width")
    axes[1].set_ylabel("Width (ppm)")
    axes[1].legend(fontsize=8)
    for ax in axes:
        time_axis(ax)
    save(fig, "center_boundaries_and_width", "Dynamic Peak Center Boundaries and Width")

    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    if rates:
        ax.plot([r["elapsed_hours"] for r in rates], [r["absolute_rate_per_hour"] for r in rates], "o-", label="Candidate interval rate")
        ax.plot([r["elapsed_hours"] for r in rates], [r["rolling_slope"] for r in rates], "s--", label="Rolling candidate slope")
        ax.legend(fontsize=8)
    ax.set_ylabel("Primary dynamic area change per hour")
    time_axis(ax)
    save(fig, "rate_vs_time", "Primary Dynamic Area Rate vs Time")

    fig, ax = plt.subplots(figsize=(10.5, 5.6))
    for mode in sorted({r["mode"] for r in normalized}):
        rr = [r for r in normalized if r["mode"] == mode]
        ax.plot([r["elapsed_hours"] for r in rr], [r["normalized_area"] for r in rr], "o-", label=mode)
    if normalized:
        ax.legend(fontsize=8)
    ax.set_ylabel("Normalized primary dynamic area")
    time_axis(ax)
    save(fig, "normalized_area_vs_time", "Normalized Primary Dynamic Area vs Time")

    fig, ax = plt.subplots(figsize=(10.5, 6))
    for s in spectra:
        ax.plot(s["ppm"], s["intensity"], lw=.9, label=f"{s['observation']} {s['acquisition_time'][11:19]}")
    ax.set(xlim=(6.12, 5.48), xlabel="Chemical shift (ppm)", ylabel="ACME/ALS real intensity (a.u.)")
    ax.legend(fontsize=7, ncol=2)
    ax.grid(alpha=.2)
    save(fig, "spectral_evolution", "Focused Spectral Evolution")
    with (output / "plot_manifest.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(manifest[0]))
        writer.writeheader()
        writer.writerows(manifest)
    return manifest
