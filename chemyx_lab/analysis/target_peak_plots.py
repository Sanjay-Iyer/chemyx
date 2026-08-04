"""Publication and slide figures for the focused target-peak report."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .plot_titles import format_dataset_plot_title, resolve_dataset_display_name


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    return plt


def _np():
    import numpy as np

    return np


BLUE = "#1f5a85"
ORANGE = "#d97706"
GREEN = "#238636"
RED = "#b42318"
GRAY = "#667085"
LIGHT = "#e7eef4"


def _title(analysis, descriptive_title: str) -> str:
    """Format a focused-analysis title from its configured dataset identity."""

    return format_dataset_plot_title(
        _dataset_name(analysis),
        descriptive_title,
    )


def _dataset_name(analysis) -> str:
    return resolve_dataset_display_name(analysis.config.dataset_display_name)


def _peak_legend_label(analysis) -> str:
    label = " ".join(str(analysis.config.peak_label).split())
    return label if "peak" in label.casefold().split() else f"{label} peak"


def _style(ax, *, slide: bool = False) -> None:
    size = 15 if slide else 9
    ax.tick_params(labelsize=size - 1, direction="out", length=4)
    ax.xaxis.label.set_size(size)
    ax.yaxis.label.set_size(size)
    ax.title.set_size(size + 1)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, color="#d0d5dd", alpha=0.45, linewidth=0.6)


def _arrays(analysis):
    np = _np()
    rows = analysis.measurements
    return (
        np.asarray([r["elapsed_time_hours"] for r in rows], dtype=float),
        np.asarray([r["area"] for r in rows], dtype=float),
        np.asarray([r["area_ci_low"] for r in rows], dtype=float),
        np.asarray([r["area_ci_high"] for r in rows], dtype=float),
    )


def _completion_marker(ax, analysis, y=None, *, slide=False) -> None:
    result = analysis.completion
    if not result.complete or result.completion_elapsed_hours is None:
        return
    x = result.completion_elapsed_hours
    ax.axvline(x, color=GREEN, linestyle="--", linewidth=1.4, alpha=0.9)
    if y is not None and result.completion_index is not None:
        ax.scatter([x], [y[result.completion_index]], s=70 if slide else 42,
                   marker="D", facecolor="white", edgecolor=GREEN,
                   linewidth=1.7, zorder=6, label="Detected completion")


def _area_figure(analysis, size, slide=False):
    plt = _plt()
    np = _np()
    time, area, low, high = _arrays(analysis)
    fig, ax = plt.subplots(figsize=size)
    if analysis.config.figures.show_uncertainty:
        valid = np.isfinite(low) & np.isfinite(high)
        ax.fill_between(time[valid], low[valid], high[valid], color=BLUE, alpha=0.13)
    ax.plot(time, area, color=BLUE, linewidth=1.2, alpha=0.65)
    ax.scatter(time, area, s=58 if slide else 30, color=BLUE, edgecolor="white",
               linewidth=0.8, zorder=4, label=_peak_legend_label(analysis))
    _completion_marker(ax, analysis, area, slide=slide)
    ax.set_title(_title(analysis, "Area vs Time"))
    ax.set_xlabel("Elapsed time (hours)")
    ax.set_ylabel("Area")
    status = analysis.completion.status.replace("_", " ")
    ax.text(
        0.99,
        0.05,
        f"Status: {status}",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        color=GREEN if analysis.completion.complete else GRAY,
        fontsize=13 if slide else 8,
        bbox={"facecolor": "white", "edgecolor": "none", "alpha": 0.88, "pad": 2},
    )
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=12 if slide else 8, loc="upper left")
    fig.tight_layout()
    return fig


def _normalized_figure(analysis, size, slide=False):
    plt = _plt()
    fig, ax = plt.subplots(figsize=size)
    labels = {
        "fraction_of_max": "Fraction of maximum",
        "relative_to_first": "Relative to first",
        "zero_to_one": "Zero-to-one",
    }
    colors = [BLUE, ORANGE, GREEN]
    for color, mode in zip(colors, analysis.config.normalization_modes):
        rows = [r for r in analysis.normalized if r["normalization_mode"] == mode]
        ax.plot([r["elapsed_time_hours"] for r in rows],
                [r["normalized_area"] for r in rows], "o-", color=color,
                linewidth=1.4, markersize=5, label=labels[mode])
    _completion_marker(ax, analysis)
    ax.set_title(_title(analysis, "Normalized Area vs Time"))
    ax.set_xlabel("Elapsed time (hours)")
    ax.set_ylabel("Normalized area")
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=12 if slide else 8)
    fig.tight_layout()
    return fig


def _change_figure(analysis, size, slide=False):
    plt = _plt()
    np = _np()
    rows = analysis.rates
    t = np.asarray([r["elapsed_time_hours"] for r in rows], dtype=float)
    delta = np.asarray([r["delta_area"] for r in rows], dtype=float)
    rate = np.asarray([r["area_rate_per_hour"] for r in rows], dtype=float)
    fig, axes = plt.subplots(2, 1, figsize=size, sharex=True)
    for ax, values, ylabel, color in (
        (axes[0], delta, "Delta Area", BLUE),
        (axes[1], rate, "dArea/dt (area/hour)", ORANGE),
    ):
        ax.axhline(0, color=GRAY, linewidth=0.8)
        ax.bar(t, np.nan_to_num(values, nan=0.0), width=0.055, color=color, alpha=0.85)
        ax.set_ylabel(ylabel)
        _style(ax, slide=slide)
    fig.suptitle(
        _title(analysis, "Area Change Between Acquisitions"),
        fontsize=17 if slide else 11,
    )
    axes[0].set_title("Area change per interval")
    axes[1].set_title("Area change rate")
    axes[-1].set_xlabel("Elapsed time (hours)")
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return fig


def _rate_figure(analysis, size, slide=False):
    plt = _plt()
    np = _np()
    rows = analysis.rates
    time = np.asarray([r["elapsed_time_hours"] for r in rows], dtype=float)
    slope = np.asarray([r["rolling_slope_per_hour"] for r in rows], dtype=float)
    low = np.asarray([r["rolling_slope_ci_low"] for r in rows], dtype=float)
    high = np.asarray([r["rolling_slope_ci_high"] for r in rows], dtype=float)
    threshold = analysis.config.completion.absolute_slope_threshold_per_hour
    fig, ax = plt.subplots(figsize=size)
    ax.axhspan(-threshold, threshold, color=GREEN, alpha=0.10,
               label="Completion threshold band")
    valid = np.isfinite(low) & np.isfinite(high)
    ax.fill_between(time[valid], low[valid], high[valid], color=ORANGE, alpha=0.16,
                    label="95% slope interval")
    ax.axhline(0, color=GRAY, linewidth=0.9)
    ax.plot(time, slope, "o-", color=ORANGE, linewidth=1.5, markersize=5,
            label=f"Local slope ({analysis.config.rolling_window}-point window)")
    _completion_marker(ax, analysis)
    ax.set_title(_title(analysis, "Area Change Rate"))
    ax.set_xlabel("Elapsed time (hours)")
    ax.set_ylabel("Area rate (area/hour)")
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=11 if slide else 8)
    fig.tight_layout()
    return fig


def _percent_figure(analysis, size, slide=False):
    plt = _plt()
    np = _np()
    rows = analysis.rates
    time = np.asarray([r["elapsed_time_hours"] for r in rows], dtype=float)
    percent = np.asarray([r["percent_change_per_interval"] for r in rows], dtype=float)
    threshold = analysis.config.completion.percent_change_threshold
    fig, ax = plt.subplots(figsize=size)
    ax.axhspan(-threshold, threshold, color=GREEN, alpha=0.10,
               label="Stable-change threshold")
    colors = np.where(percent >= 0, BLUE, RED)
    ax.bar(time, np.nan_to_num(percent, nan=0.0), width=0.055, color=colors, alpha=0.85)
    ax.axhline(0, color=GRAY, linewidth=0.9)
    ax.set_title(_title(analysis, "Percent Change per Interval"))
    ax.set_xlabel("Elapsed time (hours)")
    ax.set_ylabel("Area change (%)")
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=11 if slide else 8)
    fig.tight_layout()
    return fig


def _spectra_figure(
    analysis,
    size,
    slide=False,
    *,
    stacked=True,
    descriptive_title: str | None = None,
):
    plt = _plt()
    np = _np()
    ppm = np.asarray(analysis.spectrum_grid_ppm, dtype=float)
    grid = np.asarray(analysis.spectrum_grid_intensity, dtype=float)
    time, _, _, _ = _arrays(analysis)
    fig, ax = plt.subplots(figsize=size)
    cmap = plt.get_cmap("viridis")
    global_span = max(float(np.nanpercentile(grid, 99) - np.nanpercentile(grid, 1)), 1e-12)
    offset = 0.24 * global_span if stacked else 0.0
    for index, trace in enumerate(grid):
        color = cmap(index / max(1, len(grid) - 1))
        label = f"{time[index]:.2f} h"
        ax.plot(ppm, trace + index * offset, color=color, linewidth=1.1,
                alpha=0.92, label=label)
    lo, hi = analysis.config.integration_window_ppm
    ax.axvspan(lo, hi, color=ORANGE, alpha=0.08, label="Integration window")
    ax.axvline(analysis.config.expected_center_ppm, color=ORANGE, linestyle=":", linewidth=1.1)
    ax.set_xlim(analysis.config.plot_window_ppm[1], analysis.config.plot_window_ppm[0])
    title = descriptive_title or (
        "Focused Spectral Evolution" if stacked else "Focused Spectral Overlay"
    )
    ax.set_title(_title(analysis, title))
    ax.set_xlabel("Chemical shift (ppm)")
    ax.set_ylabel("Intensity + offset" if stacked else "Intensity")
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=9 if slide else 6, ncol=2, loc="upper left")
    fig.tight_layout()
    return fig


def _time_edges(values):
    np = _np()
    values = np.asarray(values, dtype=float)
    if values.size == 1:
        return np.asarray([values[0] - 0.5, values[0] + 0.5])
    middle = 0.5 * (values[:-1] + values[1:])
    return np.concatenate(([values[0] - (middle[0] - values[0])], middle,
                           [values[-1] + (values[-1] - middle[-1])]))


def _heatmap_figure(analysis, size, slide=False):
    plt = _plt()
    np = _np()
    ppm = np.asarray(analysis.spectrum_grid_ppm, dtype=float)
    grid = np.asarray(analysis.spectrum_grid_intensity, dtype=float)
    time, _, _, _ = _arrays(analysis)
    ppm_edges = _time_edges(ppm)
    time_edges = _time_edges(time)
    fig, ax = plt.subplots(figsize=size)
    mesh = ax.pcolormesh(ppm_edges, time_edges, grid, shading="flat", cmap="magma")
    centers = np.asarray([r["peak_center_ppm"] for r in analysis.measurements], dtype=float)
    valid = np.isfinite(centers) & np.asarray(
        [bool(row["peak_quality_pass"]) for row in analysis.measurements], dtype=bool
    )
    ax.plot(centers[valid], time[valid], color="white", linewidth=1.0,
            marker="o", markersize=3, label="Tracked center")
    ax.axvline(analysis.config.expected_center_ppm, color="cyan", linestyle=":", linewidth=1.0)
    ax.set_xlim(analysis.config.plot_window_ppm[1], analysis.config.plot_window_ppm[0])
    ax.set_title(_title(analysis, "Time–ppm Intensity Heatmap"))
    ax.set_xlabel("Chemical shift (ppm)")
    ax.set_ylabel("Elapsed time (hours)")
    ax.grid(False)
    ax.tick_params(labelsize=13 if slide else 8)
    fig.colorbar(mesh, ax=ax, label="Intensity")
    ax.legend(frameon=False, fontsize=10 if slide else 7)
    fig.tight_layout()
    return fig


def _dashboard_figure(analysis, size, slide=False):
    plt = _plt()
    np = _np()
    time, area, _, _ = _arrays(analysis)
    rows = analysis.measurements
    rate = np.asarray([r["rolling_slope_per_hour"] for r in analysis.rates], dtype=float)
    metrics = [
        (area, "Area", BLUE),
        ([r["peak_height"] for r in rows], "Peak height", ORANGE),
        ([r["snr"] for r in rows], "SNR", GREEN),
        ([r["peak_center_ppm"] for r in rows], "Peak position (ppm)", RED),
        ([r["fwhm_hz"] for r in rows], "FWHM (Hz)", GRAY),
        ([r["prominence_snr"] for r in rows], "Prominence SNR", BLUE),
        (rate, "Local slope", ORANGE),
    ]
    fig, axes = plt.subplots(4, 2, figsize=size, sharex=True)
    for ax, (values, label, color) in zip(axes.flat, metrics):
        ax.plot(time, values, "o-", color=color, linewidth=1.1, markersize=3)
        ax.set_ylabel(label)
        _style(ax, slide=slide)
    status_ax = axes.flat[-1]
    status_ax.axis("off")
    result = analysis.completion
    status_ax.text(0.02, 0.78, "Completion status", fontsize=14 if slide else 9,
                   fontweight="semibold")
    status_ax.text(0.02, 0.55, result.status.replace("_", " "),
                   fontsize=20 if slide else 14,
                   color=GREEN if result.complete else RED)
    status_ax.text(0.02, 0.32, f"Evidence: {result.evidence_level}", fontsize=12 if slide else 8)
    fig.suptitle(
        _title(analysis, "Target Peak Quality Control"),
        fontsize=17 if slide else 11,
    )
    axes[-1, 0].set_xlabel("Elapsed time (hours)")
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    return fig


def _completion_figure(analysis, size, slide=False):
    plt = _plt()
    np = _np()
    time, area, _, _ = _arrays(analysis)
    rates = analysis.rates
    slope = np.asarray([r["rolling_slope_per_hour"] for r in rates], dtype=float)
    threshold = analysis.config.completion.absolute_slope_threshold_per_hour
    fig, axes = plt.subplots(2, 1, figsize=size, sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    axes[0].plot(time, area, "o-", color=BLUE, linewidth=1.4, markersize=5,
                 label="Measured area")
    _completion_marker(axes[0], analysis, area, slide=slide)
    axes[0].set_ylabel("Area")
    axes[0].set_title("Area and completion marker")
    axes[0].legend(frameon=False, fontsize=11 if slide else 8)
    axes[1].axhspan(-threshold, threshold, color=GREEN, alpha=0.12,
                    label="Slope threshold")
    axes[1].axhline(0, color=GRAY, linewidth=0.8)
    axes[1].plot(time, slope, "o-", color=ORANGE, linewidth=1.4, markersize=4,
                 label="Recent fitted slope")
    _completion_marker(axes[1], analysis)
    axes[1].set_ylabel("Area/hour")
    axes[1].set_xlabel("Elapsed time (hours)")
    axes[1].legend(frameon=False, fontsize=10 if slide else 7)
    for ax in axes:
        _style(ax, slide=slide)
    result = analysis.completion
    text = (
        f"{result.status.replace('_', ' ')} at {result.completion_elapsed_hours:.2f} h"
        if result.complete and result.completion_elapsed_hours is not None
        else f"{result.status.replace('_', ' ')} — no completion detected"
    )
    axes[0].text(0.99, 0.05, text, transform=axes[0].transAxes, ha="right",
                 fontsize=12 if slide else 8, color=GREEN if result.complete else RED)
    fig.suptitle(
        _title(analysis, "Completion Detection"),
        fontsize=17 if slide else 11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return fig


def _stage_figure(analysis, size, slide=False):
    """Area timeline with only explicitly configured stage boundaries."""

    plt = _plt()
    time, area, _, _ = _arrays(analysis)
    fig, ax = plt.subplots(figsize=size)
    ax.plot(time, area, "o-", color=BLUE, linewidth=1.4, markersize=5,
            label=analysis.config.peak_label)
    colors = ("#dbeafe", "#fef3c7")
    for index, stage in enumerate(analysis.config.stages):
        ax.axvspan(stage.start_hours, stage.end_hours, color=colors[index % 2],
                   alpha=0.55)
        ax.text(0.5 * (stage.start_hours + stage.end_hours), 0.96,
                f"{stage.label}\n{stage.expected_direction}",
                transform=ax.get_xaxis_transform(), ha="center", va="top",
                fontsize=11 if slide else 7)
    _completion_marker(ax, analysis, area, slide=slide)
    ax.set_title(_title(analysis, "Stage/Cycle Overview"))
    ax.set_xlabel("Elapsed time (hours)")
    ax.set_ylabel("Area")
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=11 if slide else 8)
    fig.tight_layout()
    return fig


def _main_figure(analysis, size, slide=False):
    """Four-panel paper figure: spectra, area, rate, decision trace."""

    plt = _plt()
    np = _np()
    ppm = np.asarray(analysis.spectrum_grid_ppm, dtype=float)
    grid = np.asarray(analysis.spectrum_grid_intensity, dtype=float)
    time, area, low, high = _arrays(analysis)
    fig, axes = plt.subplots(2, 2, figsize=size)
    cmap = plt.get_cmap("viridis")
    for index, trace in enumerate(grid):
        axes[0, 0].plot(ppm, trace, color=cmap(index / max(1, len(grid) - 1)),
                        linewidth=0.8, alpha=0.8)
    axes[0, 0].axvspan(*analysis.config.integration_window_ppm, color=ORANGE, alpha=0.08)
    axes[0, 0].set_xlim(analysis.config.plot_window_ppm[1], analysis.config.plot_window_ppm[0])
    axes[0, 0].set(title="a  Focused spectra", xlabel="Chemical shift (ppm)", ylabel="Intensity")
    valid = np.isfinite(low) & np.isfinite(high)
    axes[0, 1].fill_between(time[valid], low[valid], high[valid], color=BLUE, alpha=0.13)
    axes[0, 1].plot(time, area, "o-", color=BLUE, linewidth=1.2, markersize=4)
    _completion_marker(axes[0, 1], analysis, area)
    axes[0, 1].set(title="b  Area", xlabel="Elapsed time (hours)", ylabel="Area")
    slope = np.asarray([r["rolling_slope_per_hour"] for r in analysis.rates], dtype=float)
    threshold = analysis.config.completion.absolute_slope_threshold_per_hour
    axes[1, 0].axhspan(-threshold, threshold, color=GREEN, alpha=0.10)
    axes[1, 0].axhline(0, color=GRAY, linewidth=0.7)
    axes[1, 0].plot(time, slope, "o-", color=ORANGE, linewidth=1.2, markersize=4)
    _completion_marker(axes[1, 0], analysis)
    axes[1, 0].set(title="c  Local area rate", xlabel="Elapsed time (hours)", ylabel="Area/hour")
    statuses = [row["status"].replace("_", " ") for row in analysis.decision_trace]
    codes = {name: index for index, name in enumerate(dict.fromkeys(statuses))}
    axes[1, 1].step(time, [codes[s] for s in statuses], where="post", color=GREEN, linewidth=1.4)
    axes[1, 1].scatter(time, [codes[s] for s in statuses], color=GREEN, s=18)
    axes[1, 1].set_yticks(list(codes.values()), list(codes.keys()))
    axes[1, 1].set(title="d  Decision state", xlabel="Elapsed time (hours)", ylabel="Status")
    for ax in axes.flat:
        _style(ax, slide=slide)
    fig.suptitle(
        _title(analysis, "Target Peak Analysis"),
        fontsize=17 if slide else 11,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    return fig


def _valid_timing_rows(analysis) -> list[dict]:
    return [
        row
        for row in analysis.timing_comparison
        if row.get("comparison_qc_pass")
    ]


def _minute_difference_label(value: float) -> str:
    """Format a concise signed actual-minus-nominal timing label."""

    return "0.0 min" if abs(value) < 0.05 else f"{value:+.1f} min"


def _timing_elapsed_figure(analysis, size, slide=False, *, label_offsets=False):
    """Nominal and metadata-derived elapsed time on the same acquisition axis."""

    plt = _plt()
    np = _np()
    rows = _valid_timing_rows(analysis)
    x = np.asarray([row["acquisition_index"] for row in rows], dtype=float)
    nominal = 60.0 * np.asarray(
        [row["nominal_elapsed_hours"] for row in rows], dtype=float
    )
    actual = 60.0 * np.asarray(
        [row["actual_elapsed_hours"] for row in rows], dtype=float
    )
    fig, ax = plt.subplots(figsize=size)
    connector_color = ORANGE if label_offsets else "#98a2b3"
    ax.vlines(x, np.minimum(nominal, actual), np.maximum(nominal, actual),
              color=connector_color,
              linewidth=2.2 if label_offsets and slide else 1.5,
              alpha=0.85 if label_offsets else 0.65,
              zorder=1)
    ax.plot(x, nominal, "o--", color=GRAY, linewidth=1.5, markersize=5,
            label="Filename nominal")
    ax.plot(x, actual, "o-", color=BLUE, linewidth=1.7, markersize=6,
            label="Metadata actual")
    if label_offsets:
        for x_value, nominal_value, actual_value in zip(
            x, nominal, actual, strict=True
        ):
            difference = actual_value - nominal_value
            ax.annotate(
                _minute_difference_label(difference),
                (x_value, 0.5 * (nominal_value + actual_value)),
                xytext=(6, 0),
                textcoords="offset points",
                ha="left",
                va="center",
                color="#7a4b00",
                fontsize=10 if slide else 7,
                bbox={
                    "boxstyle": "round,pad=0.15",
                    "facecolor": "white",
                    "edgecolor": "none",
                    "alpha": 0.82,
                },
                zorder=5,
            )
    ax.set_title(_title(analysis, "Expected vs Actual Elapsed Time"))
    ax.set_xlabel("Filename time")
    ax.set_ylabel("Elapsed time (minutes)")
    ax.set_xticks(x, [f"{row['filename_sequence'][:2]}:{row['filename_sequence'][2:]}"
                      for row in rows])
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=12 if slide else 8, loc="upper left")
    fig.tight_layout()
    return fig


def _timing_elapsed_with_offsets_figure(analysis, size, slide=False):
    """Combine elapsed-time trends with labeled actual-minus-nominal gaps."""

    return _timing_elapsed_figure(
        analysis,
        size,
        slide=slide,
        label_offsets=True,
    )


def _timing_elapsed_absolute_offset_figure(analysis, size, slide=False):
    """Plot elapsed trends with shading and absolute clock-offset labels."""

    plt = _plt()
    np = _np()
    rows = _valid_timing_rows(analysis)
    x = np.asarray([row["acquisition_index"] for row in rows], dtype=float)
    nominal = 60.0 * np.asarray(
        [row["nominal_elapsed_hours"] for row in rows], dtype=float
    )
    actual = 60.0 * np.asarray(
        [row["actual_elapsed_hours"] for row in rows], dtype=float
    )
    absolute_offset = np.asarray(
        [row["clock_time_offset_minutes"] for row in rows], dtype=float
    )
    fig, ax = plt.subplots(figsize=size)
    ax.fill_between(
        x,
        nominal,
        actual,
        color=RED,
        alpha=0.12,
        linewidth=0,
        zorder=1,
    )
    ax.plot(
        x,
        nominal,
        "o--",
        color=GRAY,
        linewidth=1.5,
        markersize=5,
        label="Filename nominal",
        zorder=2,
    )
    ax.plot(
        x,
        actual,
        "o-",
        color=RED,
        linewidth=2.2 if slide else 1.8,
        markersize=7 if slide else 5.5,
        markeredgecolor="white",
        markeredgewidth=0.8,
        label="Metadata actual",
        zorder=3,
    )
    for index, (x_value, actual_value, offset_value) in enumerate(
        zip(x, actual, absolute_offset, strict=True)
    ):
        first_point = index == 0
        ax.annotate(
            _minute_difference_label(offset_value),
            (x_value, actual_value),
            xytext=(0, 10 if first_point else -13),
            textcoords="offset points",
            ha="center",
            va="bottom" if first_point else "top",
            color=RED,
            fontsize=10 if slide else 7,
            fontweight="semibold",
            bbox={
                "boxstyle": "round,pad=0.12",
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.78,
            },
            zorder=4,
        )
    ax.set_title(
        _title(analysis, "Expected vs Actual Elapsed Time with Absolute Offset")
    )
    ax.set_xlabel("Filename time")
    ax.set_ylabel("Elapsed time (minutes)")
    ax.set_xticks(
        x,
        [
            f"{row['filename_sequence'][:2]}:{row['filename_sequence'][2:]}"
            for row in rows
        ],
    )
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=12 if slide else 8, loc="upper left")
    fig.tight_layout()
    return fig


def _timing_offset_figure(analysis, size, slide=False):
    """Elapsed timing error relative to the nominal filename schedule."""

    plt = _plt()
    np = _np()
    rows = _valid_timing_rows(analysis)
    x = np.asarray([row["acquisition_index"] for row in rows], dtype=float)
    offset = np.asarray(
        [row["elapsed_timing_offset_minutes"] for row in rows], dtype=float
    )
    fig, ax = plt.subplots(figsize=size)
    ax.axhline(0.0, color=GRAY, linewidth=1.0)
    ax.vlines(x, 0.0, offset, color=ORANGE, linewidth=2.2, alpha=0.85)
    ax.plot(x, offset, color=ORANGE, linewidth=1.0, alpha=0.55)
    ax.scatter(x, offset, s=55 if slide else 32, color=ORANGE,
               edgecolor="white", linewidth=0.7, zorder=4)
    ax.set_title(_title(analysis, "Timing Offset by Acquisition"))
    ax.set_xlabel("Filename time")
    ax.set_ylabel("Actual - nominal (minutes)")
    ax.set_xticks(x, [f"{row['filename_sequence'][:2]}:{row['filename_sequence'][2:]}"
                      for row in rows])
    _style(ax, slide=slide)
    fig.tight_layout()
    return fig


def _absolute_timing_offset_figure(analysis, size, slide=False):
    """Plot the unnormalized metadata-minus-filename clock offset."""

    plt = _plt()
    np = _np()
    rows = _valid_timing_rows(analysis)
    x = np.asarray([row["acquisition_index"] for row in rows], dtype=float)
    offset = np.asarray(
        [row["clock_time_offset_minutes"] for row in rows], dtype=float
    )
    fig, ax = plt.subplots(figsize=size)
    ax.axhline(0.0, color=GRAY, linewidth=1.1, zorder=1)
    ax.vlines(x, 0.0, offset, color=RED, linewidth=2.5, alpha=0.82, zorder=2)
    ax.scatter(
        x,
        offset,
        s=72 if slide else 40,
        color=RED,
        marker="D",
        edgecolor="white",
        linewidth=0.9,
        zorder=3,
    )
    for x_value, offset_value in zip(x, offset, strict=True):
        ax.annotate(
            f"{offset_value:+.1f}",
            (x_value, offset_value),
            xytext=(0, 7 if offset_value >= 0 else -8),
            textcoords="offset points",
            ha="center",
            va="bottom" if offset_value >= 0 else "top",
            color=RED,
            fontsize=10 if slide else 7,
            fontweight="semibold",
        )
    ax.set_title(_title(analysis, "Absolute Timing Offset by Acquisition"))
    ax.set_xlabel("Filename time")
    ax.set_ylabel("Metadata - filename (minutes)")
    ax.set_xticks(
        x,
        [
            f"{row['filename_sequence'][:2]}:{row['filename_sequence'][2:]}"
            for row in rows
        ],
    )
    ax.margins(y=0.16)
    _style(ax, slide=slide)
    fig.tight_layout()
    return fig


def _timing_dumbbell_figure(analysis, size, slide=False):
    """Pair each nominal filename clock time with its metadata timestamp."""

    from datetime import datetime
    import matplotlib.dates as mdates

    plt = _plt()
    np = _np()
    rows = _valid_timing_rows(analysis)
    y = np.arange(1, len(rows) + 1)
    nominal = np.asarray(
        [mdates.date2num(datetime.fromisoformat(row["nominal_timestamp"])) for row in rows]
    )
    actual = np.asarray(
        [mdates.date2num(datetime.fromisoformat(row["metadata_timestamp"])) for row in rows]
    )
    fig, ax = plt.subplots(figsize=size)
    ax.hlines(y, np.minimum(nominal, actual), np.maximum(nominal, actual),
              color="#b7c0cc", linewidth=3.0 if slide else 2.0, alpha=0.9)
    ax.scatter(nominal, y, s=58 if slide else 34, color=GRAY, marker="o",
               edgecolor="white", linewidth=0.7, label="Filename nominal", zorder=3)
    ax.scatter(actual, y, s=92 if slide else 52, color=RED, marker="D",
               edgecolor="white", linewidth=1.0, label="Metadata actual", zorder=4)
    for y_value, nominal_value, actual_value, row in zip(
        y, nominal, actual, rows, strict=True
    ):
        ax.annotate(
            _minute_difference_label(float(row["clock_time_offset_minutes"])),
            (0.5 * (nominal_value + actual_value), y_value),
            xytext=(0, -11 if slide else -7),
            textcoords="offset points",
            ha="center",
            va="bottom",
            color="#475467",
            fontsize=9 if slide else 6.5,
            zorder=5,
        )
    ax.set_title(_title(analysis, "Filename Time vs Metadata Time"))
    ax.set_xlabel("Clock time")
    ax.set_ylabel("Acquisition")
    ax.set_yticks(y, [str(row["acquisition_index"]) for row in rows])
    ax.invert_yaxis()
    ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=(0, 30)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    _style(ax, slide=slide)
    ax.yaxis.grid(False)
    ax.legend(frameon=False, fontsize=12 if slide else 8, loc="upper right")
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    return fig


def _timing_clock_test_figure(analysis, size, slide=False):
    """Compare each elapsed-time series against its own clock timestamps."""

    from datetime import datetime
    import matplotlib.dates as mdates

    plt = _plt()
    np = _np()
    rows = _valid_timing_rows(analysis)
    nominal_clock = [
        datetime.fromisoformat(row["nominal_timestamp"]) for row in rows
    ]
    metadata_clock = [
        datetime.fromisoformat(row["metadata_timestamp"]) for row in rows
    ]
    nominal_elapsed = 60.0 * np.asarray(
        [row["nominal_elapsed_hours"] for row in rows], dtype=float
    )
    metadata_elapsed = 60.0 * np.asarray(
        [row["actual_elapsed_hours"] for row in rows], dtype=float
    )
    fig, ax = plt.subplots(figsize=size)
    ax.plot(
        nominal_clock,
        nominal_elapsed,
        "o-",
        color=BLUE,
        linewidth=1.8,
        markersize=6,
        label="Filename nominal",
    )
    ax.plot(
        metadata_clock,
        metadata_elapsed,
        "o-",
        color=RED,
        linewidth=1.8,
        markersize=6,
        label="Metadata actual",
    )
    ax.set_title(_title(analysis, "Filename vs Metadata Timing Test"))
    ax.set_xlabel("Clock time")
    ax.set_ylabel("Elapsed time (minutes)")
    ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=(0, 30)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.margins(x=0.03)
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=12 if slide else 8, loc="upper left")
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    return fig


def _timing_clock_v2_figure(analysis, size, slide=False):
    """Extend the approved clock-time comparison with shading and labels."""

    from datetime import datetime
    import matplotlib.dates as mdates

    plt = _plt()
    np = _np()
    rows = _valid_timing_rows(analysis)
    nominal_clock = [
        datetime.fromisoformat(row["nominal_timestamp"]) for row in rows
    ]
    metadata_clock = [
        datetime.fromisoformat(row["metadata_timestamp"]) for row in rows
    ]
    nominal_elapsed = 60.0 * np.asarray(
        [row["nominal_elapsed_hours"] for row in rows], dtype=float
    )
    metadata_elapsed = 60.0 * np.asarray(
        [row["actual_elapsed_hours"] for row in rows], dtype=float
    )
    absolute_offset = np.asarray(
        [row["clock_time_offset_minutes"] for row in rows], dtype=float
    )
    fig, ax = plt.subplots(figsize=size)
    ax.fill(
        nominal_clock + metadata_clock[::-1],
        np.concatenate((nominal_elapsed, metadata_elapsed[::-1])),
        color=RED,
        alpha=0.12,
        linewidth=0,
        zorder=1,
    )
    ax.plot(
        nominal_clock,
        nominal_elapsed,
        "o-",
        color=BLUE,
        linewidth=1.8,
        markersize=6,
        label="Filename nominal",
        zorder=2,
    )
    ax.plot(
        metadata_clock,
        metadata_elapsed,
        "o-",
        color=RED,
        linewidth=1.8,
        markersize=6,
        label="Metadata actual",
        zorder=3,
    )
    for index, (clock, elapsed, offset) in enumerate(
        zip(metadata_clock, metadata_elapsed, absolute_offset, strict=True)
    ):
        first_point = index == 0
        ax.annotate(
            _minute_difference_label(offset),
            (clock, elapsed),
            xytext=(7, 8 if first_point else -11),
            textcoords="offset points",
            ha="left",
            va="bottom" if first_point else "top",
            color=RED,
            fontsize=10 if slide else 7,
            fontweight="semibold",
            bbox={
                "boxstyle": "round,pad=0.12",
                "facecolor": "white",
                "edgecolor": "none",
                "alpha": 0.78,
            },
            zorder=4,
        )
    ax.set_title(_title(analysis, "Filename vs Metadata Clock Time V2"))
    ax.set_xlabel("Clock time")
    ax.set_ylabel("Elapsed time (minutes)")
    ax.xaxis.set_major_locator(mdates.MinuteLocator(byminute=(0, 30)))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax.margins(x=0.03)
    _style(ax, slide=slide)
    ax.legend(frameon=False, fontsize=12 if slide else 8, loc="upper left")
    fig.autofmt_xdate(rotation=0, ha="center")
    fig.tight_layout()
    return fig


def render_target_peak_figures(analysis, plots_dir: Path) -> tuple[list[str], list[dict]]:
    """Render coherent slide and paper groups and return a provenance manifest."""

    plt = _plt()
    base = Path(plots_dir) / "target_peak"
    cfg = analysis.config.figures
    written: list[str] = []
    manifest: list[dict] = []

    specifications: list[tuple[str, str, Callable, tuple[float, float], bool, str, str]] = [
        ("slides", "01_focused_spectra", lambda s, sl: _spectra_figure(analysis, s, sl, stacked=True, descriptive_title="Focused Spectral Evolution"), cfg.slide_size_inches, True, "target_peak_spectra_long.csv", "Focused Spectral Evolution"),
        ("slides", "02_area_vs_time", lambda s, sl: _area_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_measurements.csv", "Area vs Time"),
        ("slides", "03_rate_vs_time", lambda s, sl: _rate_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_rates.csv", "Area Change Rate"),
        ("slides", "04_completion_decision", lambda s, sl: _completion_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_measurements.csv;target_peak_rates.csv;target_peak_completion_trace.csv", "Completion Detection"),
        ("paper", "main_figure", lambda s, sl: _main_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_spectra_long.csv;target_peak_measurements.csv;target_peak_rates.csv;target_peak_completion_trace.csv", "Target Peak Analysis"),
        ("paper", "supp_area_absolute", lambda s, sl: _area_figure(analysis, s, sl), cfg.paper_single_column_inches, False, "target_peak_measurements.csv", "Area vs Time"),
        ("paper", "supp_area_normalized", lambda s, sl: _normalized_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_normalized.csv", "Normalized Area vs Time"),
        ("paper", "supp_interval_changes", lambda s, sl: _change_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_rates.csv", "Area Change Between Acquisitions"),
        ("paper", "supp_percent_change", lambda s, sl: _percent_figure(analysis, s, sl), cfg.paper_single_column_inches, False, "target_peak_rates.csv", "Percent Change per Interval"),
        ("paper", "supp_spectra_overlay", lambda s, sl: _spectra_figure(analysis, s, sl, stacked=False, descriptive_title="Focused Spectral Overlay"), cfg.paper_double_column_inches, False, "target_peak_spectra_long.csv", "Focused Spectral Overlay"),
        ("paper", "supp_spectra_waterfall", lambda s, sl: _spectra_figure(analysis, s, sl, stacked=True, descriptive_title="Spectral Waterfall"), cfg.paper_double_column_inches, False, "target_peak_spectra_long.csv", "Spectral Waterfall"),
        ("paper", "supp_time_ppm_heatmap", lambda s, sl: _heatmap_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_spectra_long.csv;target_peak_measurements.csv", "Time–ppm Intensity Heatmap"),
        ("qc", "peak_metrics_dashboard", lambda s, sl: _dashboard_figure(analysis, s, sl), (8.0, 8.5), False, "target_peak_measurements.csv;target_peak_rates.csv;target_peak_completion.csv", "Target Peak Quality Control"),
        ("qc", "completion_decision", lambda s, sl: _completion_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_measurements.csv;target_peak_rates.csv;target_peak_completion_trace.csv", "Completion Detection"),
    ]
    if analysis.config.stages:
        specifications.extend(
            [
                ("slides", "05_stage_overview", lambda s, sl: _stage_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_measurements.csv;target_peak_stages.csv", "Stage/Cycle Overview"),
                ("paper", "supp_stage_overview", lambda s, sl: _stage_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_measurements.csv;target_peak_stages.csv", "Stage/Cycle Overview"),
            ]
        )
    if _valid_timing_rows(analysis):
        specifications.extend(
            [
                ("slides/archive", "06_expected_vs_actual_time", lambda s, sl: _timing_elapsed_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_timing_comparison.csv", "Expected vs Actual Elapsed Time"),
                ("slides", "07_timing_offset", lambda s, sl: _timing_offset_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_timing_comparison.csv", "Timing Offset by Acquisition"),
                ("slides", "08_filename_vs_metadata_time", lambda s, sl: _timing_dumbbell_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_timing_comparison.csv", "Filename Time vs Metadata Time"),
                ("slides/archive", "09_elapsed_time_with_offsets", lambda s, sl: _timing_elapsed_with_offsets_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_timing_comparison.csv", "Expected vs Actual Elapsed Time"),
                ("slides", "10_absolute_timing_offset_by_acquisition", lambda s, sl: _absolute_timing_offset_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_timing_comparison.csv", "Absolute Timing Offset by Acquisition"),
                ("slides/archive", "11_expected_vs_actual_elapsed_time_absolute_offset", lambda s, sl: _timing_elapsed_absolute_offset_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_timing_comparison.csv", "Expected vs Actual Elapsed Time with Absolute Offset"),
                ("slides/archive", "12_filename_vs_metadata_clock_time_v1", lambda s, sl: _timing_clock_test_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_timing_comparison.csv", "Filename vs Metadata Timing Test"),
                ("slides", "13_filename_vs_metadata_clock_time", lambda s, sl: _timing_clock_v2_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_timing_comparison.csv", "Filename vs Metadata Clock Time V2"),
                ("paper", "supp_timing_expected_vs_actual", lambda s, sl: _timing_elapsed_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_timing_comparison.csv", "Expected vs Actual Elapsed Time"),
                ("paper", "supp_timing_offset", lambda s, sl: _timing_offset_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_timing_comparison.csv", "Timing Offset by Acquisition"),
                ("paper", "supp_timing_filename_vs_metadata", lambda s, sl: _timing_dumbbell_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_timing_comparison.csv", "Filename Time vs Metadata Time"),
                ("paper", "supp_timing_elapsed_with_offsets", lambda s, sl: _timing_elapsed_with_offsets_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_timing_comparison.csv", "Expected vs Actual Elapsed Time"),
                ("paper", "supp_absolute_timing_offset_by_acquisition", lambda s, sl: _absolute_timing_offset_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_timing_comparison.csv", "Absolute Timing Offset by Acquisition"),
                ("paper", "supp_expected_vs_actual_elapsed_time_absolute_offset", lambda s, sl: _timing_elapsed_absolute_offset_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_timing_comparison.csv", "Expected vs Actual Elapsed Time with Absolute Offset"),
                ("paper", "supp_filename_vs_metadata_clock_time_v1", lambda s, sl: _timing_clock_test_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_timing_comparison.csv", "Filename vs Metadata Timing Test"),
                ("paper", "supp_filename_vs_metadata_clock_time", lambda s, sl: _timing_clock_v2_figure(analysis, s, sl), cfg.paper_double_column_inches, False, "target_peak_timing_comparison.csv", "Filename vs Metadata Clock Time V2"),
                ("test", "timing_test_filename_vs_metadata_clock_time", lambda s, sl: _timing_clock_test_figure(analysis, s, sl), cfg.slide_size_inches, True, "target_peak_timing_comparison.csv", "Filename vs Metadata Timing Test"),
            ]
        )

    for group, stem, builder, size, slide, data_files, descriptive_title in specifications:
        fig = builder(size, slide)
        for extension in cfg.formats:
            path = base / group / f"{stem}.{extension}"
            path.parent.mkdir(parents=True, exist_ok=True)
            fig.savefig(path, dpi=cfg.dpi if extension == "png" else None,
                        bbox_inches="tight", facecolor="white")
            written.append(str(path))
            manifest.append(
                {
                    "figure": str(path),
                    "group": group,
                    "data_files": data_files,
                    "dataset": _dataset_name(analysis),
                    "visible_title": _title(analysis, descriptive_title),
                    "peak_label": analysis.config.peak_label,
                    "integration_window_ppm": str(analysis.config.integration_window_ppm),
                }
            )
        plt.close(fig)
    return written, manifest
