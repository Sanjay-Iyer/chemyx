"""Three-way NMR phase review: Original, Automated script result, and Manual.

Phase 4 keeps the Phase 3 manual P0/P1/pivot interaction and candidate
workflow, and adds a chemist-facing before/after review of what the real
automated processing pipeline (``scripts/nmr/process_fid.py:main``) did to a
spectrum.  Three synchronised plots are maintained from one source file:

``Original``    the unphased FFT straight from the JCAMP-DX file;
``Automated``   the phase-corrected result produced by ``process_fid.py``;
``Manual``      the live interactive adjustment.

The automated trace is never recomputed with GUI mathematics.  It is read from
the pipeline's own exported spectrum CSV when one exists, and otherwise
rebuilt through the shared audited helper using the phase values the pipeline
recorded in its summary -- the reconstruction path is always labelled as such.

This module is offline review only.  It never controls the NMR spectrometer,
the pump, or the needle; it never modifies a source ``.dx`` file; and it writes
only into an output directory the user selects.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Sequence

import nmrglue as ng
import numpy as np
import pyqtgraph as pg
from PySide6 import QtCore, QtWidgets

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from chemyx_lab.analysis.nmr import (  # noqa: E402
    build_processing_inspection,
    read_jcamp_fid,
)
from chemyx_lab.analysis.plot_titles import (  # noqa: E402
    resolve_dataset_display_name,
)
from _common import parse_acquisition_timestamp  # noqa: E402
import process_fid  # noqa: E402


DEFAULT_DX = (
    REPO_ROOT
    / "results/runs/automated/chemyx_demo_081026_v3"
    / "20260810_171441_si6/raw_nmr"
    / "20260810_171806_081626_phsi4_0001_8scan_gain12.dx"
)

# The repository has no central constant for this location; the automated
# workflows write here by convention.  An environment variable keeps the path
# overridable without editing code on a different machine.
DEFAULT_RUNS_ROOT = Path(
    os.environ.get(
        "CHEMYX_NMR_RUNS_ROOT", str(REPO_ROOT / "results/runs/automated")
    )
)

LINE_BROADENING_HZ = 0.03
ZERO_FILL_POINTS = 65536

PHASE_SET_SCHEMA = "chemyx-pump.nmr-phase-set.v1"
PHASE_REVIEW_SCHEMA = "chemyx-pump.nmr-phase-review.v1"
PIPELINE_ENTRY_POINT = "scripts/nmr/process_fid.py:main"
PHASE4_SCRIPT_IDENTITY = "scripts/nmr/phase4.py"

#: How the displayed automated trace was obtained.
SPECTRUM_FROM_CSV = "pipeline_spectrum_csv"
SPECTRUM_RECONSTRUCTED = "reconstructed_from_summary_phase"


class SourceDxNotFound(FileNotFoundError):
    """Raised when a review's source ``.dx`` cannot be located on disk."""


# ---------------------------------------------------------------------------
# Manual phase candidates (Phase 3 compatible)
# ---------------------------------------------------------------------------


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
    """Small in-session phase store, intentionally independent of Qt.

    The on-disk format is deliberately unchanged from Phase 3 so phase sets
    written by either script load in the other.
    """

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

    def add(
        self, p0_deg: float, p1_deg: float, pivot_ppm: float, name: str | None = None
    ) -> PhaseCandidate:
        used = {candidate.name for candidate in self.candidates}
        if name is None:
            while f"Candidate {self._next_number}" in used:
                self._next_number += 1
            chosen = f"Candidate {self._next_number}"
            self._next_number += 1
        else:
            chosen = " ".join(name.split())
            if not chosen:
                raise ValueError("candidate name cannot be empty")
            if chosen in used:
                suffix = 2
                while f"{chosen} ({suffix})" in used:
                    suffix += 1
                chosen = f"{chosen} ({suffix})"
        candidate = PhaseCandidate(
            name=chosen,
            p0_deg=float(p0_deg),
            p1_deg=float(p1_deg),
            pivot_ppm=float(pivot_ppm),
        )
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
        if any(
            i != index and item.name == clean
            for i, item in enumerate(self.candidates)
        ):
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
            raise ValueError("not a supported phase-set file")
        saved_source = Path(payload.get("source_file", "")).resolve()
        if saved_source != Path(expected_source).resolve():
            raise ValueError(
                f"phase set belongs to a different source file: {saved_source}"
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


# ---------------------------------------------------------------------------
# Spectrum model: one audited FFT, three derived states
# ---------------------------------------------------------------------------


class ReviewSpectrumModel:
    """Cache the audited FFT once and derive Original and Manual from it.

    ``Original`` is the unphased FFT.  ``Manual`` applies the GUI's
    pivot-referenced phase.  The automated state lives outside this class
    because it belongs to the pipeline, not to the GUI.
    """

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
        self.metadata = dict(inspection.metadata)
        self.ppm = np.asarray(inspection.ppm_axis, dtype=float)
        self.fft_spectrum = np.asarray(
            inspection.fft_spectrum, dtype=np.complex128
        )
        # Before phase correction: the real FFT with no phase applied at all.
        self.original_spectrum = np.real(self.fft_spectrum)
        # The phase the pipeline uses for --phase-method stored.
        self.stored_spectrum = np.real(inspection.phased_spectrum)
        self.stored_p0 = float(inspection.phase0_deg)
        self.stored_p1 = float(inspection.phase1_deg)
        # nmrglue's p1 convention starts at array index zero.  Using that ppm
        # as the default pivot reproduces the stored phase exactly.
        self.stored_pivot_ppm = float(self.ppm[0])
        self.processed_points = int(inspection.processed_points)
        self.line_broadening_hz = float(inspection.line_broadening_hz)
        self.dataset_display_name = resolve_dataset_display_name(input_paths=path)

    def pivot_fraction(self, pivot_ppm: float) -> float:
        fractions = np.linspace(0.0, 1.0, self.ppm.size)
        order = np.argsort(self.ppm)
        return float(np.interp(pivot_ppm, self.ppm[order], fractions[order]))

    def effective_p0(self, p0_deg: float, p1_deg: float, pivot_ppm: float) -> float:
        """Translate the GUI pivot to nmrglue's index-zero P1 convention."""

        return float(p0_deg) - float(p1_deg) * self.pivot_fraction(pivot_ppm)

    def p0_at_pivot(
        self, index_zero_p0_deg: float, p1_deg: float, pivot_ppm: float
    ) -> float:
        """Inverse of :meth:`effective_p0`.

        The pipeline records P0 in nmrglue's index-zero convention.  The manual
        controls are pivot-referenced, so an automated phase must be converted
        before it can be shown on those controls.
        """

        return float(index_zero_p0_deg) + float(p1_deg) * self.pivot_fraction(
            pivot_ppm
        )

    def phased(self, p0_deg: float, p1_deg: float, pivot_ppm: float) -> np.ndarray:
        return np.real(
            ng.proc_base.ps(
                self.fft_spectrum,
                p0=self.effective_p0(p0_deg, p1_deg, pivot_ppm),
                p1=float(p1_deg),
                inv=True,
            )
        )

    def phased_index_zero(
        self, p0_deg: float, p1_deg: float, inverse: bool = True
    ) -> np.ndarray:
        """Apply a phase already expressed in nmrglue's index-zero convention."""

        return np.real(
            ng.proc_base.ps(
                self.fft_spectrum,
                p0=float(p0_deg),
                p1=float(p1_deg),
                inv=bool(inverse),
            )
        )


# ---------------------------------------------------------------------------
# The automated (pipeline) state
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AutomatedPhaseResult:
    """Everything needed to redisplay one ``process_fid.py`` result."""

    source_dx: Path
    source_filename: str
    output_directory: Path
    summary_path: Path
    entry_point: str = PIPELINE_ENTRY_POINT
    p0_deg: float = 0.0
    p1_deg: float = 0.0
    pivot_ppm: float | None = None
    phase_method: str = "stored"
    phase_direction: str = "inverse"
    reference_shift_ppm: float = 0.0
    line_broadening_hz: float = LINE_BROADENING_HZ
    zero_fill_points: int = ZERO_FILL_POINTS
    spectrum_csv: Path | None = None
    processed_at: str = ""
    run_name: str = ""
    dataset_display_name: str = ""
    processing_settings: dict = field(default_factory=dict)
    produced_by_phase4: bool = False

    @property
    def inverse_phase(self) -> bool:
        return self.phase_direction != "direct"

    def phase_text(self) -> str:
        pivot = (
            "array index 0"
            if self.pivot_ppm is None
            else f"{self.pivot_ppm:.6f} ppm"
        )
        return (
            f"P0 = {self.p0_deg:.1f}° | P1 = {self.p1_deg:.1f}° | Pivot = {pivot}"
        )

    def to_payload(self) -> dict:
        return {
            "source_dx": str(self.source_dx),
            "source_filename": self.source_filename,
            "output_directory": str(self.output_directory),
            "summary_path": str(self.summary_path),
            "entry_point": self.entry_point,
            "run_name": self.run_name,
            "dataset_display_name": self.dataset_display_name,
            "processed_at": self.processed_at,
            "produced_by_phase4": self.produced_by_phase4,
            "phase": {
                "p0_deg": self.p0_deg,
                "p1_deg": self.p1_deg,
                "pivot_ppm": self.pivot_ppm,
                "phase_method": self.phase_method,
                "phase_direction": self.phase_direction,
                "convention": "nmrglue index-zero p0/p1",
            },
            "display_preprocessing": {
                "line_broadening_hz": self.line_broadening_hz,
                "zero_fill_points": self.zero_fill_points,
            },
            "reference_shift_ppm": self.reference_shift_ppm,
            "spectrum_csv": (
                None if self.spectrum_csv is None else str(self.spectrum_csv)
            ),
            "processing_settings": self.processing_settings,
        }

    @classmethod
    def from_payload(cls, payload: dict) -> "AutomatedPhaseResult":
        phase = payload.get("phase", {})
        preprocessing = payload.get("display_preprocessing", {})
        spectrum_csv = payload.get("spectrum_csv")
        pivot = phase.get("pivot_ppm")
        return cls(
            source_dx=Path(payload["source_dx"]),
            source_filename=str(
                payload.get("source_filename") or Path(payload["source_dx"]).name
            ),
            output_directory=Path(payload.get("output_directory", "")),
            summary_path=Path(payload.get("summary_path", "")),
            entry_point=str(payload.get("entry_point", PIPELINE_ENTRY_POINT)),
            p0_deg=float(phase.get("p0_deg", 0.0)),
            p1_deg=float(phase.get("p1_deg", 0.0)),
            pivot_ppm=None if pivot is None else float(pivot),
            phase_method=str(phase.get("phase_method", "stored")),
            phase_direction=str(phase.get("phase_direction", "inverse")),
            reference_shift_ppm=float(payload.get("reference_shift_ppm", 0.0)),
            line_broadening_hz=float(
                preprocessing.get("line_broadening_hz", LINE_BROADENING_HZ)
            ),
            zero_fill_points=int(
                preprocessing.get("zero_fill_points", ZERO_FILL_POINTS)
            ),
            spectrum_csv=None if not spectrum_csv else Path(spectrum_csv),
            processed_at=str(payload.get("processed_at", "")),
            run_name=str(payload.get("run_name", "")),
            dataset_display_name=str(payload.get("dataset_display_name", "")),
            processing_settings=dict(payload.get("processing_settings", {})),
            produced_by_phase4=bool(payload.get("produced_by_phase4", False)),
        )


def _record_for_source(summary: dict, source_filename: str) -> dict:
    """Pick the summary record matching one acquisition filename."""

    records = summary.get("records", [])
    if not records:
        raise ValueError("pipeline summary contains no records")
    for record in records:
        if str(record.get("file", "")) == source_filename:
            return record
    return records[0]


def automated_result_from_summary(
    summary_path: Path,
    *,
    source_filename: str | None = None,
    search_roots: Sequence[Path] = (),
    produced_by_phase4: bool = False,
) -> AutomatedPhaseResult:
    """Build the automated state from a ``process_fid.py`` summary on disk.

    The summary is the pipeline's own authoritative output; nothing here
    recomputes what it reports.
    """

    summary_path = Path(summary_path).resolve()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    output_directory = summary_path.parent
    record = _record_for_source(
        summary, source_filename or str(summary.get("records", [{}])[0].get("file", ""))
    )
    parameters = dict(summary.get("parameters", {}))

    recorded_source = str(record.get("source_path", "") or "")
    roots = list(search_roots) + [output_directory, output_directory.parent]
    source_dx = resolve_source_dx(
        recorded_source,
        str(record.get("file", "")),
        search_roots=roots,
    )

    spectrum_csv_text = str(record.get("spectrum_csv", "") or "")
    spectrum_csv: Path | None = None
    if spectrum_csv_text:
        candidate = Path(spectrum_csv_text)
        if not candidate.is_file():
            # The pipeline records absolute paths; a relocated result folder
            # still carries the file next to its summary.
            relocated = output_directory / candidate.name
            candidate = relocated if relocated.is_file() else candidate
        if candidate.is_file():
            spectrum_csv = candidate.resolve()

    return AutomatedPhaseResult(
        source_dx=source_dx,
        source_filename=str(record.get("file", source_dx.name)),
        output_directory=output_directory,
        summary_path=summary_path,
        entry_point=PIPELINE_ENTRY_POINT,
        p0_deg=float(record.get("phase0_deg", 0.0)),
        p1_deg=float(record.get("phase1_deg", 0.0)),
        pivot_ppm=None,
        phase_method=str(record.get("phase_method", "stored")),
        phase_direction=str(record.get("phase_direction", "inverse")),
        reference_shift_ppm=float(record.get("applied_shift_ppm", 0.0) or 0.0),
        line_broadening_hz=float(
            record.get("line_broadening_hz", LINE_BROADENING_HZ)
        ),
        zero_fill_points=int(
            parameters.get("zero_fill_points") or record.get("processed_points")
            or ZERO_FILL_POINTS
        ),
        spectrum_csv=spectrum_csv,
        processed_at=str(summary.get("created_at", "")),
        run_name=output_directory.name,
        dataset_display_name=str(parameters.get("dataset_display_name", "") or ""),
        processing_settings=parameters,
        produced_by_phase4=produced_by_phase4,
    )


def resolve_source_dx(
    recorded_path: str | Path,
    filename: str,
    *,
    search_roots: Sequence[Path] = (),
) -> Path:
    """Locate a source ``.dx``, tolerating results moved between machines.

    Production summaries written on another machine carry absolute paths that
    no longer exist here, so a recorded path that is missing falls back to a
    search by filename.  Failure is reported, never silently ignored.
    """

    recorded = Path(recorded_path) if recorded_path else None
    if recorded is not None and recorded.is_file():
        return recorded.resolve()

    if filename:
        for root in search_roots:
            root = Path(root)
            if not root.exists():
                continue
            direct = root / "raw_nmr" / filename
            if direct.is_file():
                return direct.resolve()
            for found in root.rglob(filename):
                if found.is_file():
                    return found.resolve()

    searched = ", ".join(str(Path(root)) for root in search_roots) or "no directories"
    raise SourceDxNotFound(
        f"Could not locate the source .dx file {filename or recorded!r}. "
        f"Recorded path: {recorded or 'none'} (missing). Searched: {searched}. "
        "Open the .dx manually to continue the review."
    )


def read_pipeline_spectrum_csv(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Read ``referenced_ppm``/``real`` from the pipeline's exported CSV."""

    ppm: list[float] = []
    real: list[float] = []
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = {"referenced_ppm", "real"} - set(reader.fieldnames or ())
        if missing:
            raise ValueError(
                f"{path} is not a pipeline spectrum CSV (missing {sorted(missing)})"
            )
        for row in reader:
            ppm.append(float(row["referenced_ppm"]))
            real.append(float(row["real"]))
    if not ppm:
        raise ValueError(f"{path} contains no spectrum rows")
    return np.asarray(ppm, dtype=float), np.asarray(real, dtype=float)


def automated_spectrum(
    result: AutomatedPhaseResult, model: ReviewSpectrumModel
) -> tuple[np.ndarray, np.ndarray, str]:
    """Return ``(ppm, intensity, provenance)`` for the automated trace.

    Prefers the pipeline's own exported spectrum.  When the run was made
    without ``--export-csv`` -- which is the production default -- the trace is
    rebuilt from the same audited FFT using the phase the pipeline recorded,
    and the provenance string says so.
    """

    if result.spectrum_csv is not None and Path(result.spectrum_csv).is_file():
        ppm, real = read_pipeline_spectrum_csv(result.spectrum_csv)
        return ppm, real, SPECTRUM_FROM_CSV

    real = model.phased_index_zero(
        result.p0_deg, result.p1_deg, inverse=result.inverse_phase
    )
    # process_fid reports analysis_ppm = original_ppm + applied_shift_ppm.
    ppm = model.ppm + float(result.reference_shift_ppm)
    return ppm, real, SPECTRUM_RECONSTRUCTED


def _unique_run_name(output_root: Path, when: datetime | None = None) -> str:
    stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S_%f")
    base = f"phase4_{stamp}"
    candidate = base
    suffix = 2
    while (output_root / candidate).exists():
        candidate = f"{base}_{suffix}"
        suffix += 1
    return candidate


def run_automated_processing(
    *,
    source: Path,
    output_root: Path,
    dataset_display_name: str,
    pipeline_runner: Callable[[list[str]], int] = process_fid.main,
    when: datetime | None = None,
) -> AutomatedPhaseResult:
    """Run the real pipeline once, unmodified, and read back what it produced.

    Phase 4 does not pass a phase here: the point is to observe what the
    automated processing decides on its own.  ``--export-csv`` is requested so
    the displayed automated trace is the pipeline's literal output.
    """

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
        "--export-csv",
    ]
    return_code = pipeline_runner(arguments)
    if return_code != 0:
        raise RuntimeError(
            f"process_fid.py analysis failed with exit code {return_code}"
        )
    summary_path = output_directory / f"{run_name}_summary.json"
    if not summary_path.is_file():
        raise RuntimeError(
            f"analysis completed without its expected summary: {summary_path}"
        )
    return automated_result_from_summary(
        summary_path,
        source_filename=source.name,
        search_roots=[source.parent],
        produced_by_phase4=True,
    )


# ---------------------------------------------------------------------------
# Phase review files
# ---------------------------------------------------------------------------


def save_phase_review(
    path: Path,
    *,
    model: ReviewSpectrumModel,
    automated: AutomatedPhaseResult,
    manual: PhaseCandidate | None = None,
    candidates: PhaseCandidateCollection | None = None,
    when: datetime | None = None,
) -> Path:
    """Write a small versioned pointer file, not a copy of the results.

    The pipeline's own outputs stay authoritative; this records where they are
    and what phase they used so the comparison can be reopened without
    rerunning the processing.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PHASE_REVIEW_SCHEMA,
        "created_at": (when or datetime.now()).isoformat(timespec="microseconds"),
        "script": PHASE4_SCRIPT_IDENTITY,
        "review_only": True,
        "source_dx": str(model.path),
        "source_filename": model.path.name,
        "dataset_display_name": model.dataset_display_name,
        "automated": automated.to_payload(),
        "manual": None if manual is None else asdict(manual),
        "candidates": (
            []
            if candidates is None
            else [asdict(item) for item in candidates.candidates]
        ),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


@dataclass(frozen=True)
class PhaseReview:
    source_dx: Path
    dataset_display_name: str
    automated: AutomatedPhaseResult
    manual: PhaseCandidate | None
    candidates: list[PhaseCandidate]
    created_at: str


def load_phase_review(
    path: Path, *, search_roots: Sequence[Path] = ()
) -> PhaseReview:
    """Read a review file and resolve its source, reporting a missing file."""

    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("schema") != PHASE_REVIEW_SCHEMA:
        raise ValueError(
            f"not a supported phase review file (expected {PHASE_REVIEW_SCHEMA})"
        )
    automated = AutomatedPhaseResult.from_payload(payload["automated"])
    roots = list(search_roots) + [
        automated.output_directory,
        automated.output_directory.parent,
        Path(path).parent,
    ]
    source_dx = resolve_source_dx(
        payload.get("source_dx", ""),
        str(payload.get("source_filename", "")),
        search_roots=roots,
    )
    manual_payload = payload.get("manual")
    manual = (
        None
        if not manual_payload
        else PhaseCandidate(
            name=str(manual_payload["name"]),
            p0_deg=float(manual_payload["p0_deg"]),
            p1_deg=float(manual_payload["p1_deg"]),
            pivot_ppm=float(manual_payload["pivot_ppm"]),
        )
    )
    candidates = [
        PhaseCandidate(
            name=str(item["name"]),
            p0_deg=float(item["p0_deg"]),
            p1_deg=float(item["p1_deg"]),
            pivot_ppm=float(item["pivot_ppm"]),
        )
        for item in payload.get("candidates", [])
    ]
    return PhaseReview(
        source_dx=source_dx,
        dataset_display_name=str(payload.get("dataset_display_name", "")),
        automated=automated,
        manual=manual,
        candidates=candidates,
        created_at=str(payload.get("created_at", "")),
    )


def automated_as_candidate(
    result: AutomatedPhaseResult,
    model: ReviewSpectrumModel,
    *,
    name: str = "Automated",
    pivot_ppm: float | None = None,
) -> PhaseCandidate:
    """Convert a pipeline phase into pivot-referenced manual controls.

    With the default pivot (array index zero) the conversion is the identity,
    so copying the automated phase into Manual reproduces the automated trace
    exactly before the chemist changes anything.
    """

    pivot = model.stored_pivot_ppm if pivot_ppm is None else float(pivot_ppm)
    return PhaseCandidate(
        name=name,
        p0_deg=model.p0_at_pivot(result.p0_deg, result.p1_deg, pivot),
        p1_deg=float(result.p1_deg),
        pivot_ppm=pivot,
    )


# ---------------------------------------------------------------------------
# Browsing completed automated runs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Acquisition:
    """One ``.dx`` inside a completed automated run, with its known outputs."""

    run_name: str
    run_directory: Path
    source_dx: Path
    analysis_directory: Path | None = None
    summary_path: Path | None = None

    @property
    def has_automated_result(self) -> bool:
        return self.summary_path is not None and self.summary_path.is_file()

    def label(self) -> str:
        # ASCII only: these labels are also printed to Windows consoles, which
        # default to cp1252 and raise on decorative glyphs.
        mark = (
            "[processed]" if self.has_automated_result else "[not yet processed]"
        )
        return f"{self.source_dx.name}  {mark}"


def discover_runs(root: Path = DEFAULT_RUNS_ROOT) -> list[Path]:
    """List run directories that actually contain NMR acquisitions."""

    root = Path(root)
    if not root.is_dir():
        return []
    runs: list[Path] = []
    for project in sorted(p for p in root.iterdir() if p.is_dir()):
        nested = sorted(p for p in project.iterdir() if p.is_dir())
        if any((child / "raw_nmr").is_dir() for child in nested):
            runs.extend(
                child for child in nested if (child / "raw_nmr").is_dir()
            )
        elif (project / "raw_nmr").is_dir():
            runs.append(project)
    return runs


def _find_analysis_for(run_directory: Path, source_dx: Path) -> tuple[
    Path | None, Path | None
]:
    processed = run_directory / "processed_nmr"
    if not processed.is_dir():
        return None, None
    best: tuple[Path, Path] | None = None
    for analysis_dir in sorted(p for p in processed.iterdir() if p.is_dir()):
        summary = analysis_dir / f"{analysis_dir.name}_summary.json"
        if not summary.is_file():
            continue
        try:
            payload = json.loads(summary.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        files = {str(item.get("file", "")) for item in payload.get("records", [])}
        if source_dx.name in files:
            return analysis_dir, summary
        if best is None:
            best = (analysis_dir, summary)
    return best if best is not None else (None, None)


def discover_acquisitions(run_directory: Path) -> list[Acquisition]:
    """List the ``.dx`` acquisitions of one run and their analysis outputs."""

    run_directory = Path(run_directory)
    raw = run_directory / "raw_nmr"
    search_dir = raw if raw.is_dir() else run_directory
    found: list[Acquisition] = []
    for dx in sorted(search_dir.rglob("*.dx")):
        analysis_dir, summary = _find_analysis_for(run_directory, dx)
        found.append(
            Acquisition(
                run_name=run_directory.name,
                run_directory=run_directory,
                source_dx=dx.resolve(),
                analysis_directory=analysis_dir,
                summary_path=summary,
            )
        )
    return found


def acquisition_details(acquisition: Acquisition) -> dict[str, str]:
    """Read structured JCAMP metadata rather than parsing the filename."""

    details = {
        "run": acquisition.run_name,
        "file": acquisition.source_dx.name,
        "source_dx": str(acquisition.source_dx),
        "analysis_directory": (
            str(acquisition.analysis_directory)
            if acquisition.analysis_directory
            else "none"
        ),
        "automated_result": (
            "present" if acquisition.has_automated_result else "not yet processed"
        ),
    }
    try:
        fid = read_jcamp_fid(acquisition.source_dx)
    except Exception as exc:  # noqa: BLE001 - browsing must survive a bad file
        details["metadata_error"] = str(exc)
        return details
    metadata = fid.metadata
    timestamp, source = parse_acquisition_timestamp(
        metadata, acquisition.source_dx
    )
    details["acquired_at"] = (
        "unknown" if timestamp is None else timestamp.isoformat(sep=" ")
    )
    details["acquired_at_source"] = source
    for key, label in (("$SCANS", "scans"), ("$RECVR_GAIN", "gain")):
        value = str(metadata.get(key, "") or "").strip()
        if value:
            details[label] = value
    return details


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------


ORIGINAL_PEN = "#6b6b6b"
AUTOMATED_PEN = "#d95f02"
MANUAL_PEN = "#1f77b4"


class RunBrowserDialog(QtWidgets.QDialog):
    """A small run/acquisition picker, not a general file manager."""

    def __init__(self, root: Path, parent: QtWidgets.QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Browse Automated Runs")
        self.resize(940, 560)
        self.root = Path(root)
        self.selected: Acquisition | None = None

        layout = QtWidgets.QVBoxLayout(self)

        root_row = QtWidgets.QHBoxLayout()
        root_row.addWidget(QtWidgets.QLabel("Runs root:"))
        self.root_label = QtWidgets.QLineEdit(str(self.root), readOnly=True)
        root_row.addWidget(self.root_label, 1)
        change = QtWidgets.QPushButton("Change…")
        change.clicked.connect(self._change_root)
        root_row.addWidget(change)
        layout.addLayout(root_row)

        columns = QtWidgets.QHBoxLayout()
        left = QtWidgets.QVBoxLayout()
        left.addWidget(QtWidgets.QLabel("Runs"))
        self.run_list = QtWidgets.QListWidget()
        self.run_list.currentRowChanged.connect(self._run_selected)
        left.addWidget(self.run_list)
        columns.addLayout(left, 1)

        middle = QtWidgets.QVBoxLayout()
        middle.addWidget(QtWidgets.QLabel("NMR acquisitions"))
        self.acquisition_list = QtWidgets.QListWidget()
        self.acquisition_list.currentRowChanged.connect(self._acquisition_selected)
        self.acquisition_list.itemDoubleClicked.connect(lambda _item: self.accept())
        middle.addWidget(self.acquisition_list)
        columns.addLayout(middle, 1)

        right = QtWidgets.QVBoxLayout()
        right.addWidget(QtWidgets.QLabel("Details"))
        self.details = QtWidgets.QPlainTextEdit(readOnly=True)
        right.addWidget(self.details)
        columns.addLayout(right, 1)
        layout.addLayout(columns)

        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Open
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.open_button = buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Open
        )
        self.open_button.setEnabled(False)
        layout.addWidget(buttons)

        self._runs: list[Path] = []
        self._acquisitions: list[Acquisition] = []
        self._reload()

    def _reload(self) -> None:
        self.run_list.clear()
        self.acquisition_list.clear()
        self.details.clear()
        self._runs = discover_runs(self.root)
        if not self._runs:
            self.details.setPlainText(
                f"No automated runs with raw_nmr/ found under:\n{self.root}"
            )
            return
        for run in self._runs:
            try:
                shown = run.relative_to(self.root)
            except ValueError:
                shown = run
            self.run_list.addItem(str(shown))

    def _change_root(self) -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Select a runs root directory", str(self.root)
        )
        if chosen:
            self.root = Path(chosen)
            self.root_label.setText(chosen)
            self._reload()

    def _run_selected(self, row: int) -> None:
        self.acquisition_list.clear()
        self.selected = None
        self.open_button.setEnabled(False)
        if not 0 <= row < len(self._runs):
            return
        self._acquisitions = discover_acquisitions(self._runs[row])
        for acquisition in self._acquisitions:
            self.acquisition_list.addItem(acquisition.label())
        if not self._acquisitions:
            self.details.setPlainText("This run contains no .dx acquisitions.")

    def _acquisition_selected(self, row: int) -> None:
        if not 0 <= row < len(self._acquisitions):
            self.selected = None
            self.open_button.setEnabled(False)
            return
        self.selected = self._acquisitions[row]
        self.open_button.setEnabled(True)
        details = acquisition_details(self.selected)
        self.details.setPlainText(
            "\n".join(f"{key}: {value}" for key, value in details.items())
        )


class Phase4Window(QtWidgets.QMainWindow):
    def __init__(
        self, path: Path = DEFAULT_DX, runs_root: Path = DEFAULT_RUNS_ROOT
    ):
        super().__init__()
        self.setWindowTitle("NMR Phase Review — Phase 4 (offline review)")
        self.resize(1480, 1040)
        self.runs_root = Path(runs_root)
        self.model = ReviewSpectrumModel(path)
        self.phase_candidates = PhaseCandidateCollection()
        self.automated: AutomatedPhaseResult | None = None
        self.automated_trace: tuple[np.ndarray, np.ndarray] | None = None
        self.automated_provenance = ""
        self.output_directory: Path | None = None
        self._busy = False
        self._updating_candidate_list = False
        self._build_ui()
        self._load_model_into_controls()

    # -- construction ----------------------------------------------------

    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        layout = QtWidgets.QVBoxLayout(central)

        heading = QtWidgets.QLabel(
            "NMR Phase Review — Original vs Automated vs Manual"
        )
        heading.setStyleSheet("font-size: 20px; font-weight: 600;")
        layout.addWidget(heading)

        layout.addLayout(self._toolbar())
        layout.addLayout(self._view_row())

        self.stacked_widget = self._stacked_plots()
        self.overlay_widget = self._overlay_plot()
        self.overlay_widget.setVisible(False)
        layout.addWidget(self.stacked_widget, 1)
        layout.addWidget(self.overlay_widget, 1)

        lower = QtWidgets.QHBoxLayout()
        lower.addWidget(self._manual_group(), 2)
        lower.addWidget(self._candidate_group(), 2)
        lower.addWidget(self._information_group(), 3)
        layout.addLayout(lower)

        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _toolbar(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        for label, slot in (
            ("Open .DX", self._open_file),
            ("Browse Runs", self._browse_runs),
            ("Open Phase Review", self._open_phase_review),
            ("Run Automated Processing", self._run_automated),
            ("Select Output Directory", self._choose_output_directory),
            ("Save Phase Review", self._save_phase_review),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
            if label == "Run Automated Processing":
                self.run_button = button
            if label == "Save Phase Review":
                self.save_review_button = button
        row.addStretch(1)
        return row

    def _view_row(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("View:"))
        for label, bounds in (
            ("Full spectrum", None),
            ("Product region", (5.60, 6.00)),
            ("Reference region", (4.70, 5.30)),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(
                lambda _checked=False, selected=bounds: self._set_view(selected)
            )
            row.addWidget(button)
        self.overlay_checkbox = QtWidgets.QCheckBox("Overlay comparison")
        self.overlay_checkbox.setToolTip(
            "Show Original, Automated and Manual together in one plot."
        )
        self.overlay_checkbox.toggled.connect(self._toggle_overlay)
        row.addWidget(self.overlay_checkbox)
        row.addStretch(1)
        return row

    def _new_plot(self, title: str) -> pg.PlotWidget:
        plot = pg.PlotWidget()
        plot.setBackground("w")
        plot.showGrid(x=True, y=True, alpha=0.18)
        plot.setLabel("bottom", "Chemical shift", units="ppm")
        plot.setLabel("left", "Intensity", units="a.u.")
        plot.getPlotItem().invertX(True)  # normal NMR convention
        plot.setTitle(title)
        return plot

    def _stacked_plots(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)

        self.original_plot = self._new_plot("Original — Before Phase Correction")
        self.original_curve = self.original_plot.plot(
            pen=pg.mkPen(ORIGINAL_PEN, width=1.1),
            autoDownsample=True,
            clipToView=True,
        )
        self.automated_plot = self._new_plot("Automated — Script Result")
        self.automated_curve = self.automated_plot.plot(
            pen=pg.mkPen(AUTOMATED_PEN, width=1.2),
            autoDownsample=True,
            clipToView=True,
        )
        self.manual_plot = self._new_plot("Manual — Current Adjustment")
        self.manual_curve = self.manual_plot.plot(
            pen=pg.mkPen(MANUAL_PEN, width=1.2),
            autoDownsample=True,
            clipToView=True,
        )

        for plot, label_attr in (
            (self.original_plot, "original_phase_label"),
            (self.automated_plot, "automated_phase_label"),
            (self.manual_plot, "manual_phase_label"),
        ):
            layout.addWidget(plot, 1)
            label = QtWidgets.QLabel()
            label.setStyleSheet("font-family: monospace; font-size: 12px;")
            setattr(self, label_attr, label)
            layout.addWidget(label)

        # One shared ppm axis: panning or zooming any plot moves all three.
        self.automated_plot.setXLink(self.original_plot)
        self.manual_plot.setXLink(self.original_plot)
        return container

    def _overlay_plot(self) -> QtWidgets.QWidget:
        container = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        self.overlay_plot = self._new_plot("Overlay — Original / Automated / Manual")
        self.overlay_plot.addLegend()
        self.overlay_original = self.overlay_plot.plot(
            pen=pg.mkPen(ORIGINAL_PEN, width=1.0, style=QtCore.Qt.DotLine),
            name="Original",
            autoDownsample=True,
            clipToView=True,
        )
        self.overlay_automated = self.overlay_plot.plot(
            pen=pg.mkPen(AUTOMATED_PEN, width=1.2, style=QtCore.Qt.DashLine),
            name="Automated",
            autoDownsample=True,
            clipToView=True,
        )
        self.overlay_manual = self.overlay_plot.plot(
            pen=pg.mkPen(MANUAL_PEN, width=1.2),
            name="Manual",
            autoDownsample=True,
            clipToView=True,
        )
        self.overlay_plot.setXLink(self.original_plot)
        layout.addWidget(self.overlay_plot, 1)
        return container

    def _manual_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Manual Phase")
        grid = QtWidgets.QGridLayout(box)
        self.p0_slider, self.p0_value = self._phase_control(grid, 0, "Phase 0")
        self.p1_slider, self.p1_value = self._phase_control(grid, 1, "Phase 1")
        grid.addWidget(QtWidgets.QLabel("Pivot ppm"), 2, 0)
        self.pivot = QtWidgets.QDoubleSpinBox(
            decimals=6, minimum=-100, maximum=100
        )
        self.pivot.setSingleStep(0.01)
        self.pivot.valueChanged.connect(self._update_plots)
        grid.addWidget(self.pivot, 2, 1)
        reset = QtWidgets.QPushButton("Reset to stored phase")
        reset.clicked.connect(self._reset_stored)
        grid.addWidget(reset, 3, 0, 1, 2)
        self.copy_button = QtWidgets.QPushButton("Copy Automated → Manual")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_automated_to_manual)
        grid.addWidget(self.copy_button, 4, 0, 1, 2)
        return box

    def _phase_control(self, layout, row: int, label: str):
        layout.addWidget(QtWidgets.QLabel(label), row, 0)
        slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        slider.setRange(-1800, 1800)
        slider.setSingleStep(1)
        slider.valueChanged.connect(self._update_plots)
        layout.addWidget(slider, row, 1)
        value = QtWidgets.QLabel()
        value.setMinimumWidth(70)
        layout.addWidget(value, row, 2)
        return slider, value

    def _candidate_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Saved Phase Corrections (Manual)")
        layout = QtWidgets.QVBoxLayout(box)
        self.candidate_list = QtWidgets.QListWidget()
        self.candidate_list.currentRowChanged.connect(self._candidate_selected)
        layout.addWidget(self.candidate_list)
        first = QtWidgets.QHBoxLayout()
        for label, slot in (
            ("Save Candidate", self._save_candidate),
            ("Rename", self._rename_candidate),
            ("Delete", self._delete_candidate),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            first.addWidget(button)
        layout.addLayout(first)
        self.save_automated_candidate_button = QtWidgets.QPushButton(
            "Save Automated as Candidate"
        )
        self.save_automated_candidate_button.setEnabled(False)
        self.save_automated_candidate_button.clicked.connect(
            self._save_automated_candidate
        )
        layout.addWidget(self.save_automated_candidate_button)
        second = QtWidgets.QHBoxLayout()
        for label, slot in (
            ("Save Phase Set", self._save_phase_set),
            ("Load Phase Set", self._load_phase_set),
        ):
            button = QtWidgets.QPushButton(label)
            button.clicked.connect(slot)
            second.addWidget(button)
        layout.addLayout(second)
        self.candidate_status = QtWidgets.QLabel()
        self.candidate_status.setWordWrap(True)
        layout.addWidget(self.candidate_status)
        return box

    def _information_group(self) -> QtWidgets.QGroupBox:
        box = QtWidgets.QGroupBox("Information")
        layout = QtWidgets.QVBoxLayout(box)
        self.information = QtWidgets.QPlainTextEdit(readOnly=True)
        self.information.setStyleSheet("font-family: monospace; font-size: 12px;")
        layout.addWidget(self.information)
        return box

    # -- state -----------------------------------------------------------

    def _load_model_into_controls(self) -> None:
        for widget in (self.p0_slider, self.p1_slider, self.pivot):
            widget.blockSignals(True)
        self.p0_slider.setValue(round(self.model.stored_p0 * 10))
        self.p1_slider.setValue(round(self.model.stored_p1 * 10))
        self.pivot.setValue(self.model.stored_pivot_ppm)
        for widget in (self.p0_slider, self.p1_slider, self.pivot):
            widget.blockSignals(False)
        self.original_curve.setData(self.model.ppm, self.model.original_spectrum)
        self.overlay_original.setData(self.model.ppm, self.model.original_spectrum)
        self.original_phase_label.setText(
            "Original: no phase correction applied (raw FFT)"
        )
        self._update_plots()
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
        self._update_plots()

    def _set_automated(
        self, result: AutomatedPhaseResult | None, note: str = ""
    ) -> None:
        self.automated = result
        if result is None:
            self.automated_trace = None
            self.automated_provenance = ""
            self.automated_curve.setData([], [])
            self.overlay_automated.setData([], [])
        else:
            ppm, real, provenance = automated_spectrum(result, self.model)
            self.automated_trace = (ppm, real)
            self.automated_provenance = provenance
            self.automated_curve.setData(ppm, real)
            self.overlay_automated.setData(ppm, real)
        self.copy_button.setEnabled(result is not None)
        self.save_automated_candidate_button.setEnabled(result is not None)
        self.save_review_button.setEnabled(result is not None)
        self._update_plots()
        if note:
            self.status.setText(note)

    def _update_plots(self, _value=None) -> None:
        p0_deg, p1_deg, pivot_ppm = self._current_values()
        manual = self.model.phased(p0_deg, p1_deg, pivot_ppm)
        self.manual_curve.setData(self.model.ppm, manual)
        self.overlay_manual.setData(self.model.ppm, manual)
        self.p0_value.setText(f"{p0_deg:.1f}°")
        self.p1_value.setText(f"{p1_deg:.1f}°")
        self.manual_phase_label.setText(
            f"Manual:    P0 = {p0_deg:.1f}° | P1 = {p1_deg:.1f}° | "
            f"Pivot = {pivot_ppm:.6f} ppm"
        )
        if self.automated is None:
            self.automated_phase_label.setText(
                "Automated: not run yet — click Run Automated Processing, "
                "or open a run that already has a result."
            )
        else:
            provenance = (
                "pipeline spectrum CSV"
                if self.automated_provenance == SPECTRUM_FROM_CSV
                else "rebuilt from the phase recorded in the pipeline summary"
            )
            self.automated_phase_label.setText(
                f"Automated: {self.automated.phase_text()}  "
                f"[{self.automated.phase_method}; trace from {provenance}]"
            )
        self._refresh_information()
        self._refresh_candidate_state()

    def _refresh_information(self) -> None:
        p0_deg, p1_deg, pivot_ppm = self._current_values()
        lines = [
            f"Source .dx        : {self.model.path}",
            f"Dataset           : {self.model.dataset_display_name}",
            f"Runs root         : {self.runs_root}",
            f"Output directory  : {self.output_directory or 'not selected'}",
            "",
            f"Manual   phase    : P0 {p0_deg:.1f}°  P1 {p1_deg:.1f}°  "
            f"pivot {pivot_ppm:.6f} ppm",
        ]
        if self.automated is None:
            lines.append("Automated phase   : no automated result loaded")
        else:
            lines.extend(
                [
                    f"Automated phase   : P0 {self.automated.p0_deg:.1f}°  "
                    f"P1 {self.automated.p1_deg:.1f}°  "
                    f"(index-zero convention, {self.automated.phase_method})",
                    f"Entry point       : {self.automated.entry_point}",
                    f"Analysis output   : {self.automated.output_directory}",
                    f"Summary           : {self.automated.summary_path.name}",
                    f"Processed at      : {self.automated.processed_at or 'unknown'}",
                    f"Reference shift   : "
                    f"{self.automated.reference_shift_ppm:.6f} ppm",
                    f"Automated trace   : {self.automated_provenance}",
                ]
            )
        lines.append("")
        lines.append("Review only — no instrument, pump or needle is contacted.")
        lines.append("The source .dx is never modified.")
        self.information.setPlainText("\n".join(lines))

    def _refresh_candidate_state(self) -> None:
        p0_deg, p1_deg, pivot_ppm = self._current_values()
        self.candidate_status.setText(
            self.phase_candidates.state_text(p0_deg, p1_deg, pivot_ppm)
        )

    def _rebuild_candidate_list(self) -> None:
        self._updating_candidate_list = True
        try:
            self.candidate_list.clear()
            for candidate in self.phase_candidates.candidates:
                self.candidate_list.addItem(
                    f"{candidate.name} — P0 {candidate.p0_deg:.1f}°, "
                    f"P1 {candidate.p1_deg:.1f}°, pivot {candidate.pivot_ppm:.4f}"
                )
            index = self.phase_candidates.selected_index
            self.candidate_list.setCurrentRow(-1 if index is None else index)
        finally:
            self._updating_candidate_list = False
        self._refresh_candidate_state()

    # -- actions ---------------------------------------------------------

    def _toggle_overlay(self, checked: bool) -> None:
        self.overlay_widget.setVisible(checked)
        # Stacked plots stay available; overlay is an addition, not a
        # replacement.
        self.stacked_widget.setVisible(True)

    def _set_view(self, bounds) -> None:
        if bounds is None:
            low, high = float(np.min(self.model.ppm)), float(np.max(self.model.ppm))
        else:
            low, high = sorted(bounds)
        self.original_plot.setXRange(low, high, padding=0.02)
        traces = [
            (self.original_plot, self.model.ppm, self.model.original_spectrum),
            (
                self.manual_plot,
                self.model.ppm,
                self.model.phased(*self._current_values()),
            ),
        ]
        if self.automated_trace is not None:
            traces.append(
                (self.automated_plot, self.automated_trace[0], self.automated_trace[1])
            )
        for plot, ppm, values in traces:
            mask = (ppm >= low) & (ppm <= high)
            visible = np.asarray(values)[mask]
            if visible.size:
                bottom, top = np.percentile(visible, (0.5, 99.5))
                padding = max(0.08 * float(top - bottom), 1.0)
                plot.setYRange(float(bottom - padding), float(top + padding))
        self.overlay_plot.enableAutoRange(axis="y")

    def _open_file(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open NMReady JCAMP-DX file",
            str(self.model.path.parent),
            "JCAMP-DX files (*.dx);;All files (*)",
        )
        if filename:
            self._load_source(Path(filename))

    def _load_source(
        self, path: Path, automated: AutomatedPhaseResult | None = None
    ) -> None:
        try:
            self.model.load(path)
        except Exception as exc:  # noqa: BLE001 - surfaced to the chemist
            QtWidgets.QMessageBox.critical(self, "Could not open DX file", str(exc))
            return
        self.phase_candidates.clear()
        self._rebuild_candidate_list()
        self._load_model_into_controls()
        self._set_automated(automated)
        if automated is None:
            self.status.setText(
                f"Loaded {path.name}. Original spectrum shown; no automated "
                "result yet."
            )

    def _browse_runs(self) -> None:
        dialog = RunBrowserDialog(self.runs_root, self)
        if dialog.exec() != QtWidgets.QDialog.DialogCode.Accepted:
            return
        acquisition = dialog.selected
        self.runs_root = dialog.root
        if acquisition is None:
            return
        automated: AutomatedPhaseResult | None = None
        note = ""
        if acquisition.has_automated_result:
            try:
                automated = automated_result_from_summary(
                    acquisition.summary_path,
                    source_filename=acquisition.source_dx.name,
                    search_roots=[acquisition.run_directory],
                )
                note = (
                    f"Loaded the existing automated result from "
                    f"{acquisition.analysis_directory}. Nothing was recomputed."
                )
            except Exception as exc:  # noqa: BLE001
                QtWidgets.QMessageBox.warning(
                    self,
                    "Automated result could not be loaded",
                    f"{exc}\n\nThe original spectrum is still available.",
                )
        else:
            note = (
                f"{acquisition.source_dx.name} has no processed result yet. "
                "Run Automated Processing to create one."
            )
        self._load_source(acquisition.source_dx, automated)
        if note:
            self.status.setText(note)

    def _open_phase_review(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Open a saved phase review",
            str(self.output_directory or REPO_ROOT),
            "Phase review files (*.json);;All files (*)",
        )
        if not filename:
            return
        try:
            review = load_phase_review(
                Path(filename), search_roots=[self.runs_root]
            )
        except SourceDxNotFound as exc:
            QtWidgets.QMessageBox.critical(
                self, "Source .dx file not found", str(exc)
            )
            return
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self, "Could not open phase review", str(exc)
            )
            return
        self._load_source(review.source_dx, review.automated)
        for candidate in review.candidates:
            self.phase_candidates.candidates.append(candidate)
        if review.candidates:
            self.phase_candidates.selected_index = None
        self._rebuild_candidate_list()
        if review.manual is not None:
            self._set_phase_controls(review.manual)
        self.status.setText(
            f"Restored review saved {review.created_at or 'at an unknown time'}: "
            f"original and automated states are back."
        )

    def _choose_output_directory(self) -> None:
        chosen = QtWidgets.QFileDialog.getExistingDirectory(
            self,
            "Select an output directory for review results",
            str(self.output_directory or REPO_ROOT),
        )
        if chosen:
            self.output_directory = Path(chosen)
            self._refresh_information()
            self.status.setText(f"Output directory: {self.output_directory}")

    def _run_automated(self) -> None:
        if self._busy:
            return
        if self.output_directory is None:
            QtWidgets.QMessageBox.information(
                self,
                "Choose an output directory",
                "Select an output directory first. The automated processing "
                "writes a new result folder there and never overwrites "
                "production results.",
            )
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "Run automated processing",
            f"Run {PIPELINE_ENTRY_POINT} on:\n\n{self.model.path}\n\n"
            f"Output goes to a new folder under:\n{self.output_directory}\n\n"
            "The source .dx is not modified. Continue?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        self._busy = True
        self.run_button.setEnabled(False)
        self.status.setText("Running the automated processing pipeline…")
        QtWidgets.QApplication.setOverrideCursor(QtCore.Qt.WaitCursor)
        QtWidgets.QApplication.processEvents()
        try:
            result = run_automated_processing(
                source=self.model.path,
                output_root=self.output_directory,
                dataset_display_name=self.model.dataset_display_name,
            )
            self._set_automated(
                result, f"Automated processing complete: {result.output_directory}"
            )
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self, "Automated processing failed", str(exc)
            )
            self.status.setText(f"Automated processing failed: {exc}")
        finally:
            QtWidgets.QApplication.restoreOverrideCursor()
            self._busy = False
            self.run_button.setEnabled(True)

    def _copy_automated_to_manual(self) -> None:
        if self.automated is None:
            return
        candidate = automated_as_candidate(self.automated, self.model)
        self._set_phase_controls(candidate)
        self.status.setText(
            "Manual controls now hold the automated phase. Adjust to fine-tune."
        )

    def _save_candidate(self) -> None:
        p0_deg, p1_deg, pivot_ppm = self._current_values()
        self.phase_candidates.add(p0_deg, p1_deg, pivot_ppm)
        self._rebuild_candidate_list()

    def _save_automated_candidate(self) -> None:
        if self.automated is None:
            return
        candidate = automated_as_candidate(self.automated, self.model)
        self.phase_candidates.add(
            candidate.p0_deg, candidate.p1_deg, candidate.pivot_ppm, name="Automated"
        )
        self._rebuild_candidate_list()

    def _candidate_selected(self, row: int) -> None:
        if self._updating_candidate_list or row < 0:
            return
        try:
            candidate = self.phase_candidates.select(row)
        except IndexError as exc:
            QtWidgets.QMessageBox.critical(self, "Could not select phase", str(exc))
            return
        self._set_phase_controls(candidate)

    def _delete_candidate(self) -> None:
        row = self.candidate_list.currentRow()
        if row < 0:
            return
        self.phase_candidates.delete(row)
        self._rebuild_candidate_list()
        selected = self.phase_candidates.selected
        if selected is not None:
            self._set_phase_controls(selected)

    def _rename_candidate(self) -> None:
        row = self.candidate_list.currentRow()
        if row < 0:
            return
        name, accepted = QtWidgets.QInputDialog.getText(
            self,
            "Rename phase candidate",
            "New name:",
            text=self.phase_candidates.candidates[row].name,
        )
        if not accepted:
            return
        try:
            self.phase_candidates.rename(row, name)
        except ValueError as exc:
            QtWidgets.QMessageBox.critical(
                self, "Could not rename candidate", str(exc)
            )
            return
        self._rebuild_candidate_list()

    def _save_phase_set(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self,
            "Save phase set",
            str((self.output_directory or REPO_ROOT) / "phase-set.json"),
            "Phase set files (*.json)",
        )
        if not filename:
            return
        try:
            self.phase_candidates.save(
                Path(filename), self.model.path, self.model.dataset_display_name
            )
            self.status.setText(f"Phase set saved: {filename}")
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Could not save phase set", str(exc))

    def _load_phase_set(self) -> None:
        filename, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "Load phase set",
            str(self.output_directory or REPO_ROOT),
            "Phase set files (*.json);;All files (*)",
        )
        if not filename:
            return
        try:
            self.phase_candidates.load(Path(filename), self.model.path)
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(self, "Could not load phase set", str(exc))
            return
        self._rebuild_candidate_list()
        selected = self.phase_candidates.selected
        if selected is not None:
            self._set_phase_controls(selected)

    def _save_phase_review(self) -> None:
        if self.automated is None:
            QtWidgets.QMessageBox.information(
                self,
                "No automated result",
                "Run or load an automated result before saving a review.",
            )
            return
        default = (self.output_directory or REPO_ROOT) / (
            f"{self.model.path.stem}_phase_review.json"
        )
        filename, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save phase review", str(default), "Phase review files (*.json)"
        )
        if not filename:
            return
        p0_deg, p1_deg, pivot_ppm = self._current_values()
        try:
            written = save_phase_review(
                Path(filename),
                model=self.model,
                automated=self.automated,
                manual=PhaseCandidate("Manual", p0_deg, p1_deg, pivot_ppm),
                candidates=self.phase_candidates,
            )
            self.status.setText(f"Phase review saved: {written}")
        except Exception as exc:  # noqa: BLE001
            QtWidgets.QMessageBox.critical(
                self, "Could not save phase review", str(exc)
            )

    def _reset_stored(self) -> None:
        self._set_phase_controls(
            PhaseCandidate(
                "Stored",
                self.model.stored_p0,
                self.model.stored_p1,
                self.model.stored_pivot_ppm,
            )
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "spectrum",
        nargs="?",
        type=Path,
        default=None,
        help="optional NMReady JCAMP-DX file",
    )
    parser.add_argument(
        "--runs-root",
        type=Path,
        default=DEFAULT_RUNS_ROOT,
        help="root directory of completed automated runs",
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

    spectrum = args.spectrum
    if spectrum is None:
        # The audited demo file is a convenience, not a requirement: without it
        # the chemist is asked to pick a file instead of the app refusing to
        # start.
        spectrum = DEFAULT_DX if DEFAULT_DX.is_file() else None
    if spectrum is None:
        chosen, _ = QtWidgets.QFileDialog.getOpenFileName(
            None,
            "Select an NMReady JCAMP-DX file to review",
            str(args.runs_root if args.runs_root.exists() else REPO_ROOT),
            "JCAMP-DX files (*.dx);;All files (*)",
        )
        if not chosen:
            print(
                "No .dx file selected. Pass one as an argument: "
                "python scripts/nmr/phase4.py <file.dx>"
            )
            return 2
        spectrum = Path(chosen)

    window = Phase4Window(spectrum, runs_root=args.runs_root)
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
