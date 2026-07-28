"""Plots for the optional statistics pipeline.

Kept separate from :mod:`chemyx_lab.analysis.statistics_report` (which does the
numerics) so matplotlib is only imported when plots are actually requested.
Every plot is wrapped so a single drawing failure records a warning and never
aborts the run; :func:`render_plots` returns the list of files written.

Time axes use **real elapsed hours**.  These plots visualise already-computed
quantities from the report's CSV tables; they never re-derive statistics.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable


def _plt():
    import matplotlib

    matplotlib.use("Agg")
    from matplotlib import pyplot as plt

    return plt


def _np():
    import numpy as np

    return np


def _finite_xy(x, y):
    np = _np()
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    return x[mask], y[mask]


def _line_plot(path: Path, x, y, *, title, xlabel, ylabel, marker="o-"):
    plt = _plt()
    xf, yf = _finite_xy(x, y)
    if xf.size == 0:
        return None
    fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
    ax.plot(xf, yf, marker, color="#1f77b4", linewidth=1.5, markersize=5)
    ax.set_title(title, fontsize=11, fontweight="bold")
    ax.set_xlabel(xlabel, fontsize=10)
    ax.set_ylabel(ylabel, fontsize=10)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def render_plots(report, plots_dir: Path) -> list[str]:
    """Draw the statistics plot set into ``plots_dir/statistics``; return paths."""
    out = Path(plots_dir) / "statistics"
    written: list[str] = []

    def _try(name, fn):
        try:
            result = fn(out / name)
            if result is not None:
                written.append(str(result))
        except Exception as exc:  # a plot must never abort the run
            report.warnings.append(f"plot {name} failed: {exc}")

    qc = report.qc_series
    if qc:
        elapsed = qc.get("elapsed", [])
        _try("noise_vs_time.png", lambda p: _line_plot(
            p, elapsed, qc.get("region_noise", []),
            title="Region noise vs time", xlabel="Elapsed time (hours)",
            ylabel="Robust noise (intensity)"))
        _try("linewidth_vs_time.png", lambda p: _line_plot(
            p, elapsed, qc.get("median_width_hz", []),
            title="Median linewidth vs time", xlabel="Elapsed time (hours)",
            ylabel="Median FWHM (Hz)"))
        _try("reference_drift_vs_time.png", lambda p: _line_plot(
            p, elapsed, qc.get("reference_shift_ppm", []),
            title="Applied reference shift vs time", xlabel="Elapsed time (hours)",
            ylabel="Reference shift (ppm)"))
        _try("qc_pass_rate_vs_time.png", lambda p: _line_plot(
            p, elapsed, qc.get("qc_pass_fraction", []),
            title="Per-spectrum QC pass fraction vs time",
            xlabel="Elapsed time (hours)", ylabel="Fraction of peaks passing QC"))
        _try("phase_vs_time.png", lambda p: _phase_plot(
            p, elapsed, qc.get("phase0_deg", []), qc.get("phase1_deg", [])))

    sim = report.similarity_series
    if sim:
        _try("spectral_similarity_vs_time.png", lambda p: _similarity_plot(p, sim))

    if report.target_series:
        _try("area_with_ci_vs_time.png", lambda p: _area_plot(p, report.target_series))
        _try("rate_vs_time.png", lambda p: _rate_plot(p, report.target_series))
    if report.kinetic_best:
        _try("kinetic_model_comparison.png", lambda p: _kinetic_plot(p, report.kinetic_best))
    return written


def _phase_plot(path, elapsed, phase0, phase1):
    plt = _plt()
    np = _np()
    e = np.asarray(elapsed, dtype=float)
    if not np.any(np.isfinite(e)):
        return None
    fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
    for values, label, color in ((phase0, "phase0 (deg)", "#1f77b4"),
                                 (phase1, "phase1 (deg)", "#d62728")):
        xf, yf = _finite_xy(e, values)
        if xf.size:
            ax.plot(xf, yf, "o-", label=label, color=color, linewidth=1.4)
    ax.set_title("Phase correction vs time", fontsize=11, fontweight="bold")
    ax.set_xlabel("Elapsed time (hours)", fontsize=10)
    ax.set_ylabel("Phase (degrees)", fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def _similarity_plot(path, sim):
    plt = _plt()
    np = _np()
    e = np.asarray(sim.get("elapsed", []), dtype=float)
    fig, ax = plt.subplots(figsize=(9, 5), dpi=140)
    drew = False
    for key, label, color in (("correlation_to_first", "Pearson r to first", "#1f77b4"),
                              ("cosine_similarity_to_first", "cosine to first", "#2ca02c")):
        xf, yf = _finite_xy(e, sim.get(key, []))
        if xf.size:
            ax.plot(xf, yf, "o-", label=label, color=color, linewidth=1.4)
            drew = True
    if not drew:
        plt.close(fig)
        return None
    ax.set_title("Whole-spectrum similarity vs time", fontsize=11, fontweight="bold")
    ax.set_xlabel("Elapsed time (hours)", fontsize=10)
    ax.set_ylabel("Similarity to first spectrum", fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def _area_plot(path, targets):
    plt = _plt()
    np = _np()
    fig, ax = plt.subplots(figsize=(9.5, 5.5), dpi=140)
    cmap = plt.get_cmap("viridis")
    keys = list(targets)
    drew = False
    for i, key in enumerate(keys):
        target = targets[key]
        e = np.asarray(target["elapsed"], dtype=float)
        a = np.asarray(target["area"], dtype=float)
        mask = np.isfinite(e) & np.isfinite(a)
        if not np.any(mask):
            continue
        color = cmap(i / max(1, len(keys) - 1))
        unc = target.get("area_uncertainty")
        if unc is not None:
            u = np.asarray(unc, dtype=float)[mask]
            ax.errorbar(e[mask], a[mask], yerr=np.where(np.isfinite(u), u, 0.0),
                        fmt="o-", color=color, capsize=3, linewidth=1.4,
                        label=f"{target['kind']}:{target['name']}")
        else:
            ax.plot(e[mask], a[mask], "s--", color=color, linewidth=1.4,
                    label=f"{target['kind']}:{target['name']}")
        drew = True
    if not drew:
        plt.close(fig)
        return None
    ax.set_title("Integrated area vs time (fixed-window +/-1 SE)", fontsize=11, fontweight="bold")
    ax.set_xlabel("Elapsed time (hours)", fontsize=10)
    ax.set_ylabel("Integrated positive area", fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def _rate_plot(path, targets):
    from .time_series import rolling_slope

    plt = _plt()
    np = _np()
    fig, ax = plt.subplots(figsize=(9.5, 5.5), dpi=140)
    cmap = plt.get_cmap("plasma")
    keys = list(targets)
    drew = False
    for i, key in enumerate(keys):
        target = targets[key]
        e = list(target["elapsed"])
        a = list(target["area"])
        slopes = rolling_slope(e, a, window=4)
        xs = [e[j] for j in range(len(e)) if np.isfinite(slopes[j].slope)]
        ys = [slopes[j].slope for j in range(len(e)) if np.isfinite(slopes[j].slope)]
        if not xs:
            continue
        color = cmap(i / max(1, len(keys) - 1))
        ax.plot(xs, ys, "o-", color=color, linewidth=1.4,
                label=f"{target['kind']}:{target['name']}")
        drew = True
    if not drew:
        plt.close(fig)
        return None
    ax.axhline(0.0, color="#777777", linewidth=0.8)
    ax.set_title("Rolling rate (slope of area vs time)", fontsize=11, fontweight="bold")
    ax.set_xlabel("Elapsed time (hours)", fontsize=10)
    ax.set_ylabel("Rate (area per hour)", fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path


def _kinetic_plot(path, kinetic_best):
    from .kinetics import MODELS

    plt = _plt()
    np = _np()
    fig, ax = plt.subplots(figsize=(9.5, 5.5), dpi=140)
    cmap = plt.get_cmap("tab10")
    drew = False
    for i, (key, info) in enumerate(kinetic_best.items()):
        target = info["target"]
        e = np.asarray(target["elapsed"], dtype=float)
        a = np.asarray(target["area"], dtype=float)
        mask = np.isfinite(e) & np.isfinite(a)
        if not np.any(mask):
            continue
        color = cmap(i % 10)
        ax.scatter(e[mask], a[mask], color=color, s=28, zorder=3,
                   label=f"{target['name']} (data)")
        spec = MODELS.get(info["model"])
        params = info.get("params", {})
        if spec is not None and params:
            tt = np.linspace(float(np.min(e[mask])), float(np.max(e[mask])), 200)
            try:
                yy = spec.func(tt, *[params[name] for name in spec.param_names])
                ax.plot(tt, yy, "-", color=color, linewidth=1.6,
                        label=f"{target['name']}: {info['model']}")
                drew = True
            except Exception:
                pass
    if not drew:
        plt.close(fig)
        return None
    ax.set_title("Best kinetic model (by AICc) vs data", fontsize=11, fontweight="bold")
    ax.set_xlabel("Elapsed time (hours)", fontsize=10)
    ax.set_ylabel("Integrated area", fontsize=10)
    ax.grid(True, alpha=0.25)
    ax.legend(fontsize=7)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)
    return path
