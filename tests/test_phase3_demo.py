"""Focused tests for the Phase 3 phase-comparison and analysis handoff."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PySide6 import QtWidgets


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

REPO_ROOT = Path(__file__).resolve().parents[1]
NMR_SCRIPTS = REPO_ROOT / "scripts/nmr"
sys.path.insert(0, str(NMR_SCRIPTS))

import phase3  # noqa: E402
from chemyx_lab.analysis.nmr import build_processing_inspection  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app


def test_phase_candidate_collection_switch_delete_modify_and_persist(tmp_path):
    phases = phase3.PhaseCandidateCollection()
    first = phases.add(12.3, -45.6, 5.812345)
    second = phases.add(-7.8, 90.1, 4.701234)

    assert phases.select(0) == first
    assert phases.selected.p0_deg == 12.3
    assert phases.selected.p1_deg == -45.6
    assert phases.selected.pivot_ppm == 5.812345
    assert phases.select(1) == second
    assert phases.select(0) == first
    assert phases.state_text(12.3, -45.6, 5.812345) == "Candidate 1"
    assert phases.state_text(12.4, -45.6, 5.812345) == "Candidate 1 — Modified"

    phase_set = tmp_path / "saved-phases.json"
    phases.save(phase_set, phase3.DEFAULT_DX, "Demo Dataset")
    restored = phase3.PhaseCandidateCollection()
    restored.load(phase_set, phase3.DEFAULT_DX)
    assert restored.candidates == [first, second]
    assert restored.selected == first

    assert restored.delete(0) == first
    assert restored.candidates == [second]
    assert restored.selected == second


@pytest.fixture(scope="module")
def model():
    return phase3.PhaseSpectrumModel(phase3.DEFAULT_DX)


def test_phase3_manual_phase_is_applied_once_from_raw_fid(model):
    candidate = phase3.PhaseCandidate("Candidate 1", 18.2, -31.4, 5.8)
    effective_p0 = model.effective_p0(
        candidate.p0_deg, candidate.p1_deg, candidate.pivot_ppm
    )
    authoritative = build_processing_inspection(
        phase3.DEFAULT_DX,
        line_broadening_hz=phase3.LINE_BROADENING_HZ,
        zero_fill_points=phase3.ZERO_FILL_POINTS,
        phase0_deg=effective_p0,
        phase1_deg=candidate.p1_deg,
        inverse_phase=True,
    )
    np.testing.assert_allclose(
        model.phased(candidate.p0_deg, candidate.p1_deg, candidate.pivot_ppm),
        np.real(authoritative.phased_spectrum),
    )

    production = build_processing_inspection(
        phase3.DEFAULT_DX,
        line_broadening_hz=phase3.LINE_BROADENING_HZ,
        zero_fill_points=phase3.ZERO_FILL_POINTS,
        inverse_phase=True,
    )
    assert production.phase0_deg == model.production_p0
    assert production.phase1_deg == model.production_p1
    np.testing.assert_allclose(np.real(production.phased_spectrum), model.production_spectrum)


def test_analysis_handoff_passes_selected_phase_and_never_overwrites(
    tmp_path, model
):
    calls: list[list[str]] = []

    def fake_pipeline(arguments: list[str]) -> int:
        calls.append(arguments)
        output_root = Path(arguments[arguments.index("--output-dir") + 1])
        run_name = arguments[arguments.index("--run-name") + 1]
        run_dir = output_root / run_name
        assert not run_dir.exists()
        run_dir.mkdir()
        parameters = {
            "phase_method": arguments[arguments.index("--phase-method") + 1],
            "phase0": float(arguments[arguments.index("--phase0") + 1]),
            "phase1": float(arguments[arguments.index("--phase1") + 1]),
            "baseline_method": "asymmetric_least_squares",
            "reference_method": "metadata",
        }
        (run_dir / f"{run_name}_summary.json").write_text(
            json.dumps({"parameters": parameters, "processing_order": ["raw FID"]}),
            encoding="utf-8",
        )
        return 0

    fixed_time = datetime(2026, 9, 17, 12, 0, 0, 123456)
    first = phase3.PhaseCandidate("Candidate 1", 10.0, -20.0, 5.8)
    first_effective = model.effective_p0(first.p0_deg, first.p1_deg, first.pivot_ppm)
    result1 = phase3.run_selected_phase_analysis(
        source=phase3.DEFAULT_DX,
        output_root=tmp_path,
        dataset_display_name=model.dataset_display_name,
        candidate=first,
        effective_p0_deg=first_effective,
        pipeline_runner=fake_pipeline,
        when=fixed_time,
    )

    second = phase3.PhaseCandidate("Candidate 2", -30.0, 40.0, 4.9)
    second_effective = model.effective_p0(
        second.p0_deg, second.p1_deg, second.pivot_ppm
    )
    result2 = phase3.run_selected_phase_analysis(
        source=phase3.DEFAULT_DX,
        output_root=tmp_path,
        dataset_display_name=model.dataset_display_name,
        candidate=second,
        effective_p0_deg=second_effective,
        pipeline_runner=fake_pipeline,
        when=fixed_time,
    )

    assert result1.output_directory.parent == tmp_path.resolve()
    assert result2.output_directory.parent == tmp_path.resolve()
    assert result1.output_directory != result2.output_directory
    assert result2.output_directory.name.endswith("_2")
    for call, expected_p0, expected_p1 in (
        (calls[0], first_effective, first.p1_deg),
        (calls[1], second_effective, second.p1_deg),
    ):
        assert call[0] == str(phase3.DEFAULT_DX.resolve())
        assert call.count(str(phase3.DEFAULT_DX.resolve())) == 1
        assert call[call.index("--phase-method") + 1] == "manual"
        assert "--direct-phase" not in call
        assert float(call[call.index("--phase0") + 1]) == expected_p0
        assert float(call[call.index("--phase1") + 1]) == expected_p1

    provenance = json.loads(result2.provenance_path.read_text(encoding="utf-8"))
    assert provenance["selected_phase_candidate"] == "Candidate 2"
    assert provenance["phase"]["p0_deg_at_pivot"] == second.p0_deg
    assert provenance["phase"]["p1_deg"] == second.p1_deg
    assert provenance["phase"]["pivot_ppm"] == second.pivot_ppm
    assert provenance["pipeline"]["processing_settings"]["phase_method"] == "manual"
    assert provenance["pipeline"]["baseline_settings"] == {
        "method": "asymmetric_least_squares",
        "asls_smoothness": 1e6,
        "asls_asymmetry": 0.001,
        "asls_iterations": 10,
        "regional_polynomial_order": None,
    }
    assert provenance["pipeline"]["referencing_settings"]["reference_method"] == (
        "metadata"
    )


def test_window_switching_restores_controls_and_marks_modified(qapp):
    window = phase3.Phase3Window(phase3.DEFAULT_DX)
    try:
        window.p0_slider.setValue(111)
        window.p1_slider.setValue(-222)
        window.pivot.setValue(5.812345)
        window._save_candidate()
        first = window.phase_candidates.selected

        window.p0_slider.setValue(-333)
        window.p1_slider.setValue(444)
        window.pivot.setValue(4.712345)
        window._save_candidate()
        second = window.phase_candidates.selected

        window.candidate_list.setCurrentRow(0)
        assert window._current_values() == pytest.approx(
            (first.p0_deg, first.p1_deg, first.pivot_ppm)
        )
        window.candidate_list.setCurrentRow(1)
        assert window._current_values() == pytest.approx(
            (second.p0_deg, second.p1_deg, second.pivot_ppm)
        )
        window.candidate_list.setCurrentRow(0)
        window.p0_slider.setValue(window.p0_slider.value() + 1)
        assert window.candidate_status.text() == "Candidate 1 — Modified"

        window._delete_candidate()
        assert len(window.phase_candidates.candidates) == 1
    finally:
        window.close()
