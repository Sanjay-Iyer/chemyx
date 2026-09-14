"""Interactive, non-production NMR processing explorer.

This module reuses the audited JCAMP-to-spectrum processing functions but never
writes production results.  The pure :class:`NmrProcessingExplorer` model is
shared by the standalone Matplotlib app and the Jupyter/ipywidgets interface.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path

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
from build_timeseries_results import left_peak_integral  # noqa: E402
from inspect_processing import (  # noqa: E402
    ALS_ITERATIONS,
    ALS_LAMBDA,
    ALS_P,
    FFT_POINTS,
    LB_HZ,
    REGION,
    SILANE_WINDOW,
    STARTING_MATERIAL_WINDOW,
    TARGET_WINDOW,
    _arpls_baseline,
    _observe_frequency,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SERIES = REPO_ROOT / "results/runs/automated/chemyx_demo_081026_v3"
DEFAULT_EXPLORATORY_ROOT = (
    REPO_ROOT
    / "results/nmr_processing_inspection/chemyx_demo_081026_v3_plot_cleanup_v3"
    / "interactive"
)

ZOOM_PRESETS = {
    "full spectrum": None,
    "reference region": (4.70, 5.30),
    "silane region": (4.94, 5.06),
    "starting-material region": (5.40, 5.52),
    "product region": (5.70, 5.90),
    "product wider context": (5.60, 6.00),
}
DISPLAY_MODES = (
    "before only",
    "after only",
    "baseline only",
    "difference only",
    "overlay",
    "side-by-side before/after",
)
BASELINE_METHODS = ("none", "production AsLS", "polynomial", "arPLS")


@dataclass(frozen=True)
class ExplorerSettings:
    p0: float
    p1: float
    use_production_reference: bool = True
    manual_shift_ppm: float = 0.0
    baseline_method: str = "production AsLS"
    baseline_lambda: float = ALS_LAMBDA
    baseline_asymmetry: float = ALS_P
    baseline_iterations: int = ALS_ITERATIONS
    polynomial_degree: int = 2


@dataclass(frozen=True)
class ExplorerResult:
    ppm: np.ndarray
    before_phase: np.ndarray
    phased_referenced: np.ndarray
    baseline: np.ndarray
    corrected: np.ndarray
    settings: ExplorerSettings
    mode: str
    target_ppm: float
    stage1_area: float
    stage1_bounds: tuple[float, float] | None
    fixed_product_area: float
    left_peak_area: float
    left_peak_bounds: tuple[float, float] | None
    starting_material_area: float
    silane_area: float


def representative_dx_files(series_dir: Path = DEFAULT_SERIES) -> list[Path]:
    """Return the real series acquisitions in stable path order."""
    return sorted(Path(series_dir).glob("*/raw_nmr/*.dx"))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


class NmrProcessingExplorer:
    """Cached interactive processing model with a fail-closed export boundary."""

    def __init__(
        self,
        dx_path: Path,
        *,
        exploratory_root: Path = DEFAULT_EXPLORATORY_ROOT,
        dataset_display_name: str | None = None,
    ):
        self.dx_path = Path(dx_path).resolve()
        if self.dx_path.suffix.lower() != ".dx" or not self.dx_path.is_file():
            raise ValueError(f"expected an existing .dx file: {self.dx_path}")
        self.exploratory_root = Path(exploratory_root).resolve()
        allowed = DEFAULT_EXPLORATORY_ROOT.resolve()
        if not _is_relative_to(self.exploratory_root, allowed):
            raise ValueError(
                f"exploratory_root must remain inside {DEFAULT_EXPLORATORY_ROOT}"
            )
        self.dataset_display_name = (
            dataset_display_name
            or resolve_dataset_display_name(input_paths=self.dx_path)
        )
        self.inspection = build_processing_inspection(
            self.dx_path,
            line_broadening_hz=LB_HZ,
            zero_fill_points=FFT_POINTS,
            inverse_phase=True,
            als_smoothness=ALS_LAMBDA,
            als_asymmetry=ALS_P,
            als_iterations=ALS_ITERATIONS,
        )
        self.production_settings = ExplorerSettings(
            p0=float(self.inspection.phase0_deg),
            p1=float(self.inspection.phase1_deg),
        )
        self.settings = self.production_settings

    def reset_to_production(self) -> ExplorerSettings:
        self.settings = self.production_settings
        return self.settings

    def with_settings(self, **changes) -> ExplorerSettings:
        candidate = replace(self.settings, **changes)
        if candidate.baseline_method not in BASELINE_METHODS:
            raise ValueError(f"unknown baseline method: {candidate.baseline_method}")
        if candidate.baseline_lambda <= 0:
            raise ValueError("baseline_lambda must be positive")
        if not 0 < candidate.baseline_asymmetry < 1:
            raise ValueError("baseline_asymmetry must be between zero and one")
        if candidate.baseline_iterations < 1:
            raise ValueError("baseline_iterations must be positive")
        if not 0 <= candidate.polynomial_degree <= 8:
            raise ValueError("polynomial_degree must be between 0 and 8")
        self.settings = candidate
        return candidate

    def _baseline(
        self, phased_real: np.ndarray, settings: ExplorerSettings
    ) -> np.ndarray:
        if settings.baseline_method == "none":
            return np.zeros_like(phased_real)
        if settings.baseline_method == "production AsLS":
            return np.asarray(
                asymmetric_least_squares_baseline(
                    phased_real,
                    smoothness=settings.baseline_lambda,
                    asymmetry=settings.baseline_asymmetry,
                    iterations=settings.baseline_iterations,
                )
            )
        if settings.baseline_method == "polynomial":
            _, baseline, _, _ = subtract_abd_polynomial_baseline(
                phased_real,
                polynomial_order=settings.polynomial_degree,
            )
            return np.asarray(baseline)
        return np.asarray(
            _arpls_baseline(
                phased_real,
                smoothness=settings.baseline_lambda,
                iterations=settings.baseline_iterations,
            )
        )

    def compute(self, settings: ExplorerSettings | None = None) -> ExplorerResult:
        settings = settings or self.settings
        if settings.baseline_method not in BASELINE_METHODS:
            raise ValueError(f"unknown baseline method: {settings.baseline_method}")
        import nmrglue as ng

        phased = ng.proc_base.ps(
            np.asarray(self.inspection.fft_spectrum),
            p0=float(settings.p0),
            p1=float(settings.p1),
            inv=True,
        )
        phased_real = np.real(phased)
        shift = 0.0 if settings.use_production_reference else settings.manual_shift_ppm
        ppm = np.asarray(self.inspection.ppm_axis) + float(shift)
        baseline = self._baseline(phased_real, settings)
        corrected = phased_real - baseline
        picked = pick_spectrum_region(
            ppm,
            corrected,
            region_min_ppm=REGION[0],
            region_max_ppm=REGION[1],
            min_prominence_snr=5.0,
            min_distance_ppm=0.04,
            min_width_ppm=0.015,
            baseline_polynomial_order=3,
            smoothing_window_ppm=0.006,
            quantitative_intensity=corrected,
            source=self.dx_path,
        )
        observe_frequency = _observe_frequency(self.inspection.metadata)
        target_candidates = [
            peak
            for peak in picked.peaks
            if TARGET_WINDOW[0] + shift
            <= peak.interpolated_ppm
            <= TARGET_WINDOW[1] + shift
            and peak.snr >= 3.0
            and peak.prominence_snr >= 3.0
            and 1.0 <= peak.width_ppm * observe_frequency <= 10.0
            and peak.positive_area > 0
        ]
        target = (
            max(target_candidates, key=lambda peak: peak.snr)
            if target_candidates
            else None
        )
        fixed = integrate_above_local_baseline(
            ppm,
            corrected,
            left_ppm=TARGET_WINDOW[0] + shift,
            right_ppm=TARGET_WINDOW[1] + shift,
        )
        starting = integrate_above_local_baseline(
            ppm,
            corrected,
            left_ppm=STARTING_MATERIAL_WINDOW[0] + shift,
            right_ppm=STARTING_MATERIAL_WINDOW[1] + shift,
        )
        silane = integrate_above_local_baseline(
            ppm,
            corrected,
            left_ppm=SILANE_WINDOW[0] + shift,
            right_ppm=SILANE_WINDOW[1] + shift,
        )
        left = left_peak_integral(
            self.dx_path,
            ppm,
            corrected,
            (TARGET_WINDOW[0] + shift, TARGET_WINDOW[1] + shift),
            height_fraction=0.20,
            multiplet_span=0.05,
        )
        stage_bounds = None
        if target is not None:
            half = max(target.width_ppm, 0.015)
            stage_bounds = (
                target.interpolated_ppm - half,
                target.interpolated_ppm + half,
            )
        mode = (
            "production"
            if settings == self.production_settings
            else "exploratory override"
        )
        return ExplorerResult(
            ppm=ppm,
            before_phase=np.real(self.inspection.fft_spectrum),
            phased_referenced=phased_real,
            baseline=baseline,
            corrected=corrected,
            settings=settings,
            mode=mode,
            target_ppm=target.interpolated_ppm if target else 0.0,
            stage1_area=target.positive_area if target else 0.0,
            stage1_bounds=stage_bounds,
            fixed_product_area=fixed.positive_area,
            left_peak_area=float(left["area"]) if left and target else 0.0,
            left_peak_bounds=(float(left["from_ppm"]), float(left["to_ppm"]))
            if left and target
            else None,
            starting_material_area=starting.positive_area,
            silane_area=silane.positive_area,
        )

    def _export_path(self, filename: str) -> Path:
        filename = Path(filename).name
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", filename):
            raise ValueError("export filename contains unsupported characters")
        exports = (self.exploratory_root / "exports").resolve()
        if not _is_relative_to(exports, DEFAULT_EXPLORATORY_ROOT.resolve()):
            raise ValueError("export directory escaped the exploratory root")
        exports.mkdir(parents=True, exist_ok=True)
        path = (exports / filename).resolve()
        if not _is_relative_to(path, exports):
            raise ValueError("export path escaped the exports directory")
        return path

    def export_settings(self, filename: str = "exploratory_settings.json") -> Path:
        path = self._export_path(filename)
        if path.suffix.lower() != ".json":
            raise ValueError("settings exports must use .json")
        payload = {
            "exploratory_only": True,
            "source": str(self.dx_path),
            "dataset_display_name": self.dataset_display_name,
            "mode": self.compute().mode,
            "settings": asdict(self.settings),
        }
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def export_plot(
        self,
        filename: str = "exploratory_snapshot.png",
        *,
        display_mode: str = "overlay",
        zoom: str = "product wider context",
    ) -> Path:
        path = self._export_path(filename)
        if path.suffix.lower() != ".png":
            raise ValueError("plot exports must use .png")
        result = self.compute()
        fig = plot_result(
            result,
            dataset=self.dataset_display_name,
            acquisition=self.dx_path.name,
            display_mode=display_mode,
            zoom=zoom,
        )
        fig.savefig(path, dpi=180, bbox_inches="tight")
        plt.close(fig)
        return path


def _decorate_axis(ax, ppm: np.ndarray, zoom: str, manual_xlim=None):
    bounds = manual_xlim or ZOOM_PRESETS.get(zoom)
    if bounds is None:
        ax.set_xlim(float(np.max(ppm)), float(np.min(ppm)))
    else:
        ax.set_xlim(max(bounds), min(bounds))
    ax.set_xlabel("Chemical shift (ppm; high ppm → low ppm)")
    ax.set_ylabel("Intensity (a.u.)")
    ax.grid(alpha=0.2)


def plot_result(
    result: ExplorerResult,
    *,
    dataset: str,
    acquisition: str,
    display_mode: str = "overlay",
    zoom: str = "full spectrum",
    manual_xlim=None,
    y_autoscale: bool = True,
    manual_ylim=None,
    show_baseline: bool = True,
    show_stage1: bool = True,
    show_fixed: bool = True,
    show_left: bool = True,
):
    """Build a fresh figure for a computed exploratory result."""
    if display_mode not in DISPLAY_MODES:
        raise ValueError(f"unknown display mode: {display_mode}")
    if display_mode == "side-by-side before/after":
        fig, axes = plt.subplots(1, 2, figsize=(13, 5), layout="constrained")
        axes[0].plot(result.ppm, result.phased_referenced, lw=0.7)
        axes[0].set_title("Before baseline")
        axes[1].plot(result.ppm, result.corrected, lw=0.7)
        axes[1].set_title("After baseline")
        for ax in axes:
            _decorate_axis(ax, result.ppm, zoom, manual_xlim)
            if not y_autoscale and manual_ylim is not None:
                ax.set_ylim(*sorted(manual_ylim))
    else:
        fig, ax = plt.subplots(figsize=(11, 6), layout="constrained")
        if display_mode == "before only":
            ax.plot(result.ppm, result.phased_referenced, lw=0.7, label="Before")
        elif display_mode == "after only":
            ax.plot(result.ppm, result.corrected, lw=0.7, label="After")
        elif display_mode == "baseline only":
            ax.plot(result.ppm, result.baseline, lw=0.9, label="Baseline")
        elif display_mode == "difference only":
            ax.plot(result.ppm, result.baseline, lw=0.9, label="Removed signal")
        else:
            ax.plot(result.ppm, result.phased_referenced, lw=0.6, label="Before")
            ax.plot(result.ppm, result.corrected, lw=0.7, label="After")
            if show_baseline:
                ax.plot(result.ppm, result.baseline, lw=0.9, label="Baseline")
        if show_stage1 and result.stage1_bounds:
            ax.axvspan(*result.stage1_bounds, color="#4c78a8", alpha=0.12)
        if show_fixed:
            shift = (
                0.0
                if result.settings.use_production_reference
                else result.settings.manual_shift_ppm
            )
            ax.axvspan(
                TARGET_WINDOW[0] + shift,
                TARGET_WINDOW[1] + shift,
                color="#f58518",
                alpha=0.08,
            )
        if show_left and result.left_peak_bounds:
            ax.axvline(result.left_peak_bounds[0], color="#54a24b", ls="--")
            ax.axvline(result.left_peak_bounds[1], color="#54a24b", ls="--")
        _decorate_axis(ax, result.ppm, zoom, manual_xlim)
        if not y_autoscale and manual_ylim is not None:
            ax.set_ylim(*sorted(manual_ylim))
        ax.legend(frameon=False)
    fig.suptitle(
        format_dataset_plot_title(
            dataset, f"Interactive exploratory view — {result.mode}"
        )
    )
    fig.text(
        0.5,
        0.01,
        f"{acquisition} | p0={result.settings.p0:g}°, p1={result.settings.p1:g}° | "
        f"baseline={result.settings.baseline_method} | Stage-1={result.stage1_area:.3g}, "
        f"fixed={result.fixed_product_area:.3g}, left={result.left_peak_area:.3g}",
        ha="center",
        fontsize=8,
    )
    return fig


def launch_matplotlib_app(explorer: NmrProcessingExplorer):
    """Launch a lightweight standalone app using Matplotlib widgets."""
    from matplotlib.widgets import Button, CheckButtons, RadioButtons, Slider

    fig = plt.figure(figsize=(15, 9))
    plot_ax = fig.add_axes((0.28, 0.16, 0.70, 0.77))
    p0_ax = fig.add_axes((0.30, 0.09, 0.62, 0.025))
    p1_ax = fig.add_axes((0.30, 0.05, 0.62, 0.025))
    method_ax = fig.add_axes((0.02, 0.63, 0.22, 0.20))
    view_ax = fig.add_axes((0.02, 0.34, 0.22, 0.25))
    zoom_ax = fig.add_axes((0.02, 0.12, 0.22, 0.18))
    checks_ax = fig.add_axes((0.02, 0.02, 0.16, 0.08))
    reset_ax = fig.add_axes((0.19, 0.02, 0.07, 0.05))
    p0 = Slider(p0_ax, "p0", -180, 180, valinit=explorer.settings.p0)
    p1 = Slider(p1_ax, "p1", -360, 360, valinit=explorer.settings.p1)
    method = RadioButtons(method_ax, BASELINE_METHODS, active=1)
    display = RadioButtons(view_ax, DISPLAY_MODES, active=4)
    zoom = RadioButtons(zoom_ax, tuple(ZOOM_PRESETS), active=0)
    checks = CheckButtons(checks_ax, ("Stage-1", "Fixed", "Left"), (True, True, True))
    reset = Button(reset_ax, "Reset")

    def redraw(_=None):
        explorer.with_settings(
            p0=p0.val,
            p1=p1.val,
            baseline_method=method.value_selected,
        )
        result = explorer.compute()
        plot_ax.clear()
        mode = display.value_selected
        values = checks.get_status()
        if mode == "before only":
            plot_ax.plot(result.ppm, result.phased_referenced, lw=0.7)
        elif mode == "after only":
            plot_ax.plot(result.ppm, result.corrected, lw=0.7)
        elif mode in {"baseline only", "difference only"}:
            plot_ax.plot(result.ppm, result.baseline, lw=0.8)
        else:
            plot_ax.plot(result.ppm, result.phased_referenced, lw=0.55, label="Before")
            plot_ax.plot(result.ppm, result.corrected, lw=0.7, label="After")
            plot_ax.plot(result.ppm, result.baseline, lw=0.8, label="Baseline")
            plot_ax.legend(frameon=False)
        if values[0] and result.stage1_bounds:
            plot_ax.axvspan(*result.stage1_bounds, alpha=0.12)
        if values[1]:
            plot_ax.axvspan(*TARGET_WINDOW, color="#f58518", alpha=0.08)
        if values[2] and result.left_peak_bounds:
            plot_ax.axvline(result.left_peak_bounds[0], color="#54a24b", ls="--")
            plot_ax.axvline(result.left_peak_bounds[1], color="#54a24b", ls="--")
        _decorate_axis(plot_ax, result.ppm, zoom.value_selected)
        plot_ax.set_title(
            format_dataset_plot_title(
                explorer.dataset_display_name,
                f"{explorer.dx_path.stem} — Mode: {result.mode}",
            )
        )
        fig.canvas.draw_idle()

    def reset_values(_):
        explorer.reset_to_production()
        p0.set_val(explorer.production_settings.p0)
        p1.set_val(explorer.production_settings.p1)
        method.set_active(1)
        redraw()

    p0.on_changed(redraw)
    p1.on_changed(redraw)
    method.on_clicked(redraw)
    display.on_clicked(redraw)
    zoom.on_clicked(redraw)
    checks.on_clicked(redraw)
    reset.on_clicked(reset_values)
    redraw()
    plt.show()


def create_notebook_explorer(
    series_dir: Path = DEFAULT_SERIES,
    exploratory_root: Path = DEFAULT_EXPLORATORY_ROOT,
):
    """Return an ipywidgets explorer with file and processing controls."""
    import ipywidgets as widgets
    from IPython.display import clear_output, display

    files = representative_dx_files(series_dir)
    if not files:
        raise ValueError(f"no representative .dx files under {series_dir}")
    selector = widgets.Dropdown(
        options=[(path.stem, path) for path in files], description="Spectrum"
    )
    p0 = widgets.FloatSlider(description="p0", min=-180, max=180, step=1, value=5)
    p1 = widgets.FloatSlider(description="p1", min=-360, max=360, step=1, value=-10)
    manual_reference = widgets.Checkbox(description="Manual reference override")
    shift = widgets.FloatSlider(description="Δppm", min=-0.5, max=0.5, step=0.001)
    method = widgets.Dropdown(
        options=BASELINE_METHODS, value="production AsLS", description="Baseline"
    )
    lam = widgets.FloatLogSlider(
        description="lambda", base=10, min=3, max=9, value=ALS_LAMBDA
    )
    asymmetry = widgets.FloatLogSlider(
        description="p", base=10, min=-5, max=-0.3, value=ALS_P
    )
    iterations = widgets.IntSlider(
        description="iterations", min=1, max=50, value=ALS_ITERATIONS
    )
    degree = widgets.IntSlider(description="poly degree", min=0, max=6, value=2)
    display_mode = widgets.Dropdown(
        options=DISPLAY_MODES, value="overlay", description="Display"
    )
    zoom = widgets.Dropdown(
        options=tuple(ZOOM_PRESETS), value="full spectrum", description="Zoom"
    )
    manual_x = widgets.Checkbox(value=False, description="Manual x limits")
    x_low = widgets.FloatText(value=5.60, description="x low")
    x_high = widgets.FloatText(value=6.00, description="x high")
    y_autoscale = widgets.Checkbox(value=True, description="Y autoscale")
    y_low = widgets.FloatText(value=-250.0, description="y low")
    y_high = widgets.FloatText(value=500.0, description="y high")
    show_baseline = widgets.Checkbox(value=True, description="Show baseline")
    show_stage1 = widgets.Checkbox(value=True, description="Stage-1 bounds")
    show_fixed = widgets.Checkbox(value=True, description="Fixed bounds")
    show_left = widgets.Checkbox(value=True, description="Left-line bounds")
    reset = widgets.Button(description="Reset to production", button_style="primary")
    export_png = widgets.Button(description="Export PNG")
    export_json = widgets.Button(description="Export settings")
    status = widgets.HTML()
    output = widgets.Output()
    state = {"explorer": None}

    def load_explorer():
        explorer = NmrProcessingExplorer(
            selector.value, exploratory_root=exploratory_root
        )
        state["explorer"] = explorer
        return explorer

    def update(_=None):
        explorer = state["explorer"]
        if explorer is None or explorer.dx_path != Path(selector.value).resolve():
            explorer = load_explorer()
        explorer.with_settings(
            p0=p0.value,
            p1=p1.value,
            use_production_reference=not manual_reference.value,
            manual_shift_ppm=shift.value,
            baseline_method=method.value,
            baseline_lambda=lam.value,
            baseline_asymmetry=asymmetry.value,
            baseline_iterations=iterations.value,
            polynomial_degree=degree.value,
        )
        result = explorer.compute()
        status.value = (
            f"<b>Mode: {result.mode}</b> — production p0={explorer.production_settings.p0:g}°, "
            f"p1={explorer.production_settings.p1:g}°, reference shift=0.000 ppm, "
            f"AsLS λ={ALS_LAMBDA:.0e}, p={ALS_P:g}, iterations={ALS_ITERATIONS}. "
            f"Current: target={result.target_ppm:.4f} ppm; Stage-1={result.stage1_area:.3g}; "
            f"fixed={result.fixed_product_area:.3g}; left={result.left_peak_area:.3g}; "
            f"starting={result.starting_material_area:.3g}; silane={result.silane_area:.3g}."
        )
        with output:
            clear_output(wait=True)
            fig = plot_result(
                result,
                dataset=explorer.dataset_display_name,
                acquisition=explorer.dx_path.name,
                display_mode=display_mode.value,
                zoom=zoom.value,
                manual_xlim=(x_low.value, x_high.value) if manual_x.value else None,
                y_autoscale=y_autoscale.value,
                manual_ylim=(y_low.value, y_high.value),
                show_baseline=show_baseline.value,
                show_stage1=show_stage1.value,
                show_fixed=show_fixed.value,
                show_left=show_left.value,
            )
            display(fig)
            plt.close(fig)

    def reset_values(_):
        explorer = state["explorer"] or load_explorer()
        production = explorer.reset_to_production()
        p0.value = production.p0
        p1.value = production.p1
        manual_reference.value = False
        shift.value = 0.0
        method.value = production.baseline_method
        lam.value = production.baseline_lambda
        asymmetry.value = production.baseline_asymmetry
        iterations.value = production.baseline_iterations
        degree.value = production.polynomial_degree
        update()

    def save_png(_):
        explorer = state["explorer"] or load_explorer()
        path = explorer.export_plot(display_mode=display_mode.value, zoom=zoom.value)
        status.value += f"<br>Exported exploratory PNG: <code>{path}</code>"

    def save_json(_):
        explorer = state["explorer"] or load_explorer()
        path = explorer.export_settings()
        status.value += f"<br>Exported exploratory settings: <code>{path}</code>"

    controls = [
        selector,
        p0,
        p1,
        manual_reference,
        shift,
        method,
        lam,
        asymmetry,
        iterations,
        degree,
        display_mode,
        zoom,
        manual_x,
        x_low,
        x_high,
        y_autoscale,
        y_low,
        y_high,
        show_baseline,
        show_stage1,
        show_fixed,
        show_left,
    ]
    for control in controls:
        control.observe(update, names="value")
    reset.on_click(reset_values)
    export_png.on_click(save_png)
    export_json.on_click(save_json)
    load_explorer()
    reset_values(None)
    ui = widgets.VBox(
        [
            widgets.HTML(
                "<h3>Exploratory NMR processing viewer</h3>"
                "<p>This tool never writes production results. Exports are explicitly marked exploratory.</p>"
            ),
            status,
            widgets.HBox(
                [
                    widgets.VBox(controls[:10]),
                    widgets.VBox(controls[10:] + [reset, export_png, export_json]),
                ]
            ),
            output,
        ]
    )
    return ui


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spectrum",
        nargs="?",
        type=Path,
        default=representative_dx_files()[0],
        help="JCAMP-DX file to explore",
    )
    parser.add_argument("--export-root", type=Path, default=DEFAULT_EXPLORATORY_ROOT)
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    explorer = NmrProcessingExplorer(
        args.spectrum,
        exploratory_root=args.export_root,
    )
    launch_matplotlib_app(explorer)


if __name__ == "__main__":
    main()
