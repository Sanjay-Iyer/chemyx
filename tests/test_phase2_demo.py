"""Focused smoke checks for the lightweight Phase 1/Phase 2 desktop demos."""

from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
NMR_SCRIPTS = REPO_ROOT / "scripts/nmr"
sys.path.insert(0, str(NMR_SCRIPTS))

import phase2  # noqa: E402


def test_phase1_is_frozen_copy_of_original_demo():
    assert (NMR_SCRIPTS / "phase1.py").read_bytes() == (
        NMR_SCRIPTS / "desktop_nmr_phase_demo.py"
    ).read_bytes()


@pytest.fixture(scope="module")
def model():
    return phase2.Phase2Model(phase2.DEFAULT_DX)


def test_phase2_processing_controls_and_live_area(model):
    production = model.production_settings
    result = model.process(production)
    assert np.allclose(result.intensity, model.inspection.corrected_real)

    p0 = model.process(replace(production, p0_deg=production.p0_deg + 8.0))
    p1 = model.process(replace(production, p1_deg=production.p1_deg + 8.0))
    pivot = model.process(replace(production, pivot_ppm=5.8))
    assert not np.allclose(p0.intensity, result.intensity)
    assert not np.allclose(p1.intensity, result.intensity)
    assert not np.allclose(pivot.intensity, result.intensity)

    no_baseline = model.process(replace(production, baseline_enabled=False))
    flexible = model.process(replace(production, baseline_smoothness=1e4))
    assert not np.allclose(no_baseline.intensity, result.intensity)
    assert not np.allclose(flexible.baseline, result.baseline)

    shifted = model.process(replace(production, reference_shift_ppm=-0.015))
    assert np.allclose(shifted.intensity, result.intensity)
    assert np.allclose(shifted.ppm, result.ppm - 0.015)

    default_area = model.manual_area(result, production)
    narrower = replace(
        production, integration_left_ppm=5.74, integration_right_ppm=5.84
    )
    assert model.manual_area(result, narrower) != pytest.approx(default_area)


def test_phase2_exports_are_isolated_and_reproducible(model, monkeypatch, tmp_path):
    allowed = tmp_path / "nmr_phase_demo_exports"
    allowed.mkdir()
    monkeypatch.setattr(phase2, "DEFAULT_EXPORT_ROOT", allowed)
    isolated = phase2.Phase2Model(phase2.DEFAULT_DX, export_root=allowed / "smoke")
    settings = isolated.production_settings

    settings_path = isolated.save_settings(settings)
    spectrum_path = isolated.export_spectrum(settings)
    analysis_dir, summary = isolated.run_analysis(settings)

    assert settings_path.is_relative_to(allowed)
    assert spectrum_path.is_relative_to(allowed)
    assert analysis_dir.is_relative_to(allowed)
    payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert payload["exploratory_only"] is True
    assert payload["timestamp_source"] == "LONG DATE header"
    assert spectrum_path.read_text(encoding="utf-8").startswith("ppm,intensity")
    assert (analysis_dir / "settings.json").is_file()
    assert (analysis_dir / "summary.json").is_file()
    assert (analysis_dir / "peaks_simple.csv").is_file()
    assert summary["peak_ppm"] == pytest.approx(5.7916523, abs=1e-5)
    assert summary["manual_integration_area"] == pytest.approx(33.775789, abs=1e-5)
