"""Offline Phase 1-style NMR phasing, comparison, and analysis handoff.

Phase 3 preserves the manual P0/P1/pivot interaction from ``phase1.py``, adds
saved phase-candidate comparison, and explicitly hands the chosen phase to the
repository's existing ``process_fid.py`` analysis pipeline.  It never modifies
the source JCAMP-DX file, never controls hardware, and writes only to a user-
selected exploratory output directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

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
import process_fid  # noqa: E402


DEFAULT_DX = (
    REPO_ROOT
    / "results/runs/automated/chemyx_demo_081026_v3"
    / "20260810_171441_si6/raw_nmr"
    / "20260810_171806_081626_phsi4_0001_8scan_gain12.dx"
)
LINE_BROADENING_HZ = 0.03
ZERO_FILL_POINTS = 65536
PHASE_SET_SCHEMA = "chemyx-pump.nmr-phase-set.v1"
PHASE3_SCRIPT_IDENTITY = "scripts/nmr/phase3.py"


@dataclass(frozen=True)
class PhaseCandidate:
    """The minimum state needed to reproduce one displayed phase exactly."""

    name: str
    p0_deg: float
    p1_deg: float
    pivot_ppm: float

    def matches(self, p0_deg: float, p1_deg: float, pivot_ppm: float) -> bool:
        return bool(
            np.isclose(self.p0_deg, p0_deg, rtol=0.0, atol=1e-9)
            and np.isclose(self.p1_deg, p1_deg, rtol=0.0, atol=1e-9)
            and np.isclose(self.pivot_ppm, pivot_ppm, rtol=0.0, atol=1e-9)
        )


class PhaseCandidateCollection:
    """Small in-session phase store, intentionally independent of Qt."""

    def __init__(self) -> None:
        self.candidates: list[PhaseCandidate] = []
        self.selected_index: int | None = None
        self._next_number = 1

    @property
    def selected(self) -> PhaseCandidate | None:
        if self.selected_index is None:
            return None
        if not 0 <= self.selected_index < len(self.candidates):
            return None
        return self.candidates[self.selected_index]

    def add(self, p0_deg: float, p1_deg: float, pivot_ppm: float) -> PhaseCandidate:
        used = {candidate.name for candidate in self.candidates}
        while f"Candidate {self._next_number}" in used:
            self._next_number += 1
        candidate = PhaseCandidate(
            name=f"Candidate {self._next_number}",
            p0_deg=float(p0_deg),
            p1_deg=float(p1_deg),
            pivot_ppm=float(pivot_ppm),
        )
        self._next_number += 1
        self.candidates.append(candidate)
        self.selected_index = len(self.candidates) - 1
        return candidate

    def select(self, index: int) -> PhaseCandidate:
        if not 0 <= index < len(self.candidates):
            raise IndexError("phase candidate index is out of range")
        self.selected_index = index
        return self.candidates[index]

    def delete(self, index: int) -> PhaseCandidate:
        if not 0 <= index < len(self.candidates):
            raise IndexError("phase candidate index is out of range")
        removed = self.candidates.pop(index)
        if not self.candidates:
            self.selected_index = None
        elif self.selected_index == index:
            self.selected_index = min(index, len(self.candidates) - 1)
        elif self.selected_index is not None and self.selected_index > index:
            self.selected_index -= 1
        return removed

    def rename(self, index: int, name: str) -> PhaseCandidate:
        clean = " ".join(name.split())
        if not clean:
            raise ValueError("candidate name cannot be empty")
        if any(i != index and item.name == clean for i, item in enumerate(self.candidates)):
            raise ValueError(f"a phase candidate named {clean!r} already exists")
        old = self.select(index)
        renamed = PhaseCandidate(clean, old.p0_deg, old.p1_deg, old.pivot_ppm)
        self.candidates[index] = renamed
        return renamed

    def clear(self) -> None:
        self.candidates.clear()
        self.selected_index = None
        self._next_number = 1

    def state_text(self, p0_deg: float, p1_deg: float, pivot_ppm: float) -> str:
        selected = self.selected
        if selected is None:
            return "No phase candidate selected"
        suffix = "" if selected.matches(p0_deg, p1_deg, pivot_ppm) else " — Modified"
        return f"{selected.name}{suffix}"

    def to_payload(self, source: Path, dataset_display_name: str) -> dict:
        return {
            "schema": PHASE_SET_SCHEMA,
            "source_file": str(Path(source).resolve()),
            "dataset_display_name": dataset_display_name,
            "selected_index": self.selected_index,
            "candidates": [asdict(candidate) for candidate in self.candidates],
        }

    def save(self, path: Path, source: Path, dataset_display_name: str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_payload(source, dataset_display_name), indent=2),
            encoding="utf-8",
        )
        return path

    def load(self, path: Path, expected_source: Path) -> None:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        if payload.get("schema") != PHASE_SET_SCHEMA:
            raise ValueError("not a supported Phase 3 phase-set file")
        saved_source = Path(payload.get("source_file", "")).resolve()
        if saved_source != Path(expected_source).resolve():
            raise ValueError(
                "phase set belongs to a different source file: "
                f"{saved_source}"
            )
        loaded = [
            PhaseCandidate(
                name=str(item["name"]),
                p0_deg=float(item["p0_deg"]),
                p1_deg=float(item["p1_deg"]),
                pivot_ppm=float(item["pivot_ppm"]),
            )
            for item in payload.get("candidates", [])
        ]
        if len({item.name for item in loaded}) != len(loaded):
            raise ValueError("phase set contains duplicate candidate names")
        selected = payload.get("selected_index")
        if selected is not None and not 0 <= int(selected) < len(loaded):
            raise ValueError("phase set selected_index is out of range")
        self.candidates = loaded
        self.selected_index = None if selected is None else int(selected)
        self._next_number = 1
        used = {item.name for item in loaded}
        while f"Candidate {self._next_number}" in used:
            self._next_number += 1


@dataclass(frozen=True)
class Phase3AnalysisResult:
    output_directory: Path
    summary_path: Path
    provenance_path: Path
    pipeline_arguments: tuple[str, ...]


class PhaseSpectrumModel:
    """Cache the audited FFT once and apply Phase 1-compatible phase in memory."""

    def __init__(self, path: Path):
        self.load(path)

    def load(self, path: Path) -> None:
        path = Path(path).resolve()
        if path.suffix.lower() != ".dx" or not path.is_file():
            raise ValueError(f"Expected an existing .dx file: {path}")
        inspection = build_processing_inspection(
            path,
            line_broadening_hz=LINE_BROADENING_HZ,
            zero_fill_points=ZERO_FILL_POINTS,
            inverse_phase=True,
        )
        self.path = path
        self.ppm = np.asarray(inspection.ppm_axis, dtype=float)
        self.fft_spectrum = np.asarray(inspection.fft_spectrum, dtype=np.complex128)
        self.production_spectrum = np.real(inspection.phased_spectrum)
        self.production_p0 = float(inspection.phase0_deg)
        self.production_p1 = float(inspection.phase1_deg)
        self.production_pivot_ppm = float(self.ppm[0])
        self.dataset_display_name = resolve_dataset_display_name(input_paths=path)

    def effective_p0(self, p0_deg: float, p1_deg: float, pivot_ppm: float) -> float:
        """Translate the GUI pivot to nmrglue's index-zero P1 convention."""

        fractions = np.linspace(0.0, 1.0, self.ppm.size)
        order = np.argsort(self.ppm)
        pivot_fraction = float(
            np.interp(pivot_ppm, self.ppm[order], fractions[order])
        )
        return float(p0_deg) - float(p1_deg) * pivot_fraction

    def phased(self, p0_deg: float, p1_deg: float, pivot_ppm: float) -> np.ndarray:
        return np.real(
            ng.proc_base.ps(
                self.fft_spectrum,
                p0=self.effective_p0(p0_deg, p1_deg, pivot_ppm),
                p1=float(p1_deg),
                inv=True,
            )
        )


def _unique_run_name(output_root: Path, when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S_%f")
    base = f"phase3_{stamp}"
    candidate = base
    suffix = 2
    while (output_root / candidate).exists():
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def run_selected_phase_analysis(
    *,
    source: Path,
    output_root: Path,
    dataset_display_name: str,
    candidate: PhaseCandidate,
    effective_p0_deg: float,
    pipeline_runner: Callable[[list[str]], int] = process_fid.main,
    when: datetime | None = None,
) -> Phase3AnalysisResult:
    """Run the authoritative processor once from raw FID with manual phase."""

    source = Path(source).resolve()
    if source.suffix.lower() != ".dx" or not source.is_file():
        raise ValueError(f"Expected an existing .dx file: {source}")
    output_root = Path(output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    run_name = _unique_run_name(output_root, when)
    output_directory = output_root / run_name
    arguments = [
        str(source),
        "--output-dir",
        str(output_root),
        "--run-name",
        run_name,
        "--dataset-display-name",
        dataset_display_name,
        "--line-broadening-hz",
        str(LINE_BROADENING_HZ),
        "--zero-fill-points",
        str(ZERO_FILL_POINTS),
        "--phase-method",
        "manual",
        "--phase0",
        repr(float(effective_p0_deg)),
        "--phase1",
        repr(float(candidate.p1_deg)),
        "--export-csv",
    ]
    return_code = pipeline_runner(arguments)
    if return_code != 0:
        raise RuntimeError(f"process_fid.py analysis failed with exit code {return_code}")
    summary_path = output_directory / f"{run_name}_summary.json"
    if not summary_path.is_file():
        raise RuntimeError(f"analysis completed without its expected summary: {summary_path}")
    pipeline_summary = json.loads(summary_path.read_text(encoding="utf-8"))
    actual_parameters = pipeline_summary.get("parameters", {})
    baseline_method = actual_parameters.get("baseline_method")
    run_time = when or datetime.now()
    provenance = {
        "exploratory_only": True,
        "created_at": run_time.isoformat(timespec="microseconds"),
        "script": PHASE3_SCRIPT_IDENTITY,
        "source_file": str(source),
        "source_filename": source.name,
        "dataset_display_name": dataset_display_name,
        "selected_phase_candidate": candidate.name,
        "phase": {
            "p0_deg_at_pivot": candidate.p0_deg,
            "p1_deg": candidate.p1_deg,
            "pivot_ppm": candidate.pivot_ppm,
            "effective_p0_deg_at_array_index_zero": float(effective_p0_deg),
            "inverse_phase": True,
        },
        "display_preprocessing": {
            "line_broadening_hz": LINE_BROADENING_HZ,
            "zero_fill_points": ZERO_FILL_POINTS,
        },
        "pipeline": {
            "entry_point": "scripts/nmr/process_fid.py:main",
            "spectrum_builder": "chemyx_lab.analysis.nmr.build_phased_spectrum",
            "summary_file": str(summary_path),
            "arguments": arguments,
            "processing_settings": actual_parameters,
            "baseline_settings": {
                "method": baseline_method,
                "asls_smoothness": 1e6
                if baseline_method == "asymmetric_least_squares"
                else None,
                "asls_asymmetry": 0.001
                if baseline_method == "asymmetric_least_squares"
                else None,
                "asls_iterations": 10
                if baseline_method == "asymmetric_least_squares"
                else None,
                "regional_polynomial_order": actual_parameters.get(
                    "baseline_order"
                ),
            },
            "referencing_settings": {
                key: actual_parameters.get(key)
                for key in (
                    "reference_method",
                    "reference_model",
                    "reference_observed_ppm",
                    "reference_expected_ppm",
                    "reference_confidence",
                )
            },
            "peak_analysis_settings": {
                key: actual_parameters.get(key)
                for key in (
                    "region_min",
                    "region_max",
                    "detection_trace",
                    "min_prominence_snr",
                    "min_peak_distance_ppm",
                    "min_peak_width_ppm",
                    "smoothing_window_ppm",
                    "simple_target_ppm",
                    "simple_window_ppm",
                    "simple_restrict_to_window",
                    "qc_min_snr",
                    "qc_min_prominence_snr",
                    "qc_min_width_hz",
                    "qc_max_width_hz",
                    "qc_require_positive_area",
                )
            },
            "statistics": pipeline_summary.get("statistics", {}),
            "processing_order": pipeline_summary.get("processing_order", []),
        },
    }
    provenance_path = output_directory / f"{run_name}_phase3_provenance.json"
    provenance_path.write_text(
        json.dumps(provenance, indent=2, sort_keys=True, default=str),
        encoding="utf-8",
    )
    return Phase3AnalysisResult(
        output_directory=output_directory,
        summary_path=summary_path,
        provenance_path=provenance_path,
        pipeline_arguments=tuple(arguments),
    )


class Phase3Window(QtWidgets.QMainWindow):
    def __init__(self, path: Path = DEFAULT_DX):
        super().__init__()
        self.setWindowTitle("NMR Phase Explorer — Phase 3 exploratory")
        self.resize(1380, 980)
        self.model = PhaseSpectrumModel(path)
        self.phase_candidates = PhaseCandidateCollection()
        self.analysis_directory: Path | None = None
        self._analysis_running = False
        self._updating_candidate_list = False
        self._build_ui()
        self._load_model_into_controls()

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        heading = QtWidgets.QLabel("NMR Phase Explorer — Compare and Analyze")
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)

        file_row = QtWidgets.QHBoxLayout()
        file_row.addWidget(QtWidgets.QLabel("File:"))
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
        self.reference_checkbox = QtWidgets.QCheckBox(
            "Show production reference trace"
        )
        self.reference_checkbox.toggled.connect(self._update_plot)
        view_row.addWidget(self.reference_checkbox)
        view_row.addStretch(1)
        layout.addLayout(view_row)

        controls = QtWidgets.QGridLayout()
        self.p0_slider, self.p0_value = self._phase_control(controls, 0, "Phase 0")
        self.p1_slider, self.p1_value = self._phase_control(controls, 1, "Phase 1")
        controls.addWidget(QtWidgets.QLabel("Pivot ppm"), 2, 0)
        self.pivot = QtWidgets.QDoubleSpinBox(decimals=6, minimum=-100, maximum=100)
        self.pivot.setSingleStep(0.01)
        self.pivot.valueChanged.connect(self._update_plot)
        controls.addWidget(self.pivot, 2, 1)
        controls.addWidget(
            QtWidgets.QLabel(
                "P1 is referenced around this ppm; production uses array index 0."
            ),
            2,
            2,
        )
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
            pen=pg.mkPen("#1f77b4", width=1.2), autoDownsample=True, clipToView=True
        )
        self.production_curve = self.plot.plot(
            pen=pg.mkPen("#d95f02", width=1.0, style=QtCore.Qt.DashLine),
            autoDownsample=True,
            clipToView=True,
        )
        layout.addWidget(self.plot, 1)

        workflow = QtWidgets.QHBoxLayout()
        workflow.addWidget(self._candidate_group(), 1)
        workflow.addWidget(self._analysis_group(), 1)
        layout.addLayout(workflow)

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

    def _candidate_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Saved Phase Corrections")
        layout = QtWidgets.QVBoxLayout(box)
        self.candidate_list = QtWidgets.QListWidget()
        self.candidate_list.currentRowChanged.connect(self._candidate_selected)
        layout.addWidget(self.candidate_list)
        row = QtWidgets.QHBoxLayout()
        save = QtWidgets.QPushButton("Save Current Phase")
        save.clicked.connect(self._save_candidate)
        row.addWidget(save)
        delete = QtWidgets.QPushButton("Delete")
        delete.clicked.connect(self._delete_candidate)
        row.addWidget(delete)
        rename = QtWidgets.QPushButton("Rename")
        rename.clicked.connect(self._rename_candidate)
        row.addWidget(rename)
        layout.addLayout(row)
        persistence = QtWidgets.QHBoxLayout()
        save_set = QtWidgets.QPushButton("Save Phase Set")
        save_set.clicked.connect(self._save_phase_set)
        persistence.addWidget(save_set)
        load_set = QtWidgets.QPushButton("Load Phase Set")
        load_set.clicked.connect(self._load_phase_set)
        persistence.addWidget(load_set)
        layout.addLayout(persistence)
        self.candidate_status = QtWidgets.QLabel("No phase candidate selected")
        self.candidate_status.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.candidate_status)
        return box

    def _analysis_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Analysis")
        layout = QtWidgets.QVBoxLayout(box)
        layout.addWidget(QtWidgets.QLabel("Output directory"))
        self.analysis_directory_label = QtWidgets.QLineEdit(readOnly=True)
        self.analysis_directory_label.setPlaceholderText("Choose a directory first")
        layout.addWidget(self.analysis_directory_label)
        choose = QtWidgets.QPushButton("Choose Analysis Directory")
        choose.clicked.connect(self._choose_analysis_directory)
        layout.addWidget(choose)
        self.analyze_button = QtWidgets.QPushButton("Analyze Selected Phase")
        self.analyze_button.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.analyze_button.clicked.connect(self._analyze_selected_phase)
        layout.addWidget(self.analyze_button)
        self.analysis_status = QtWidgets.QLabel(
            "Select an exact saved candidate and choose an output directory."
        )
        self.analysis_status.setWordWrap(True)
        layout.addWidget(self.analysis_status)
        layout.addStretch(1)
        return box

    def _load_model_into_controls(self) -> None:
        self.file_label.setText(str(self.model.path))
        for widget in (self.p0_slider, self.p1_slider, self.pivot):
            widget.blockSignals(True)
        self.p0_slider.setValue(round(self.model.production_p0 * 10))
        self.p1_slider.setValue(round(self.model.production_p1 * 10))
        self.pivot.setValue(self.model.production_pivot_ppm)
        for widget in (self.p0_slider, self.p1_slider, self.pivot):
            widget.blockSignals(False)
        self.production_curve.setData(self.model.ppm, self.model.production_spectrum)
        self._update_plot()
        self._set_view(None)

    def _current_values(self) -> tuple[float, float, float]:
        return (
            self.p0_slider.value() / 10.0,
            self.p1_slider.value() / 10.0,
            self.pivot.value(),
        )

    def _set_phase_controls(self, candidate: PhaseCandidate) -> None:
        for widget in (self.p0_slider, self.p1_slider, self.pivot):
            widget.blockSignals(True)
        self.p0_slider.setValue(round(candidate.p0_deg * 10))
        self.p1_slider.setValue(round(candidate.p1_deg * 10))
        self.pivot.setValue(candidate.pivot_ppm)
        for widget in (self.p0_slider, self.p1_slider, self.pivot):
            widget.blockSignals(False)
        self._update_plot()

    def _update_plot(self, _value=None) -> None:
        p0_deg, p1_deg, pivot_ppm = self._current_values()
        spectrum = self.model.phased(p0_deg, p1_deg, pivot_ppm)
        self.current_curve.setData(self.model.ppm, spectrum)
        self.production_curve.setVisible(self.reference_checkbox.isChecked())
        self.p0_value.setText(f"{p0_deg:.1f}°")
        self.p1_value.setText(f"{p1_deg:.1f}°")
        self.plot.setTitle(
            f"{self.model.dataset_display_name} — {self.model.path.name}"
        )
        production = (
            np.isclose(p0_deg, self.model.production_p0)
            and np.isclose(p1_deg, self.model.production_p1)
            and np.isclose(pivot_ppm, self.model.production_pivot_ppm)
        )
        mode = "PRODUCTION SETTINGS" if production else "EXPLORATORY SETTINGS"
        self.status.setText(
            f"{mode} | P0={p0_deg:.1f}°, P1={p1_deg:.1f}°, "
            f"pivot={pivot_ppm:.6f} ppm | Source .dx remains unchanged."
        )
        self._refresh_candidate_state()

    def _refresh_candidate_state(self) -> None:
        p0_deg, p1_deg, pivot_ppm = self._current_values()
        text = self.phase_candidates.state_text(p0_deg, p1_deg, pivot_ppm)
        self.candidate_status.setText(text)
        selected = self.phase_candidates.selected
        exact = selected is not None and selected.matches(p0_deg, p1_deg, pivot_ppm)
        self.analyze_button.setEnabled(
            exact and self.analysis_directory is not None and not self._analysis_running
        )
        if selected is None:
            self.analysis_status.setText("No phase candidate selected.")
        elif not exact:
            self.analysis_status.setText(
                f"{selected.name} is modified; save it as a new candidate or reselect it."
            )
        elif self.analysis_directory is None:
            self.analysis_status.setText(
                f"{selected.name} selected. Choose an analysis directory."
            )
        elif not self._analysis_running:
            self.analysis_status.setText(
                f"{selected.name} selected. Output: {self.analysis_directory}"
            )

    def _rebuild_candidate_list(self) -> None:
        self._updating_candidate_list = True
        self.candidate_list.clear()
        for candidate in self.phase_candidates.candidates:
            self.candidate_list.addItem(
                f"{candidate.name}  |  P0 {candidate.p0_deg:.1f}°, "
                f"P1 {candidate.p1_deg:.1f}°, pivot {candidate.pivot_ppm:.6f} ppm"
            )
        if self.phase_candidates.selected_index is not None:
            self.candidate_list.setCurrentRow(self.phase_candidates.selected_index)
        self._updating_candidate_list = False
        self._refresh_candidate_state()

    def _save_candidate(self) -> None:
        candidate = self.phase_candidates.add(*self._current_values())
        self._rebuild_candidate_list()
        self._set_phase_controls(candidate)

    def _candidate_selected(self, row: int) -> None:
        if self._updating_candidate_list or row < 0:
            return
        try:
            candidate = self.phase_candidates.select(row)
            self._set_phase_controls(candidate)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not select phase", str(exc))

    def _delete_candidate(self) -> None:
        row = self.candidate_list.currentRow()
        if row < 0:
            return
        self.phase_candidates.delete(row)
        self._rebuild_candidate_list()
        if self.phase_candidates.selected is not None:
            self._set_phase_controls(self.phase_candidates.selected)

    def _rename_candidate(self) -> None:
        row = self.candidate_list.currentRow()
        if row < 0:
            return
        current = self.phase_candidates.candidates[row]
        name, accepted = QtWidgets.QInputDialog.getText(
            self, "Rename phase candidate", "Name", text=current.name
        )
        if not accepted:
            return
        try:
            self.phase_candidates.rename(row, name)
            self._rebuild_candidate_list()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not rename candidate", str(exc))

    def _save_phase_set(self) -> None:
        suggested = self.model.path.with_suffix(".phase3-phases.json")
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Phase Set", str(suggested), "JSON files (*.json)"
        )
        if not filename:
            return
        try:
            path = self.phase_candidates.save(
                Path(filename), self.model.path, self.model.dataset_display_name
            )
            self.analysis_status.setText(f"Saved phase set: {path}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not save phase set", str(exc))

    def _load_phase_set(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load Phase Set",
            str(self.model.path.parent),
            "JSON files (*.json)",
        )
        if not filename:
            return
        try:
            self.phase_candidates.load(Path(filename), self.model.path)
            self._rebuild_candidate_list()
            if self.phase_candidates.selected is not None:
                self._set_phase_controls(self.phase_candidates.selected)
            self.analysis_status.setText(f"Loaded phase set: {filename}")
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not load phase set", str(exc))

    def _choose_analysis_directory(self) -> None:
        initial = self.analysis_directory or (REPO_ROOT / "results")
        selected = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Choose Phase 3 Analysis Directory", str(initial)
        )
        if not selected:
            return
        self.analysis_directory = Path(selected).resolve()
        self.analysis_directory_label.setText(str(self.analysis_directory))
        self._refresh_candidate_state()

    def _analyze_selected_phase(self) -> None:
        candidate = self.phase_candidates.selected
        p0_deg, p1_deg, pivot_ppm = self._current_values()
        if candidate is None or not candidate.matches(p0_deg, p1_deg, pivot_ppm):
            QtWidgets.QMessageBox.warning(
                self,
                "Select a saved phase",
                "Select an unmodified saved phase candidate before analysis.",
            )
            return
        if self.analysis_directory is None:
            QtWidgets.QMessageBox.warning(
                self, "Choose output directory", "Choose an analysis directory first."
            )
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Analyze selected phase",
            f"Analyze {candidate.name} with P0={candidate.p0_deg:.1f}°, "
            f"P1={candidate.p1_deg:.1f}°, pivot={candidate.pivot_ppm:.6f} ppm?\n\n"
            f"A unique run folder will be created under:\n{self.analysis_directory}",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._analysis_running = True
        self._refresh_candidate_state()
        self.analysis_status.setText(f"Analyzing {candidate.name}…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        QtWidgets.QApplication.processEvents()
        final_status = ""
        try:
            result = run_selected_phase_analysis(
                source=self.model.path,
                output_root=self.analysis_directory,
                dataset_display_name=self.model.dataset_display_name,
                candidate=candidate,
                effective_p0_deg=self.model.effective_p0(
                    candidate.p0_deg, candidate.p1_deg, candidate.pivot_ppm
                ),
            )
            final_status = f"Analysis complete: {result.output_directory}"
        except Exception as exc:
            final_status = f"Analysis failed: {exc}"
            QtWidgets.QMessageBox.critical(self, "Analysis failed", str(exc))
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._analysis_running = False
            self._refresh_candidate_state()
            self.analysis_status.setText(final_status)

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
            self.phase_candidates.clear()
            self.analysis_directory = None
            self.analysis_directory_label.clear()
            self._rebuild_candidate_list()
            self._load_model_into_controls()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Could not open DX file", str(exc))

    def _reset_production(self) -> None:
        production = PhaseCandidate(
            "Production",
            self.model.production_p0,
            self.model.production_p1,
            self.model.production_pivot_ppm,
        )
        self._set_phase_controls(production)

    def _set_view(self, bounds) -> None:
        if bounds is None:
            low, high = float(np.min(self.model.ppm)), float(np.max(self.model.ppm))
        else:
            low, high = sorted(bounds)
        self.plot.setXRange(low, high, padding=0.02)
        mask = (self.model.ppm >= low) & (self.model.ppm <= high)
        visible = self.model.phased(*self._current_values())[mask]
        if visible.size:
            bottom, top = np.percentile(visible, (0.5, 99.5))
            padding = max(0.08 * float(top - bottom), 1.0)
            self.plot.setYRange(float(bottom - padding), float(top + padding))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spectrum",
        nargs="?",
        type=Path,
        default=DEFAULT_DX,
        help="optional NMReady JCAMP-DX file",
    )
    parser.add_argument(
        "--smoke-screenshot",
        type=Path,
        help="render one offscreen screenshot and exit",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication(sys.argv)
    pg.setConfigOptions(antialias=True)
    window = Phase3Window(args.spectrum)
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
