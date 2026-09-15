"""Lightweight desktop NMR processing demo V2 using audited repo helpers."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import nmrglue as ng
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from chemyx_lab.analysis.nmr import (  # noqa: E402
    asymmetric_least_squares_baseline,
    build_processing_inspection,
    integrate_above_local_baseline,
    pick_spectrum_region,
)
from chemyx_lab.analysis.plot_titles import resolve_dataset_display_name  # noqa: E402
from _common import parse_acquisition_timestamp  # noqa: E402
from inspect_processing import (  # noqa: E402
    ALS_ITERATIONS,
    ALS_LAMBDA,
    ALS_P,
    FFT_POINTS,
    LB_HZ,
    REGION,
    TARGET_WINDOW,
    _observe_frequency,
)

DEFAULT_DX = (
    REPO_ROOT
    / "results/runs/automated/chemyx_demo_081026_v3"
    / "20260810_171441_si6/raw_nmr"
    / "20260810_171806_081626_phsi4_0001_8scan_gain12.dx"
)
DEFAULT_EXPORT_ROOT = REPO_ROOT / "results/nmr_phase_demo_exports"
DEFAULT_INTEGRATION = TARGET_WINDOW


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


@dataclass(frozen=True)
class ProcessingSettings:
    p0_deg: float
    p1_deg: float
    pivot_ppm: float
    reference_shift_ppm: float = 0.0
    baseline_enabled: bool = True
    baseline_smoothness: float = ALS_LAMBDA
    integration_left_ppm: float = DEFAULT_INTEGRATION[0]
    integration_right_ppm: float = DEFAULT_INTEGRATION[1]


@dataclass(frozen=True)
class ProcessedSpectrum:
    ppm: np.ndarray
    phased: np.ndarray
    baseline: np.ndarray
    intensity: np.ndarray


class Phase2Model:
    """Cache reconstruction/FFT and expose only the V2 exploratory operations."""

    def __init__(self, path: Path, export_root: Path = DEFAULT_EXPORT_ROOT):
        self.export_root = Path(export_root).resolve()
        if not _inside(self.export_root, DEFAULT_EXPORT_ROOT):
            raise ValueError(f"exports must stay inside {DEFAULT_EXPORT_ROOT}")
        self._processed_cache: dict[tuple, ProcessedSpectrum] = {}
        self.load(path)

    def load(self, path: Path) -> None:
        path = Path(path).resolve()
        if path.suffix.lower() != ".dx" or not path.is_file():
            raise ValueError(f"Expected an existing .dx file: {path}")
        inspection = build_processing_inspection(
            path,
            line_broadening_hz=LB_HZ,
            zero_fill_points=FFT_POINTS,
            inverse_phase=True,
            als_smoothness=ALS_LAMBDA,
            als_asymmetry=ALS_P,
            als_iterations=ALS_ITERATIONS,
        )
        self.path = path
        self.inspection = inspection
        self.base_ppm = np.asarray(inspection.ppm_axis, dtype=float)
        self.fft_spectrum = np.asarray(inspection.fft_spectrum, dtype=np.complex128)
        self.production_phased = np.real(inspection.phased_spectrum)
        self.dataset_display_name = resolve_dataset_display_name(input_paths=path)
        self.production_settings = ProcessingSettings(
            p0_deg=float(inspection.phase0_deg),
            p1_deg=float(inspection.phase1_deg),
            pivot_ppm=float(self.base_ppm[0]),
        )
        timestamp, source = parse_acquisition_timestamp(inspection.metadata, path)
        if source != "LONG DATE header":
            timestamp, source = None, "unavailable (no JCAMP LONG DATE)"
        self.acquisition_timestamp = timestamp
        self.timestamp_source = source
        self._processed_cache.clear()

    def process(self, settings: ProcessingSettings) -> ProcessedSpectrum:
        key = (
            round(settings.p0_deg, 6),
            round(settings.p1_deg, 6),
            round(settings.pivot_ppm, 8),
            round(settings.reference_shift_ppm, 8),
            settings.baseline_enabled,
            round(settings.baseline_smoothness, 3),
        )
        cached = self._processed_cache.get(key)
        if cached is not None:
            return cached
        fractions = np.linspace(0.0, 1.0, self.base_ppm.size)
        order = np.argsort(self.base_ppm)
        pivot_fraction = float(
            np.interp(settings.pivot_ppm, self.base_ppm[order], fractions[order])
        )
        effective_p0 = settings.p0_deg - settings.p1_deg * pivot_fraction
        phased = np.real(
            ng.proc_base.ps(
                self.fft_spectrum,
                p0=effective_p0,
                p1=settings.p1_deg,
                inv=True,
            )
        )
        if settings.baseline_enabled:
            baseline = asymmetric_least_squares_baseline(
                phased,
                smoothness=settings.baseline_smoothness,
                asymmetry=ALS_P,
                iterations=ALS_ITERATIONS,
            )
            intensity = phased - baseline
        else:
            baseline = np.zeros_like(phased)
            intensity = phased
        result = ProcessedSpectrum(
            ppm=self.base_ppm + settings.reference_shift_ppm,
            phased=phased,
            baseline=np.asarray(baseline),
            intensity=np.asarray(intensity),
        )
        if len(self._processed_cache) > 12:
            self._processed_cache.clear()
        self._processed_cache[key] = result
        return result

    def manual_area(self, result: ProcessedSpectrum, settings: ProcessingSettings) -> float:
        integral = integrate_above_local_baseline(
            result.ppm,
            result.intensity,
            left_ppm=settings.integration_left_ppm,
            right_ppm=settings.integration_right_ppm,
        )
        return float(integral.positive_area)

    def _unique_path(self, suffix: str, *, directory: bool = False) -> Path:
        self.export_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        candidate = self.export_root / f"{self.path.stem}_{suffix}_{stamp}"
        if not _inside(candidate, self.export_root):
            raise ValueError("exploratory output escaped its allowed directory")
        if directory:
            candidate.mkdir()
            return candidate
        return candidate

    def settings_payload(self, settings: ProcessingSettings) -> dict:
        left, right = sorted(
            (settings.integration_left_ppm, settings.integration_right_ppm)
        )
        return {
            "exploratory_only": True,
            "source_file": str(self.path),
            "dataset_display_name": self.dataset_display_name,
            "acquisition_timestamp": self.acquisition_timestamp.isoformat()
            if self.acquisition_timestamp
            else None,
            "timestamp_source": self.timestamp_source,
            "phase": {
                "p0_deg": settings.p0_deg,
                "p1_deg": settings.p1_deg,
                "pivot_ppm": settings.pivot_ppm,
                "inv": True,
            },
            "reference": {"shift_ppm": settings.reference_shift_ppm},
            "baseline": {
                "enabled": settings.baseline_enabled,
                "method": "asymmetric_least_squares",
                "smoothness_lambda": settings.baseline_smoothness,
                "asymmetry": ALS_P,
                "iterations": ALS_ITERATIONS,
            },
            "integration": {"left_ppm": left, "right_ppm": right},
        }

    def save_settings(self, settings: ProcessingSettings) -> Path:
        path = self._unique_path("phase2_settings").with_suffix(".json")
        path.write_text(
            json.dumps(self.settings_payload(settings), indent=2), encoding="utf-8"
        )
        return path

    def export_spectrum(self, settings: ProcessingSettings) -> Path:
        result = self.process(settings)
        path = self._unique_path("phase2_spectrum").with_suffix(".csv")
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(("ppm", "intensity"))
            writer.writerows(zip(result.ppm, result.intensity))
        return path

    def run_analysis(self, settings: ProcessingSettings) -> tuple[Path, dict]:
        result = self.process(settings)
        shift = settings.reference_shift_ppm
        picked = pick_spectrum_region(
            result.ppm,
            result.intensity,
            region_min_ppm=REGION[0] + shift,
            region_max_ppm=REGION[1] + shift,
            min_prominence_snr=5.0,
            min_distance_ppm=0.04,
            min_width_ppm=0.015,
            baseline_polynomial_order=3,
            smoothing_window_ppm=0.006,
            quantitative_intensity=result.intensity,
            source=self.path,
        )
        frequency = _observe_frequency(self.inspection.metadata)
        candidates = [
            peak
            for peak in picked.peaks
            if TARGET_WINDOW[0] + shift
            <= peak.interpolated_ppm
            <= TARGET_WINDOW[1] + shift
            and peak.snr >= 3.0
            and peak.prominence_snr >= 3.0
            and 1.0 <= peak.width_ppm * frequency <= 10.0
            and peak.positive_area > 0
        ]
        target = max(candidates, key=lambda peak: peak.snr) if candidates else None
        fixed = integrate_above_local_baseline(
            result.ppm,
            result.intensity,
            left_ppm=TARGET_WINDOW[0] + shift,
            right_ppm=TARGET_WINDOW[1] + shift,
        )
        manual = self.manual_area(result, settings)
        summary = {
            "exploratory_only": True,
            "source_file": str(self.path),
            "peak_ppm": target.interpolated_ppm if target else None,
            "height": target.peak_height if target else None,
            "snr": target.snr if target else None,
            "linewidth_hz": target.width_ppm * frequency if target else None,
            "stage1_area": target.positive_area if target else None,
            "fixed_window_area": fixed.positive_area,
            "manual_integration_area": manual,
        }
        output = self._unique_path("analysis", directory=True)
        (output / "settings.json").write_text(
            json.dumps(self.settings_payload(settings), indent=2), encoding="utf-8"
        )
        (output / "summary.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        with (output / "peaks_simple.csv").open(
            "w", newline="", encoding="utf-8"
        ) as handle:
            fields = [
                "peak_ppm",
                "height",
                "snr",
                "linewidth_hz",
                "stage1_area",
                "fixed_window_area",
                "manual_integration_area",
            ]
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerow({field: summary[field] for field in fields})
        return output, summary


class Phase2Window(QtWidgets.QMainWindow):
    def __init__(self, path: Path = DEFAULT_DX):
        super().__init__()
        self.setWindowTitle("NMR Processing Demo V2 — exploratory")
        self.resize(1420, 920)
        self.model = Phase2Model(path)
        self._result: ProcessedSpectrum | None = None
        self._build_ui()
        self._update_timer = QtCore.QTimer(self)
        self._update_timer.setSingleShot(True)
        self._update_timer.setInterval(90)
        self._update_timer.timeout.connect(self._update_plot)
        self._load_model()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        title = QtWidgets.QLabel("NMR Processing Demo V2")
        title.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(title)
        file_row = QtWidgets.QHBoxLayout()
        file_row.addWidget(QtWidgets.QLabel("File"))
        self.file_label = QtWidgets.QLineEdit(readOnly=True)
        file_row.addWidget(self.file_label, 1)
        open_button = QtWidgets.QPushButton("Open .DX file")
        open_button.clicked.connect(self._open_file)
        file_row.addWidget(open_button)
        layout.addLayout(file_row)

        view_row = QtWidgets.QHBoxLayout()
        for label, bounds in (
            ("Full spectrum", None),
            ("Product region", (5.60, 6.00)),
            ("Reference region", (4.70, 5.30)),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, selected=bounds: self._set_view(selected)
            )
            view_row.addWidget(button)
        self.reference_trace = QtWidgets.QCheckBox("Show production reference trace")
        self.reference_trace.toggled.connect(self._schedule_update)
        view_row.addWidget(self.reference_trace)
        view_row.addStretch(1)
        layout.addLayout(view_row)

        groups = QtWidgets.QHBoxLayout()
        groups.addWidget(self._phase_group(), 3)
        groups.addWidget(self._baseline_group(), 2)
        groups.addWidget(self._reference_group(), 1)
        groups.addWidget(self._integration_group(), 2)
        layout.addLayout(groups)

        command_row = QtWidgets.QHBoxLayout()
        reset_all = QtWidgets.QPushButton("Reset all to production")
        reset_all.clicked.connect(self._reset_all)
        command_row.addWidget(reset_all)
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setStyleSheet("font-weight: 600;")
        command_row.addWidget(self.status, 1)
        layout.addLayout(command_row)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        self.plot.showGrid(x=True, y=True, alpha=0.18)
        self.plot.setLabel("bottom", "Chemical shift", units="ppm")
        self.plot.setLabel("left", "Intensity", units="a.u.")
        self.plot.getPlotItem().invertX(True)
        self.current_curve = self.plot.plot(
            pen=pg.mkPen("#1f77b4", width=1.2), autoDownsample=True, clipToView=True
        )
        self.production_curve = self.plot.plot(
            pen=pg.mkPen("#d95f02", width=1.0, style=QtCore.Qt.DashLine),
            autoDownsample=True,
            clipToView=True,
        )
        self.integration_region = pg.LinearRegionItem(
            values=DEFAULT_INTEGRATION,
            brush=pg.mkBrush(44, 160, 44, 45),
            pen=pg.mkPen("#2ca02c", width=1.2),
            movable=True,
        )
        self.integration_region.setZValue(10)
        self.integration_region.sigRegionChanged.connect(self._region_changed)
        self.plot.addItem(self.integration_region)
        layout.addWidget(self.plot, 1)

        action_row = QtWidgets.QHBoxLayout()
        save = QtWidgets.QPushButton("Save processing settings")
        save.clicked.connect(self._save_settings)
        action_row.addWidget(save)
        export = QtWidgets.QPushButton("Export current spectrum CSV")
        export.clicked.connect(self._export_spectrum)
        action_row.addWidget(export)
        analyze = QtWidgets.QPushButton("Run analysis with current settings")
        analyze.clicked.connect(self._confirm_analysis)
        action_row.addWidget(analyze)
        action_row.addStretch(1)
        layout.addLayout(action_row)
        self.result_panel = QtWidgets.QPlainTextEdit()
        self.result_panel.setReadOnly(True)
        self.result_panel.setMaximumHeight(108)
        self.result_panel.setPlaceholderText("Exploratory analysis summary appears here.")
        layout.addWidget(self.result_panel)

    def _phase_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("PHASE")
        grid = QtWidgets.QGridLayout(box)
        self.p0_slider, self.p0_value = self._slider_row(grid, 0, "P0", -1800, 1800)
        self.p1_slider, self.p1_value = self._slider_row(grid, 1, "P1", -1800, 1800)
        grid.addWidget(QtWidgets.QLabel("Pivot ppm"), 2, 0)
        self.pivot = QtWidgets.QDoubleSpinBox(decimals=6, minimum=-100, maximum=100)
        self.pivot.setSingleStep(0.01)
        self.pivot.valueChanged.connect(self._schedule_update)
        grid.addWidget(self.pivot, 2, 1)
        button = QtWidgets.QPushButton("Reset phase")
        button.clicked.connect(self._reset_phase)
        grid.addWidget(button, 3, 0, 1, 3)
        return box

    def _baseline_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("BASELINE")
        layout = QtWidgets.QVBoxLayout(box)
        self.baseline_enabled = QtWidgets.QCheckBox("Apply production baseline correction")
        self.baseline_enabled.toggled.connect(self._schedule_update)
        layout.addWidget(self.baseline_enabled)
        layout.addWidget(QtWidgets.QLabel("Baseline smoothness"))
        self.smoothness = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.smoothness.setRange(40, 80)
        self.smoothness.valueChanged.connect(self._schedule_update)
        layout.addWidget(self.smoothness)
        self.smoothness_value = QtWidgets.QLabel()
        layout.addWidget(self.smoothness_value)
        button = QtWidgets.QPushButton("Reset baseline")
        button.clicked.connect(self._reset_baseline)
        layout.addWidget(button)
        return box

    def _reference_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("REFERENCE")
        layout = QtWidgets.QVBoxLayout(box)
        layout.addWidget(QtWidgets.QLabel("Shift (ppm)"))
        self.reference_shift = QtWidgets.QDoubleSpinBox(
            decimals=4, minimum=-1.0, maximum=1.0
        )
        self.reference_shift.setSingleStep(0.001)
        self.reference_shift.valueChanged.connect(self._schedule_update)
        layout.addWidget(self.reference_shift)
        layout.addWidget(QtWidgets.QLabel("Production: 0.0000 ppm"))
        button = QtWidgets.QPushButton("Reset reference")
        button.clicked.connect(self._reset_reference)
        layout.addWidget(button)
        return box

    def _integration_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("INTEGRATION")
        layout = QtWidgets.QVBoxLayout(box)
        layout.addWidget(QtWidgets.QLabel("Drag the green boundaries on the plot."))
        self.area_label = QtWidgets.QLabel("Integration area: —")
        self.area_label.setStyleSheet("font-size: 15px; font-weight: 600;")
        layout.addWidget(self.area_label)
        self.region_label = QtWidgets.QLabel()
        layout.addWidget(self.region_label)
        button = QtWidgets.QPushButton("Reset integration region")
        button.clicked.connect(self._reset_region)
        layout.addWidget(button)
        return box

    def _slider_row(self, layout, row, label, minimum, maximum):
        layout.addWidget(QtWidgets.QLabel(label), row, 0)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(minimum, maximum)
        slider.valueChanged.connect(self._schedule_update)
        layout.addWidget(slider, row, 1)
        value = QtWidgets.QLabel()
        value.setMinimumWidth(58)
        layout.addWidget(value, row, 2)
        return slider, value

    def _load_model(self) -> None:
        self.file_label.setText(str(self.model.path))
        self.production_curve.setData(
            self.model.base_ppm,
            np.asarray(self.model.inspection.corrected_real, dtype=float),
        )
        self._reset_all()
        self._set_view(None)

    def _settings(self) -> ProcessingSettings:
        left, right = sorted(self.integration_region.getRegion())
        return ProcessingSettings(
            p0_deg=self.p0_slider.value() / 10.0,
            p1_deg=self.p1_slider.value() / 10.0,
            pivot_ppm=self.pivot.value(),
            reference_shift_ppm=self.reference_shift.value(),
            baseline_enabled=self.baseline_enabled.isChecked(),
            baseline_smoothness=10 ** (self.smoothness.value() / 10.0),
            integration_left_ppm=float(left),
            integration_right_ppm=float(right),
        )

    def _schedule_update(self, _value=None) -> None:
        if hasattr(self, "_update_timer"):
            self._update_timer.start()

    def _update_plot(self) -> None:
        settings = self._settings()
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            self._result = self.model.process(settings)
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
        self.current_curve.setData(self._result.ppm, self._result.intensity)
        self.production_curve.setVisible(self.reference_trace.isChecked())
        self.p0_value.setText(f"{settings.p0_deg:.1f}°")
        self.p1_value.setText(f"{settings.p1_deg:.1f}°")
        self.smoothness_value.setText(f"λ = {settings.baseline_smoothness:.1e}")
        self.plot.setTitle(f"{self.model.dataset_display_name} — {self.model.path.name}")
        self._update_area()
        self._update_status(settings)

    def _region_changed(self) -> None:
        self._update_area()
        self._update_status(self._settings())

    def _update_area(self) -> None:
        if self._result is None:
            return
        settings = self._settings()
        self.region_label.setText(
            f"{settings.integration_left_ppm:.4f} to "
            f"{settings.integration_right_ppm:.4f} ppm"
        )
        try:
            area = self.model.manual_area(self._result, settings)
            self.area_label.setText(f"Integration area: {area:.4f}")
        except Exception:
            self.area_label.setText("Integration area: region too narrow")

    def _update_status(self, settings: ProcessingSettings) -> None:
        production = self.model.production_settings
        changes = []
        comparisons = (
            (settings.p0_deg, production.p0_deg, "P0", ".1f", "°"),
            (settings.p1_deg, production.p1_deg, "P1", ".1f", "°"),
            (settings.pivot_ppm, production.pivot_ppm, "pivot", ".4f", " ppm"),
            (settings.reference_shift_ppm, 0.0, "reference", ".4f", " ppm"),
        )
        for current, default, label, fmt, unit in comparisons:
            if not np.isclose(current, default, atol=1e-5):
                changes.append(
                    f"{label} {format(default, fmt)}→{format(current, fmt)}{unit}"
                )
        if settings.baseline_enabled is not True:
            changes.append("baseline on→off")
        if not np.isclose(settings.baseline_smoothness, ALS_LAMBDA):
            changes.append(f"baseline λ {ALS_LAMBDA:.0e}→{settings.baseline_smoothness:.0e}")
        if not (
            np.isclose(settings.integration_left_ppm, DEFAULT_INTEGRATION[0])
            and np.isclose(settings.integration_right_ppm, DEFAULT_INTEGRATION[1])
        ):
            changes.append("integration region changed")
        if changes:
            self.status.setText("EXPLORATORY SETTINGS | " + "; ".join(changes))
            self.status.setStyleSheet("font-weight: 600; color: #a64b00;")
        else:
            self.status.setText("PRODUCTION SETTINGS")
            self.status.setStyleSheet("font-weight: 600; color: #177245;")

    def _reset_phase(self) -> None:
        p = self.model.production_settings
        self.p0_slider.setValue(round(p.p0_deg * 10))
        self.p1_slider.setValue(round(p.p1_deg * 10))
        self.pivot.setValue(p.pivot_ppm)
        self._schedule_update()

    def _reset_baseline(self) -> None:
        self.baseline_enabled.setChecked(True)
        self.smoothness.setValue(round(np.log10(ALS_LAMBDA) * 10))
        self._schedule_update()

    def _reset_reference(self) -> None:
        self.reference_shift.setValue(0.0)
        self._schedule_update()

    def _reset_region(self) -> None:
        self.integration_region.setRegion(DEFAULT_INTEGRATION)
        self._region_changed()

    def _reset_all(self) -> None:
        widgets = [
            self.p0_slider,
            self.p1_slider,
            self.pivot,
            self.baseline_enabled,
            self.smoothness,
            self.reference_shift,
            self.integration_region,
        ]
        for widget in widgets:
            widget.blockSignals(True)
        p = self.model.production_settings
        self.p0_slider.setValue(round(p.p0_deg * 10))
        self.p1_slider.setValue(round(p.p1_deg * 10))
        self.pivot.setValue(p.pivot_ppm)
        self.baseline_enabled.setChecked(True)
        self.smoothness.setValue(round(np.log10(ALS_LAMBDA) * 10))
        self.reference_shift.setValue(0.0)
        self.integration_region.setRegion(DEFAULT_INTEGRATION)
        for widget in widgets:
            widget.blockSignals(False)
        self._update_plot()

    def _set_view(self, bounds) -> None:
        ppm = self._result.ppm if self._result is not None else self.model.base_ppm
        if bounds is None:
            low, high = float(np.min(ppm)), float(np.max(ppm))
        else:
            low, high = sorted(bounds)
        self.plot.setXRange(low, high, padding=0.02)
        if self._result is not None:
            mask = (ppm >= low) & (ppm <= high)
            visible = self._result.intensity[mask]
            if visible.size:
                bottom, top = np.percentile(visible, (0.5, 99.5))
                padding = max(0.08 * float(top - bottom), 1.0)
                self.plot.setYRange(float(bottom - padding), float(top + padding))

    def _open_file(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open NMReady JCAMP-DX file",
            str(self.model.path.parent),
            "JCAMP-DX files (*.dx);;All files (*)",
        )
        if not filename:
            return
        try:
            self.model.load(Path(filename))
            self._load_model()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not open DX file", str(exc))

    def _save_settings(self) -> None:
        try:
            path = self.model.save_settings(self._settings())
            self.result_panel.setPlainText(f"Saved exploratory settings:\n{path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not save settings", str(exc))

    def _export_spectrum(self) -> None:
        try:
            path = self.model.export_spectrum(self._settings())
            self.result_panel.setPlainText(f"Exported current spectrum:\n{path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not export spectrum", str(exc))

    def _confirm_analysis(self) -> None:
        p = self.model.production_settings
        current = self._settings()
        text = (
            f"Production: P0={p.p0_deg:.1f}°, P1={p.p1_deg:.1f}°\n"
            f"Current: P0={current.p0_deg:.1f}°, P1={current.p1_deg:.1f}°\n"
            f"Reference: 0.0000 → {current.reference_shift_ppm:.4f} ppm\n\n"
            "Run exploratory analysis using these settings?"
        )
        answer = QtWidgets.QMessageBox.question(
            self,
            "Confirm exploratory analysis",
            text,
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self._run_analysis()

    def _run_analysis(self) -> None:
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        try:
            output, result = self.model.run_analysis(self._settings())
            def show(value, pattern):
                return "not detected" if value is None else format(value, pattern)
            self.result_panel.setPlainText(
                f"Peak ppm: {show(result['peak_ppm'], '.4f')}    "
                f"Height: {show(result['height'], '.2f')}    "
                f"SNR: {show(result['snr'], '.2f')}\n"
                f"Linewidth: {show(result['linewidth_hz'], '.2f')} Hz    "
                f"Stage-1 area: {show(result['stage1_area'], '.4f')}    "
                f"Fixed-window area: {result['fixed_window_area']:.4f}    "
                f"Manual integration: {result['manual_integration_area']:.4f}\n"
                f"Saved to: {output}"
            )
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Analysis failed", str(exc))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spectrum", nargs="?", type=Path, default=DEFAULT_DX)
    parser.add_argument(
        "--smoke-screenshot",
        type=Path,
        help="Render one offscreen screenshot and exit (focused smoke testing).",
    )
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    window = Phase2Window(args.spectrum)
    window.show()
    if args.smoke_screenshot:
        def capture() -> None:
            image = window.grab()
            args.smoke_screenshot.parent.mkdir(parents=True, exist_ok=True)
            image.save(str(args.smoke_screenshot))
            app.quit()
        QtCore.QTimer.singleShot(800, capture)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
