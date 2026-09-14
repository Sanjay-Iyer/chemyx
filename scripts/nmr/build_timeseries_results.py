"""Collect a multi-run automated series into one folder of time-series results.

Each run folder under the series directory holds a single acquisition
(``raw_nmr/*.dx``) plus the pipeline's own analysis of it
(``processed_nmr/**/..._peaks_simple.csv``).  This script gathers all of them
into one place and produces:

* ``peak_area_timeseries.csv`` -- master table: one row per timepoint, with the
  pipeline's target-peak area plus a fixed-window integral computed here
* ``region_plots/`` -- per timepoint, a zoomed plot of the peak region
  (``*_region.png``) and one of the leftmost multiplet line with its
  valley-to-valley integral shaded (``*_leftpeak.png``, the trace behind
  ``peak_area_bar_leftpeak.png``), all on a shared intensity scale so they can
  be flipped through as a series
* ``overlay_region_timeseries.png`` -- every timepoint's peak region overlaid,
  colored light-to-dark with time
* ``peak_area_bar.png`` -- target-peak area per timepoint, x = day and time

Areas in the master CSV come from each run's own ``_peaks_simple.csv`` so the
numbers match what the pipeline already reported.  The spectra are re-derived
here (same parameters the runs recorded: stored phase, 0.03 Hz line
broadening, 65536-point zero fill, asymmetric-least-squares baseline) because
the runs do not export their processed traces.

Usage::

    # Defaults: the 081026 demo series
    python scripts/nmr/build_timeseries_results.py

    # Another series, another peak
    python scripts/nmr/build_timeseries_results.py path/to/series \
        --target-ppm 6.10 --window 6.00 6.20 --region 5.9 6.3
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.patches import PathPatch, Patch
from matplotlib.path import Path as MplPath

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chemyx_lab.analysis.nmr import (  # noqa: E402
    asymmetric_least_squares_baseline,
    build_phased_spectrum,
    integrate_above_local_baseline,
    pick_spectrum_region,
)
from chemyx_lab.analysis.plot_titles import (  # noqa: E402
    format_dataset_plot_title,
    resolve_dataset_display_name,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERIES = (
    REPO_ROOT / "results" / "runs" / "automated" / "chemyx_demo_081026_v3"
)
DEFAULT_OUT_NAME = "timeseries_results"
STEPS_FILE = "steps.csv"

# Processing parameters, as recorded in each run's summary.json.
LINE_BROADENING_HZ = 0.03
ZERO_FILL_POINTS = 65536
REGION_MIN_PPM = 5.0
REGION_MAX_PPM = 6.5
MIN_PROMINENCE_SNR = 5.0
MIN_DISTANCE_PPM = 0.04
MIN_WIDTH_PPM = 0.015
BASELINE_ORDER = 3
SMOOTHING_WINDOW_PPM = 0.006

SURFACE = "#fcfcfb"
SERIES = "#2a78d6"
ACCENT = "#eb6834"
INK_PRIMARY = "#0b0b0b"
INK_SECONDARY = "#52514e"
INK_MUTED = "#898781"
GRIDLINE = "#e1e0d9"
BASELINE_INK = "#c3c2b7"

# Blue ramp, light -> dark. Ordinal use starts at step 250 so the lightest
# trace still clears 2:1 against the surface.
BLUE_RAMP = [
    "#86b6ef", "#6da7ec", "#5598e7", "#3987e5", "#2a78d6",
    "#256abf", "#1c5cab", "#184f95", "#104281", "#0d366b",
]

FONT_STACK = ["Segoe UI", "DejaVu Sans", "sans-serif"]

DPI = 200
MAX_BAR_PX = 24.0
CORNER_PX = 4.0
PX = DPI / 96.0

CSV_COLUMNS = [
    "index", "run", "dx_file", "timestamp", "day_time", "elapsed_hours",
    "peak_ppm", "integrated_area", "intensity", "snr", "prominence_snr",
    "width_hz", "detected", "window_left_ppm", "window_right_ppm",
    "window_positive_area", "region_noise",
    "left_peak_ppm", "left_peak_height", "left_peak_area",
    "left_peak_from_ppm", "left_peak_to_ppm", "multiplet_lines",
    "step_label", "acquired_start", "starting_material_area", "silane_area",
]

# The leftmost line may sit on a shoulder of its neighbour rather than in a
# clean valley, so the walk that finds its feet is capped.
LEFT_PEAK_MAX_HALF_WIDTH_PPM = 0.05

# The left-peak plots zoom to the multiplet: the fixed window plus enough
# margin to show that the trace really has returned to baseline outside it.
LEFT_PEAK_ZOOM_PAD_PPM = 0.03

# Separating the multiplet into its individual lines needs a finer pick than
# the pipeline's: at 0.04 ppm minimum separation the lines (~0.02 ppm apart)
# merge into one peak. The noise estimate still comes from the wide region, or
# the multiplet inflates it and suppresses its own lines.
LINE_MIN_DISTANCE_PPM = 0.008
LINE_MIN_WIDTH_PPM = 0.004
LINE_MIN_PROMINENCE_SNR = 3.0
LINE_SMOOTHING_WINDOW_PPM = 0.004


# --------------------------------------------------------------------------
# gathering
# --------------------------------------------------------------------------

def find_runs(series_dir: Path) -> list[Path]:
    """Run folders are the ones carrying a raw acquisition."""
    runs = [
        child for child in sorted(series_dir.iterdir())
        if child.is_dir() and any((child / "raw_nmr").glob("*.dx"))
    ]
    if not runs:
        raise SystemExit(f"no run folders with raw_nmr/*.dx under {series_dir}")
    return runs


def read_pipeline_peak(run: Path) -> dict[str, str] | None:
    """The run's own target-peak row, if it analysed the acquisition.

    A run can hold more than one processed folder once an acquisition has been
    re-analysed, so the newest wins rather than whichever sorts first.
    """
    candidates = sorted(
        run.rglob("*_peaks_simple.csv"),
        key=lambda path: path.stat().st_mtime, reverse=True,
    )
    for path in candidates:
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        if rows:
            # One acquisition per run, so the first row is the target peak.
            return rows[0]
    return None


def read_steps(path: Path) -> dict[str, dict[str, str]]:
    """Optional experiment log: dx_file -> {label, event}.

    The acquisition times alone do not say which pull a spectrum belongs to or
    when a reagent went in, and that is bench knowledge rather than anything
    recoverable from the data -- so it lives in an editable sidecar rather than
    being guessed here.
    """
    if not path.is_file():
        return {}
    with path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    steps = {}
    for row in rows:
        key = (row.get("dx_file") or "").strip()
        if key:
            steps[key] = {
                "label": (row.get("label") or "").strip(),
                "event": (row.get("event") or "").strip(),
            }
    return steps


def window_area(ppm: np.ndarray, real: np.ndarray,
                bounds: tuple[float, float]) -> float:
    """Positive area over a fixed ppm window, above the line joining its feet.

    Used for peaks that are tracked whether or not they are present: a
    detection-based measure would report a string of zeros and lose the decay.
    """
    integral = integrate_above_local_baseline(
        ppm, real, left_ppm=min(bounds), right_ppm=max(bounds)
    )
    return integral.positive_area


def process_spectrum(dx_path: Path) -> tuple[np.ndarray, np.ndarray, object]:
    """Phase, baseline-correct, and peak-pick one acquisition."""
    spectrum = build_phased_spectrum(
        dx_path,
        line_broadening_hz=LINE_BROADENING_HZ,
        zero_fill_points=ZERO_FILL_POINTS,
        phase_method="stored",
        inverse_phase=True,
        truncation_window="none",
    )
    real = spectrum.real - asymmetric_least_squares_baseline(spectrum.real)
    picked = pick_spectrum_region(
        spectrum.ppm_axis, real,
        region_min_ppm=REGION_MIN_PPM,
        region_max_ppm=REGION_MAX_PPM,
        min_prominence_snr=MIN_PROMINENCE_SNR,
        min_distance_ppm=MIN_DISTANCE_PPM,
        min_width_ppm=MIN_WIDTH_PPM,
        baseline_polynomial_order=BASELINE_ORDER,
        smoothing_window_ppm=SMOOTHING_WINDOW_PPM,
        quantitative_intensity=real,
        source=dx_path,
    )
    return np.asarray(spectrum.ppm_axis, dtype=float), real, picked


def target_peak(picked, target_ppm: float, tolerance_ppm: float):
    """The picked peak nearest the target, or None if nothing is close."""
    in_window = [
        peak for peak in picked.peaks
        if abs(peak.interpolated_ppm - target_ppm) <= tolerance_ppm
    ]
    if not in_window:
        return None
    return max(in_window, key=lambda peak: peak.positive_area)


def _walk_to_valley(values: np.ndarray, start: int, step: int,
                    limit: int, confirm: int = 3) -> int:
    """Walk downhill from ``start`` and stop at the first confirmed minimum.

    The multiplet's lines are not baseline resolved, so each line's foot is the
    valley between it and its neighbour rather than the baseline.  A rise only
    counts as the valley once ``confirm`` consecutive samples keep rising, so
    ripple on the flank does not end the walk early.
    """
    index = start
    while 0 <= index + step < values.size and abs(index - start) < limit:
        nxt = index + step
        if values[nxt] > values[index]:
            ahead = [nxt + k * step for k in range(confirm)]
            if not all(0 <= i < values.size for i in ahead):
                return index
            if bool(np.all(np.diff(values[ahead]) > 0)):
                return index
        index = nxt
    return index


def left_peak_integral(dx_path: Path, ppm: np.ndarray, real: np.ndarray,
                       window: tuple[float, float], *,
                       height_fraction: float, multiplet_span: float):
    """Integrate only the leftmost (highest-ppm) line of the multiplet.

    The lines are not baseline resolved, so this re-picks the region finely
    enough to separate them, then keeps only lines that belong to the
    multiplet: at least ``height_fraction`` of its tallest line and within
    ``multiplet_span`` ppm of it.  Without those two rules the highest-shift
    feature is often a noise stub sitting above the multiplet.  The chosen
    line is integrated between its adjacent valleys, which excludes the
    neighbours the pipeline's whole-multiplet bounds include.
    """
    fine = pick_spectrum_region(
        ppm, real,
        region_min_ppm=REGION_MIN_PPM,
        region_max_ppm=REGION_MAX_PPM,
        min_prominence_snr=LINE_MIN_PROMINENCE_SNR,
        min_distance_ppm=LINE_MIN_DISTANCE_PPM,
        min_width_ppm=LINE_MIN_WIDTH_PPM,
        baseline_polynomial_order=BASELINE_ORDER,
        smoothing_window_ppm=LINE_SMOOTHING_WINDOW_PPM,
        quantitative_intensity=real,
        source=dx_path,
    )
    lines = [
        line for line in fine.peaks
        if window[0] <= line.interpolated_ppm <= window[1]
    ]
    if not lines:
        return None

    strongest = max(lines, key=lambda line: line.peak_height)
    members = [
        line for line in lines
        if line.peak_height >= height_fraction * strongest.peak_height
        and abs(line.interpolated_ppm - strongest.interpolated_ppm)
        <= multiplet_span
    ]
    if not members:
        return None

    peak = max(members, key=lambda line: line.interpolated_ppm)
    region_ppm = np.asarray(fine.ppm_axis, dtype=float)
    smoothed = np.asarray(fine.smoothed, dtype=float)
    order = np.argsort(region_ppm)                 # ascending ppm
    region_ppm, smoothed = region_ppm[order], smoothed[order]

    # Snap to the maximum of the smoothed trace near the reported shift. The
    # picker locates the apex on the unsmoothed trace, and a sample or two of
    # disagreement puts the start of the walk on a rising flank, which ends it
    # immediately and leaves a bound sitting on the peak.
    near = np.flatnonzero(np.abs(region_ppm - peak.peak_ppm) <= 0.006)
    apex = (
        int(near[np.argmax(smoothed[near])]) if near.size
        else int(np.argmin(np.abs(region_ppm - peak.peak_ppm)))
    )
    spacing = float(np.median(np.diff(region_ppm)))
    limit = max(int(LEFT_PEAK_MAX_HALF_WIDTH_PPM / spacing), 3)
    # Confirm a valley over a linewidth-scale distance, not a fixed number of
    # samples: at 65536 points a few samples span ~0.001 ppm, so sample-counted
    # confirmation halts on ripple right beside the apex and the integration
    # chord then cuts across the peak itself.
    confirm = max(int(LINE_SMOOTHING_WINDOW_PPM / spacing), 3)

    high = _walk_to_valley(smoothed, apex, +1, limit, confirm)  # higher ppm
    low = _walk_to_valley(smoothed, apex, -1, limit, confirm)   # neighbour side
    left_ppm = float(region_ppm[low])
    right_ppm = float(region_ppm[high])

    integral = integrate_above_local_baseline(
        ppm, real, left_ppm=left_ppm, right_ppm=right_ppm
    )
    return {
        "ppm": peak.interpolated_ppm,
        "height": peak.peak_height,
        "area": integral.positive_area,
        "from_ppm": left_ppm,
        "to_ppm": right_ppm,
        "lines": len(members),
    }


# --------------------------------------------------------------------------
# drawing
# --------------------------------------------------------------------------

def _px_to_data(ax, px: float) -> tuple[float, float]:
    inv = ax.transData.inverted()
    x0, y0 = inv.transform((0.0, 0.0))
    x1, y1 = inv.transform((px, px))
    return abs(x1 - x0), abs(y1 - y0)


def _rounded_bar(ax, x, height, width, rx, ry, color) -> None:
    """Column with a rounded cap and square feet on the baseline."""
    if height <= 0:
        return
    left, right = x - width / 2.0, x + width / 2.0
    rx = min(rx, width / 2.0)
    ry = min(ry, height)
    verts = [
        (left, 0.0), (left, height - ry),
        (left, height), (left + rx, height),
        (right - rx, height),
        (right, height), (right, height - ry),
        (right, 0.0), (left, 0.0),
    ]
    codes = [
        MplPath.MOVETO, MplPath.LINETO,
        MplPath.CURVE3, MplPath.CURVE3,
        MplPath.LINETO,
        MplPath.CURVE3, MplPath.CURVE3,
        MplPath.LINETO, MplPath.CLOSEPOLY,
    ]
    ax.add_patch(PathPatch(MplPath(verts, codes), facecolor=color,
                           edgecolor="none", zorder=3))


def local_integration_fill(ppm: np.ndarray, real: np.ndarray,
                           left_ppm: float, right_ppm: float):
    """Return the local chord and positive fill used by peak integration."""
    lo, hi = sorted((float(left_ppm), float(right_ppm)))
    ppm_values = np.asarray(ppm, dtype=float)
    real_values = np.asarray(real, dtype=float)
    full_order = np.argsort(ppm_values)
    sorted_ppm = ppm_values[full_order]
    sorted_real = real_values[full_order]
    mask = (sorted_ppm >= lo) & (sorted_ppm <= hi)
    x = sorted_ppm[mask]
    y = sorted_real[mask]
    left_y = float(np.interp(lo, sorted_ppm, sorted_real))
    right_y = float(np.interp(hi, sorted_ppm, sorted_real))
    chord = left_y + (right_y - left_y) * (x - lo) / (hi - lo)
    return x, y, chord, np.maximum(y - chord, 0.0)


def _figure(width_in: float, title: str, subtitle: str | None = None,
            subtitle2: str | None = None, bottom_in: float = 0.95) -> tuple:
    height_in = width_in * 0.56
    fig, ax = plt.subplots(figsize=(width_in, height_in), dpi=DPI)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    if subtitle is None:
        block_in = 0.9                    # title only
    else:
        block_in = 1.15 if subtitle2 is None else 1.55
    fig.subplots_adjust(
        left=1.15 / width_in,
        right=1.0 - 0.35 / width_in,
        top=1.0 - block_in / height_in,
        bottom=bottom_in / height_in,
    )
    title_x = 1.15 / width_in
    fig.text(title_x, 1.0 - 0.30 / height_in, title, color=INK_PRIMARY,
             fontsize=25, fontweight="semibold", ha="left", va="top")
    if subtitle:
        fig.text(title_x, 1.0 - 0.66 / height_in, subtitle, color=INK_MUTED,
                 fontsize=13, ha="left", va="top")
    if subtitle2:
        fig.text(title_x, 1.0 - 0.94 / height_in, subtitle2, color=INK_MUTED,
                 fontsize=13, ha="left", va="top")
    return fig, ax


def _style_spectrum_axes(ax, region: tuple[float, float], ymax: float) -> None:
    ax.set_xlim(region[1], region[0])           # ppm runs right to left
    ax.set_ylim(-0.04 * ymax, ymax)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1.0, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE_INK)
    ax.spines["bottom"].set_linewidth(1.0)
    ax.tick_params(axis="both", length=0, colors=INK_MUTED, labelsize=12)
    ax.set_xlabel("¹H chemical shift (ppm)", color=INK_SECONDARY, fontsize=13,
                  labelpad=10)
    ax.set_ylabel("Phase-corrected real intensity (a.u.)",
                  color=INK_SECONDARY, fontsize=13, labelpad=12)


def plot_region(row: dict, ppm: np.ndarray, real: np.ndarray, peak,
                region: tuple[float, float], window: tuple[float, float],
                ymax: float, out_dir: Path) -> Path:
    """One timepoint, zoomed on the peak region."""
    area = float(row["integrated_area"])
    detected = row["detected"] == "yes"
    fig, ax = _figure(
        10.0, format_dataset_plot_title(
            row["_dataset_display_name"],
            f"Peak region · {row['day_time'].replace(chr(10), ' ')}",
        ),
        f"{row['dx_file']} · +{float(row['elapsed_hours']):.2f} h from first "
        f"acquisition",
        (f"target peak {float(row['peak_ppm']):.3f} ppm · area {area:.2f} · "
         f"SNR {float(row['snr']):.1f}") if detected else
        (f"no peak passed detection near {float(row['peak_ppm']):.2f} ppm · "
         f"window integral {float(row['window_positive_area']):.2f}"),
    )
    _style_spectrum_axes(ax, region, ymax)

    mask = (ppm >= region[0]) & (ppm <= region[1])
    # The fixed integration window, drawn on every timepoint so the series is
    # measured the same way whether or not detection fired.
    ax.axvspan(window[0], window[1], color=SERIES, alpha=0.07, zorder=1,
               linewidth=0)
    ax.plot(ppm[mask], real[mask], color=SERIES, linewidth=1.6, zorder=3)

    if peak is not None:
        left = peak.interpolated_ppm - peak.width_ppm
        right = peak.interpolated_ppm + peak.width_ppm
        fill = (ppm >= left) & (ppm <= right)
        ax.fill_between(ppm[fill], 0.0, real[fill], color=ACCENT, alpha=0.35,
                        zorder=2, linewidth=0)
        height = float(np.max(real[fill])) if fill.any() else 0.0
        ax.annotate(
            f"{peak.interpolated_ppm:.3f} ppm",
            xy=(peak.interpolated_ppm, min(height, ymax)),
            xytext=(0, 10), textcoords="offset points",
            ha="center", va="bottom", color=INK_PRIMARY, fontsize=12,
            fontweight="semibold",
        )

    ax.legend(
        handles=[
            Patch(facecolor=ACCENT, alpha=0.35, label="Reported peak area"),
            Patch(facecolor=SERIES, alpha=0.07,
                  label=f"Fixed window {window[0]:g}–{window[1]:g} ppm"),
        ],
        loc="upper right", frameon=False, fontsize=12,
        handlelength=1.1, handleheight=1.1, labelcolor=INK_SECONDARY,
    )

    out_path = out_dir / f"{int(row['index']):02d}_{row['run']}_region.png"
    fig.savefig(out_path, facecolor=SURFACE, dpi=DPI)
    plt.close(fig)
    return out_path


def plot_left_peak_region(row: dict, ppm: np.ndarray, real: np.ndarray,
                          window: tuple[float, float], ymax: float,
                          out_dir: Path) -> Path:
    """One timepoint, zoomed on the leftmost line of the multiplet.

    The companion to ``peak_area_bar_leftpeak.png``: the shaded band is
    exactly the integral that chart's bar reports, between the valleys the
    walk found, so any bar can be read back against the trace it came from.
    Shares the region plots' intensity ceiling, so the traces are comparable
    across the series as well as against the bars.
    """
    zoom = (window[0] - LEFT_PEAK_ZOOM_PAD_PPM,
            window[1] + LEFT_PEAK_ZOOM_PAD_PPM)
    measured = bool(row["left_peak_from_ppm"] and row["left_peak_to_ppm"])

    fig, ax = _figure(
        10.0, format_dataset_plot_title(
            row["_dataset_display_name"],
            f"Leftmost peak · {row['day_time'].replace(chr(10), ' ')}",
        ),
    )
    _style_spectrum_axes(ax, zoom, ymax)

    mask = (ppm >= zoom[0]) & (ppm <= zoom[1])
    ax.plot(ppm[mask], real[mask], color=SERIES, linewidth=1.6, zorder=3)

    if not measured:
        ax.text(sum(window) / 2.0, ymax * 0.08, "n.d.", ha="center",
                va="bottom", color=INK_MUTED, fontsize=13)
    else:
        left_ppm = float(row["left_peak_from_ppm"])
        right_ppm = float(row["left_peak_to_ppm"])
        area = float(row["left_peak_area"])
        fill_x, fill_y, chord, positive = local_integration_fill(
            ppm, real, left_ppm, right_ppm
        )
        ax.plot(fill_x, chord, color=ACCENT, linewidth=1.2,
                linestyle="--", zorder=4)
        ax.fill_between(fill_x, chord, chord + positive, color=ACCENT,
                        alpha=0.35, zorder=2, linewidth=0)
        ax.scatter([fill_x[0], fill_x[-1]], [chord[0], chord[-1]],
                   color=ACCENT, s=22, zorder=5)

        apex_ppm = float(row["left_peak_ppm"])
        height = float(np.max(fill_y)) if fill_y.size else 0.0
        ax.annotate(
            f"{apex_ppm:.3f} ppm · area {area:.2f}",
            xy=(apex_ppm, min(height, ymax)),
            xytext=(0, 10), textcoords="offset points",
            ha="center", va="bottom", color=INK_PRIMARY, fontsize=12,
            fontweight="semibold",
        )

    out_path = out_dir / f"{int(row['index']):02d}_{row['run']}_leftpeak.png"
    fig.savefig(out_path, facecolor=SURFACE, dpi=DPI)
    plt.close(fig)
    return out_path


def plot_overlay(rows: list[dict], traces: list[tuple[np.ndarray, np.ndarray]],
                 region: tuple[float, float], ymax: float,
                 out_path: Path) -> Path:
    """Every timepoint's peak region on one axis, light-to-dark with time."""
    n = len(rows)
    fig, ax = _figure(
        12.0, format_dataset_plot_title(
            rows[0]["_dataset_display_name"], "Peak region over time"
        ),
        f"{n} acquisitions · {rows[0]['day_time'].replace(chr(10), ' ')} to "
        f"{rows[-1]['day_time'].replace(chr(10), ' ')} · "
        f"{float(rows[-1]['elapsed_hours']):.1f} h span",
        "color runs light to dark with acquisition time",
    )
    _style_spectrum_axes(ax, region, ymax)

    handles = []
    for i, (row, (ppm, real)) in enumerate(zip(rows, traces)):
        color = BLUE_RAMP[round(i * (len(BLUE_RAMP) - 1) / max(n - 1, 1))]
        mask = (ppm >= region[0]) & (ppm <= region[1])
        ax.plot(ppm[mask], real[mask], color=color, linewidth=1.5,
                zorder=3 + i)
        handles.append(
            Patch(facecolor=color,
                  label=row["day_time"].replace("\n", " ") +
                  f"   {float(row['integrated_area']):>6.2f}")
        )

    ax.legend(
        handles=handles, loc="upper left", frameon=False, fontsize=11,
        handlelength=1.1, handleheight=1.1, labelcolor=INK_SECONDARY,
        title="Acquisition        area", title_fontsize=11, alignment="left",
    )
    ax.get_legend().get_title().set_color(INK_MUTED)

    fig.savefig(out_path, facecolor=SURFACE, dpi=DPI)
    plt.close(fig)
    return out_path


def plot_area_bars(rows: list[dict], out_path: Path, *, value_key: str,
                   title: str, subtitle: str | None = None,
                   subtitle2: str | None = None) -> Path:
    """Peak area per timepoint, x = day and time."""
    n = len(rows)
    values = np.array([float(row[value_key] or 0.0) for row in rows])
    fig, ax = _figure(
        11.0,
        format_dataset_plot_title(rows[0]["_dataset_display_name"], title),
        subtitle,
        subtitle2,
        bottom_in=1.25,
    )
    top = float(values.max()) * 1.20
    ax.set_xlim(-0.7, n - 0.3)
    ax.set_ylim(0.0, top)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1.0, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE_INK)
    ax.spines["bottom"].set_linewidth(1.0)
    ax.set_xticks(range(n))
    ax.set_xticklabels([row["day_time"] for row in rows])
    ax.tick_params(axis="both", length=0, colors=INK_MUTED, labelsize=12)
    ax.set_ylabel("Area", color=INK_SECONDARY, fontsize=14, labelpad=12)
    ax.set_xlabel("Acquisition day and time", color=INK_SECONDARY, fontsize=13,
                  labelpad=10)

    fig.canvas.draw()
    bar_width = min(0.62, _px_to_data(ax, MAX_BAR_PX * PX)[0])
    rx, ry = _px_to_data(ax, CORNER_PX * PX)
    for i, value in enumerate(values):
        _rounded_bar(ax, i, value, bar_width, rx, ry, SERIES)

    peak_i = int(values.argmax())
    pad = top * 0.022
    for i, value in enumerate(values):
        if value <= 0:
            ax.text(i, pad, "n.d.", ha="center", va="bottom", color=INK_MUTED,
                    fontsize=12)
        elif i == peak_i:
            ax.text(i, value + pad, f"{value:.2f}", ha="center", va="bottom",
                    color=INK_PRIMARY, fontsize=13, fontweight="semibold")

    fig.savefig(out_path, facecolor=SURFACE, dpi=DPI)
    plt.close(fig)
    return out_path


def plot_reaction_timeline(rows: list[dict], out_path: Path, *,
                           sm_window: tuple[float, float],
                           silane_window: tuple[float, float],
                           product_window: tuple[float, float]) -> Path:
    """Every tracked band across the run, against the bench timeline.

    Raw areas on one axis rather than each series normalised to its own
    maximum: normalising a band that is only ever noise would draw it as a
    full-height bar and invent a trend that is not there.
    """
    n = len(rows)
    series = [
        ("starting_material_area",
         f"Starting material {sm_window[0]:g}–{sm_window[1]:g} ppm", ACCENT),
        ("silane_area",
         f"Added silane {silane_window[0]:g}–{silane_window[1]:g} ppm",
         "#1baf7a"),
        ("left_peak_area",
         f"Product {product_window[0]:g}–{product_window[1]:g} ppm "
         f"(left line)", SERIES),
    ]
    values = {key: np.array([float(row[key] or 0.0) for row in rows])
              for key, _, _ in series}
    top = max(arr.max() for arr in values.values()) * 1.22

    maxima = " · ".join(
        f"{label.split(' ')[0].lower()} max {values[key].max():.1f}"
        for key, label, _ in series
    )
    fig, ax = _figure(
        13.0, format_dataset_plot_title(
            rows[0]["_dataset_display_name"], "Reaction timeline"
        ),
        f"{n} acquisitions · raw integrated area, one axis · {maxima}",
        "x axis is the bench step; areas are not normalised, so a band that "
        "stays flat really is absent",
        bottom_in=1.55,
    )
    ax.set_xlim(-0.7, n - 0.3)
    ax.set_ylim(0.0, top)
    ax.yaxis.grid(True, color=GRIDLINE, linewidth=1.0, zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(BASELINE_INK)
    ax.spines["bottom"].set_linewidth(1.0)
    ax.set_xticks(range(n))
    ax.set_xticklabels([row["_axis_label"] for row in rows])
    ax.tick_params(axis="both", length=0, colors=INK_MUTED, labelsize=12)
    ax.set_ylabel("Area", color=INK_SECONDARY, fontsize=14, labelpad=12)
    ax.set_xlabel("Bench step", color=INK_SECONDARY, fontsize=13, labelpad=10)

    fig.canvas.draw()
    gap = _px_to_data(ax, 2.0 * PX)[0]
    bar_width = min(0.26, _px_to_data(ax, MAX_BAR_PX * PX)[0])
    rx, ry = _px_to_data(ax, CORNER_PX * PX)
    offsets = [-(bar_width + gap), 0.0, bar_width + gap]

    for (key, _, color), offset in zip(series, offsets):
        for i, value in enumerate(values[key]):
            _rounded_bar(ax, i + offset, value, bar_width, rx, ry, color)

    # Where a reagent went in, so the reader sees which bars are downstream.
    for i, row in enumerate(rows):
        if not row["_event"]:
            continue
        ax.axvline(i - 0.5, color=INK_MUTED, linewidth=1.2, linestyle=(0, (4, 3)),
                   zorder=2)
        ax.annotate(
            row["_event"], xy=(i - 0.5, top * 0.97), xytext=(6, 0),
            textcoords="offset points", ha="left", va="top",
            color=INK_SECONDARY, fontsize=12, fontweight="semibold",
        )

    ax.legend(
        handles=[Patch(facecolor=color, label=label)
                 for _, label, color in series],
        loc="lower right", bbox_to_anchor=(1.0, 1.01), borderaxespad=0.0,
        frameon=False, fontsize=12, ncol=3, handlelength=1.1,
        handleheight=1.1, labelcolor=INK_SECONDARY,
    )

    fig.savefig(out_path, facecolor=SURFACE, dpi=DPI)
    plt.close(fig)
    return out_path


# --------------------------------------------------------------------------
# cli
# --------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gather a multi-run automated series into one folder of "
                    "time-series results: master CSV, per-timepoint region "
                    "plots, an overlay, and an area bar chart."
    )
    parser.add_argument(
        "series_dir", nargs="?", type=Path, default=DEFAULT_SERIES,
        help=f"directory of run folders (default: {DEFAULT_SERIES.name})",
    )
    parser.add_argument(
        "--out-dir", type=Path, default=None,
        help=f"output directory (default: <series_dir>/{DEFAULT_OUT_NAME})",
    )
    parser.add_argument(
        "--target-ppm", type=float, default=5.80,
        help="chemical shift of the peak of interest (default: 5.80)",
    )
    parser.add_argument(
        "--target-tolerance", type=float, default=0.12,
        help="how far from the target a picked peak may sit (default: 0.12)",
    )
    parser.add_argument(
        "--window", type=float, nargs=2, metavar=("LEFT", "RIGHT"),
        default=(5.70, 5.90),
        help="fixed integration window, ppm (default: 5.70 5.90)",
    )
    parser.add_argument(
        "--region", type=float, nargs=2, metavar=("LEFT", "RIGHT"),
        default=(5.60, 6.00),
        help="plotted ppm range (default: 5.60 6.00)",
    )
    parser.add_argument(
        "--sm-window", type=float, nargs=2, metavar=("LEFT", "RIGHT"),
        default=(5.40, 5.52),
        help="starting-material integration window, ppm (default: 5.40 5.52)",
    )
    parser.add_argument(
        "--silane-window", type=float, nargs=2, metavar=("LEFT", "RIGHT"),
        default=(4.94, 5.06),
        help="added-silane integration window, ppm (default: 4.94 5.06)",
    )
    parser.add_argument(
        "--steps", type=Path, default=None,
        help=f"experiment log mapping dx_file to a step label and event "
             f"(default: <series_dir>/{STEPS_FILE} if present)",
    )
    parser.add_argument(
        "--line-height-fraction", type=float, default=0.20,
        help="a multiplet line counts only if it reaches this fraction of the "
             "tallest line in the window (default: 0.20)",
    )
    parser.add_argument(
        "--multiplet-span", type=float, default=0.05,
        help="how far a line may sit from the tallest one and still belong to "
             "the same multiplet, ppm (default: 0.05)",
    )
    parser.add_argument(
        "--region-ymax", type=float, default=None,
        help="shared intensity ceiling for the spectrum plots "
             "(default: 1.1x the tallest trace in the series)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    series_dir = args.series_dir.resolve()
    if not series_dir.is_dir():
        raise SystemExit(f"series directory not found: {series_dir}")

    out_dir = (args.out_dir or series_dir / DEFAULT_OUT_NAME).resolve()
    dataset_display_name = resolve_dataset_display_name(input_paths=series_dir)
    region_dir = out_dir / "region_plots"
    region_dir.mkdir(parents=True, exist_ok=True)
    # Region plots are numbered by position in the series, so dropping an
    # acquisition renumbers the rest and would otherwise strand the old files
    # under indices that no longer mean anything. Only this script's own
    # output pattern is cleared.
    for pattern in ("*_region.png", "*_leftpeak.png"):
        for stale in region_dir.glob(pattern):
            stale.unlink()

    plt.rcParams["font.family"] = "sans-serif"
    plt.rcParams["font.sans-serif"] = FONT_STACK

    window = (min(args.window), max(args.window))
    region = (min(args.region), max(args.region))
    sm_window = (min(args.sm_window), max(args.sm_window))
    silane_window = (min(args.silane_window), max(args.silane_window))
    steps = read_steps(args.steps or series_dir / STEPS_FILE)
    if steps:
        print(f"experiment log: {len(steps)} labelled acquisitions")

    collected = []
    for run in find_runs(series_dir):
        dx_path = next(iter(sorted((run / "raw_nmr").glob("*.dx"))))
        pipeline = read_pipeline_peak(run)
        if pipeline is None:
            print(f"  {run.name}: no _peaks_simple.csv, skipped")
            continue
        ppm, real, picked = process_spectrum(dx_path)
        peak = target_peak(picked, args.target_ppm, args.target_tolerance)
        integral = integrate_above_local_baseline(
            ppm, real, left_ppm=window[0], right_ppm=window[1]
        )
        collected.append({
            "run": run.name,
            "dx_file": dx_path.name,
            "pipeline": pipeline,
            "ppm": ppm,
            "real": real,
            "peak": peak,
            "left_peak": left_peak_integral(
                dx_path, ppm, real, window,
                height_fraction=args.line_height_fraction,
                multiplet_span=args.multiplet_span,
            ),
            "noise": picked.noise,
            "window_area": integral.positive_area,
            "sm_area": window_area(ppm, real, sm_window),
            "sm_detected": any(
                sm_window[0] <= peak.interpolated_ppm <= sm_window[1]
                for peak in picked.peaks
            ),
            "silane_area": window_area(ppm, real, silane_window),
            "timestamp": datetime.fromisoformat(pipeline["timestamp"]),
            # Acquisition START, from the filename -- this is the clock time
            # the bench log refers to; the header timestamp is ~10 min later.
            "started": datetime.strptime(dx_path.name[:15], "%Y%m%d_%H%M%S"),
            "step": steps.get(dx_path.name, {}),
        })

    if not collected:
        raise SystemExit("no runs produced a peak row")
    collected.sort(key=lambda item: item["timestamp"])
    start = collected[0]["timestamp"]

    rows: list[dict] = []
    for index, item in enumerate(collected, 1):
        pipeline = item["pipeline"]
        area = float(pipeline["integrated_area"])
        # A left-peak measurement only counts where the pipeline also resolved
        # the target; otherwise the picker is chasing noise in an empty window.
        left = item["left_peak"] if area > 0 else None
        rows.append({
            "index": index,
            "run": item["run"],
            "dx_file": item["dx_file"],
            "timestamp": item["timestamp"].isoformat(),
            "day_time": item["timestamp"].strftime("%m-%d\n%H:%M"),
            "elapsed_hours": f"{(item['timestamp'] - start).total_seconds() / 3600.0:.3f}",
            "peak_ppm": pipeline["peak_ppm"],
            "integrated_area": f"{area:.2f}",
            "intensity": pipeline["intensity"],
            "snr": pipeline["snr"],
            "prominence_snr": pipeline["prominence_snr"],
            "width_hz": pipeline["width_hz"],
            "detected": "yes" if area > 0 else "no",
            "window_left_ppm": f"{window[0]:.2f}",
            "window_right_ppm": f"{window[1]:.2f}",
            "window_positive_area": f"{item['window_area']:.2f}",
            "region_noise": f"{item['noise']:.2f}",
            "left_peak_ppm": f"{left['ppm']:.3f}" if left else "",
            "left_peak_height": f"{left['height']:.2f}" if left else "",
            "left_peak_area": f"{left['area']:.2f}" if left else "0.00",
            "left_peak_from_ppm": f"{left['from_ppm']:.3f}" if left else "",
            "left_peak_to_ppm": f"{left['to_ppm']:.3f}" if left else "",
            "multiplet_lines": left["lines"] if left else "",
            "step_label": item["step"].get("label", ""),
            "acquired_start": item["started"].isoformat(),
            "starting_material_area": f"{item['sm_area']:.2f}",
            "silane_area": f"{item['silane_area']:.2f}",
            "_dataset_display_name": dataset_display_name,
            # Not a CSV column: the event marker and the axis label the
            # timeline plot draws.
            "_event": item["step"].get("event", ""),
            "_axis_label": (
                item["step"].get("label")
                or item["started"].strftime("%m-%d\n%H:%M")
            ).replace(" · ", "\n"),
        })

    csv_path = out_dir / "peak_area_timeseries.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS,
                                extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "day_time": row["day_time"].replace("\n", " ")})
    print(f"master table -> {csv_path}")

    ymax = args.region_ymax
    if ymax is None:
        peaks_in_region = [
            float(np.max(item["real"][
                (item["ppm"] >= region[0]) & (item["ppm"] <= region[1])
            ]))
            for item in collected
        ]
        ymax = max(peaks_in_region) * 1.10
    print(f"shared intensity ceiling: {ymax:.0f}")

    for row, item in zip(rows, collected):
        path = plot_region(row, item["ppm"], item["real"], item["peak"],
                           region, window, ymax, region_dir)
        print(f"  {path.relative_to(out_dir)}")
        left_path = plot_left_peak_region(row, item["ppm"], item["real"],
                                          window, ymax, region_dir)
        print(f"  {left_path.relative_to(out_dir)}")

    overlay = plot_overlay(
        rows, [(item["ppm"], item["real"]) for item in collected],
        region, ymax, out_dir / "overlay_region_timeseries.png",
    )
    print(f"overlay -> {overlay.name}")
    span = f"{float(rows[-1]['elapsed_hours']):.1f} h span"
    bars = plot_area_bars(
        rows, out_dir / "peak_area_bar.png",
        value_key="integrated_area",
        title="Automated peak area over time",
        subtitle=f"{len(rows)} acquisitions · target peak near "
                 f"{args.target_ppm:g} ppm · {span}",
        subtitle2="bars marked n.d. had no peak pass detection at the target "
                  "shift",
    )
    print(f"bar chart -> {bars.name}")

    # Title only: the shift range and the valley-to-valley integration rule
    # stay in the CSV (left_peak_ppm / left_peak_from_ppm / left_peak_to_ppm)
    # rather than on the chart.
    left_bars = plot_area_bars(
        rows, out_dir / "peak_area_bar_leftpeak.png",
        value_key="left_peak_area",
        title="Peak Area Over Time",
    )
    print(f"bar chart -> {left_bars.name}")

    sm_seen = sum(1 for item in collected if item["sm_detected"])
    sm_bars = plot_area_bars(
        rows, out_dir / "peak_area_bar_startingmaterial.png",
        value_key="starting_material_area",
        title="Starting material peak area over time",
        subtitle=f"{len(rows)} acquisitions · "
                 f"{sm_window[0]:g}–{sm_window[1]:g} ppm · {span}",
        subtitle2=(
            "fixed-window integral · no peak was detected at this shift in "
            "ANY acquisition, so these bars are baseline noise, not a trend"
            if sm_seen == 0 else
            f"fixed-window integral · a peak was detected here in "
            f"{sm_seen} of {len(rows)} acquisitions"
        ),
    )
    print(f"bar chart -> {sm_bars.name}")

    timeline = plot_reaction_timeline(
        rows, out_dir / "reaction_timeline.png",
        sm_window=sm_window, silane_window=silane_window,
        product_window=window,
    )
    print(f"timeline -> {timeline.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
