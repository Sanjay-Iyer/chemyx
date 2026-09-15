"""Lightweight PySide6/pyqtgraph NMR phase explorer using real JCAMP-DX data."""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import nmrglue as ng
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from chemyx_lab.analysis.nmr import build_processing_inspection  # noqa: E402
from chemyx_lab.analysis.plot_titles import (  # noqa: E402
    resolve_dataset_display_name,
)

DEFAULT_DX = (
    REPO_ROOT
    / "results/runs/automated/chemyx_demo_081026_v3"
    / "20260810_171441_si6/raw_nmr"
    / "20260810_171806_081626_phsi4_0001_8scan_gain12.dx"
)


@dataclass
class PhaseSpectrumModel:
    """Cache the audited FFT once and apply interactive phase in memory."""

    path: Path

    def __post_init__(self):
        self.load(self.path)

    def load(self, path: Path):
        path = Path(path).resolve()
        if path.suffix.lower() != ".dx" or not path.is_file():
            raise ValueError(f"Expected an existing .dx file: {path}")
        inspection = build_processing_inspection(
            path,
            line_broadening_hz=0.03,
            zero_fill_points=65536,
            inverse_phase=True,
        )
        self.path = path
        self.ppm = np.asarray(inspection.ppm_axis, dtype=float)
        self.fft_spectrum = np.asarray(inspection.fft_spectrum, dtype=np.complex128)
        self.production_spectrum = np.real(inspection.phased_spectrum)
        self.production_p0 = float(inspection.phase0_deg)
        self.production_p1 = float(inspection.phase1_deg)
        # nmrglue's p1 convention starts at array index zero.  Using that ppm
        # as the default pivot reproduces the production phase exactly.
        self.production_pivot_ppm = float(self.ppm[0])
        self.dataset_display_name = resolve_dataset_display_name(input_paths=path)

    def phased(self, p0: float, p1: float, pivot_ppm: float) -> np.ndarray:
        fractions = np.linspace(0.0, 1.0, self.ppm.size)
        pivot_fraction = float(np.interp(pivot_ppm, self.ppm, fractions))
        effective_p0 = float(p0) - float(p1) * pivot_fraction
        return np.real(
            ng.proc_base.ps(
                self.fft_spectrum,
                p0=effective_p0,
                p1=float(p1),
                inv=True,
            )
        )


class PhaseDemoWindow(QtWidgets.QMainWindow):
    def __init__(self, path: Path = DEFAULT_DX):
        super().__init__()
        self.setWindowTitle("NMR Phase Explorer — exploratory demo")
        self.resize(1250, 800)
        self.model = PhaseSpectrumModel(path)
        self._build_ui()
        self._load_model_into_controls()

    def _build_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        heading = QtWidgets.QLabel("NMR Phase Explorer")
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)

        file_row = QtWidgets.QHBoxLayout()
        file_row.addWidget(QtWidgets.QLabel("File:"))
        self.file_label = QtWidgets.QLineEdit()
        self.file_label.setReadOnly(True)
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
        self.reference_checkbox = QtWidgets.QCheckBox("Show production reference trace")
        self.reference_checkbox.toggled.connect(self._update_plot)
        view_row.addWidget(self.reference_checkbox)
        view_row.addStretch(1)
        layout.addLayout(view_row)

        controls = QtWidgets.QGridLayout()
        self.p0_slider, self.p0_value = self._phase_control(controls, 0, "Phase 0")
        self.p1_slider, self.p1_value = self._phase_control(controls, 1, "Phase 1")
        controls.addWidget(QtWidgets.QLabel("Pivot ppm"), 2, 0)
        self.pivot = QtWidgets.QDoubleSpinBox()
        self.pivot.setDecimals(4)
        self.pivot.setRange(-100.0, 100.0)
        self.pivot.setSingleStep(0.01)
        self.pivot.valueChanged.connect(self._update_plot)
        controls.addWidget(self.pivot, 2, 1)
        self.pivot_note = QtWidgets.QLabel(
            "P1 is referenced around this ppm; the production default is nmrglue array index 0."
        )
        controls.addWidget(self.pivot_note, 2, 2)
        reset = QtWidgets.QPushButton("Reset phase to production")
        reset.clicked.connect(self._reset_production)
        controls.addWidget(reset, 3, 1)
        layout.addLayout(controls)

        self.plot = pg.PlotWidget()
        self.plot.setBackground("w")
        self.plot.showGrid(x=True, y=True, alpha=0.18)
        self.plot.setLabel("bottom", "Chemical shift", units="ppm")
        self.plot.setLabel("left", "Intensity", units="a.u.")
        self.plot.getPlotItem().invertX(True)
        self.current_curve = self.plot.plot(
            pen=pg.mkPen("#1f77b4", width=1.2),
            autoDownsample=True,
            clipToView=True,
        )
        self.production_curve = self.plot.plot(
            pen=pg.mkPen("#d95f02", width=1.0, style=QtCore.Qt.DashLine),
            autoDownsample=True,
            clipToView=True,
        )
        layout.addWidget(self.plot, 1)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _phase_control(self, layout, row: int, label: str):
        layout.addWidget(QtWidgets.QLabel(label), row, 0)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(-1800, 1800)
        slider.setSingleStep(1)
        slider.valueChanged.connect(self._update_plot)
        layout.addWidget(slider, row, 1)
        value = QtWidgets.QLabel()
        value.setMinimumWidth(95)
        layout.addWidget(value, row, 2)
        return slider, value

    def _load_model_into_controls(self):
        self.file_label.setText(str(self.model.path))
        self.p0_slider.blockSignals(True)
        self.p1_slider.blockSignals(True)
        self.pivot.blockSignals(True)
        self.p0_slider.setValue(round(self.model.production_p0 * 10))
        self.p1_slider.setValue(round(self.model.production_p1 * 10))
        self.pivot.setValue(self.model.production_pivot_ppm)
        self.p0_slider.blockSignals(False)
        self.p1_slider.blockSignals(False)
        self.pivot.blockSignals(False)
        self.production_curve.setData(self.model.ppm, self.model.production_spectrum)
        self._update_plot()
        self._set_view(None)

    def _open_file(self):
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
            self._load_model_into_controls()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not open DX file", str(exc))

    def _current_values(self):
        return (
            self.p0_slider.value() / 10.0,
            self.p1_slider.value() / 10.0,
            self.pivot.value(),
        )

    def _update_plot(self, _value=None):
        p0, p1, pivot = self._current_values()
        spectrum = self.model.phased(p0, p1, pivot)
        self.current_curve.setData(self.model.ppm, spectrum)
        self.production_curve.setVisible(self.reference_checkbox.isChecked())
        self.p0_value.setText(f"{p0:.1f}°")
        self.p1_value.setText(f"{p1:.1f}°")
        production = (
            np.isclose(p0, self.model.production_p0)
            and np.isclose(p1, self.model.production_p1)
            and np.isclose(pivot, self.model.production_pivot_ppm)
        )
        mode = "PRODUCTION SETTINGS" if production else "EXPLORATORY SETTINGS"
        self.status.setText(
            f"{mode} | Production P0={self.model.production_p0:.1f}°, "
            f"P1={self.model.production_p1:.1f}° | Current P0={p0:.1f}°, "
            f"P1={p1:.1f}°, pivot={pivot:.4f} ppm | "
            "No production files are written."
        )
        self.plot.setTitle(
            f"{self.model.dataset_display_name} — {self.model.path.name}"
        )

    def _reset_production(self):
        self.p0_slider.setValue(round(self.model.production_p0 * 10))
        self.p1_slider.setValue(round(self.model.production_p1 * 10))
        self.pivot.setValue(self.model.production_pivot_ppm)
        self._update_plot()

    def _set_view(self, bounds):
        if bounds is None:
            low, high = float(np.min(self.model.ppm)), float(np.max(self.model.ppm))
        else:
            low, high = sorted(bounds)
        self.plot.setXRange(low, high, padding=0.02)
        mask = (self.model.ppm >= low) & (self.model.ppm <= high)
        p0, p1, pivot = self._current_values()
        visible = self.model.phased(p0, p1, pivot)[mask]
        if visible.size:
            bottom, top = np.percentile(visible, (0.5, 99.5))
            padding = max(0.08 * float(top - bottom), 1.0)
            self.plot.setYRange(float(bottom - padding), float(top + padding))


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spectrum",
        nargs="?",
        type=Path,
        default=DEFAULT_DX,
        help="Optional JCAMP-DX file; defaults to the audited Aug. 10 5:15 pull",
    )
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    window = PhaseDemoWindow(args.spectrum)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
