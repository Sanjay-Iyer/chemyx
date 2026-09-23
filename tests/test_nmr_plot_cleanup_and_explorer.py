from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "scripts/nmr"
SERIES = REPO_ROOT / "results/runs/automated/chemyx_demo_081026_v3"
FINAL_PACKAGE = (
    REPO_ROOT
    / "results/nmr_processing_inspection/chemyx_demo_081026_v3_plot_cleanup_v3"
)
# Run data under results/runs is gitignored, so a clean checkout skips these.
requires_series = pytest.mark.skipif(
    not SERIES.is_dir(),
    reason="the audited demo run folder is not present in this checkout",
)


def _load_script(name: str):
    sys.path.insert(0, str(SCRIPT_DIR))
    path = SCRIPT_DIR / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def explorer_module():
    return _load_script("interactive_processing_explorer")


@pytest.fixture(scope="module")
def cleanup_module():
    return _load_script("build_plot_cleanup")


@pytest.fixture(scope="module")
def primary_dx(explorer_module):
    return explorer_module.representative_dx_files(SERIES)[0]


@requires_series
def test_explorer_loads_known_dx_and_reset_restores_production(
    explorer_module, primary_dx
):
    explorer = explorer_module.NmrProcessingExplorer(primary_dx)
    production = explorer.compute()
    assert production.mode == "production"
    assert production.settings.p0 == 5.0
    assert production.settings.p1 == -10.0
    explorer.with_settings(p0=25.0, baseline_method="none")
    assert explorer.compute().mode == "exploratory override"
    assert explorer.reset_to_production() == explorer.production_settings
    assert explorer.compute().mode == "production"


@requires_series
def test_phase_reference_and_baseline_controls_change_expected_arrays(
    explorer_module, primary_dx
):
    explorer = explorer_module.NmrProcessingExplorer(primary_dx)
    production = explorer.compute()
    phase_changed = explorer.compute(
        explorer_module.replace(explorer.production_settings, p0=35.0)
    )
    assert not np.allclose(
        phase_changed.phased_referenced, production.phased_referenced
    )

    shifted = explorer.compute(
        explorer_module.replace(
            explorer.production_settings,
            use_production_reference=False,
            manual_shift_ppm=0.123,
        )
    )
    np.testing.assert_allclose(shifted.ppm - production.ppm, 0.123)
    np.testing.assert_allclose(shifted.corrected, production.corrected)

    no_baseline = explorer.compute(
        explorer_module.replace(explorer.production_settings, baseline_method="none")
    )
    assert np.count_nonzero(production.baseline) > 0
    assert np.all(no_baseline.baseline == 0.0)
    np.testing.assert_allclose(no_baseline.corrected, no_baseline.phased_referenced)
    polynomial = explorer.compute(
        explorer_module.replace(
            explorer.production_settings,
            baseline_method="polynomial",
            polynomial_degree=2,
        )
    )
    arpls = explorer.compute(
        explorer_module.replace(
            explorer.production_settings,
            baseline_method="arPLS",
            baseline_lambda=1e7,
        )
    )
    assert not np.allclose(polynomial.baseline, production.baseline)
    assert not np.allclose(arpls.baseline, production.baseline)


@requires_series
def test_exports_are_restricted_to_exploratory_root(
    explorer_module, primary_dx, tmp_path, monkeypatch
):
    allowed = tmp_path / "interactive"
    monkeypatch.setattr(explorer_module, "DEFAULT_EXPLORATORY_ROOT", allowed)
    explorer = explorer_module.NmrProcessingExplorer(
        primary_dx, exploratory_root=allowed
    )
    settings = explorer.export_settings("pytest_settings.json")
    plot = explorer.export_plot("pytest_snapshot.png")
    assert settings.parent == allowed / "exports"
    assert plot.parent == allowed / "exports"
    assert json.loads(settings.read_text(encoding="utf-8"))["exploratory_only"]
    with pytest.raises(ValueError, match="must remain inside"):
        explorer_module.NmrProcessingExplorer(primary_dx, exploratory_root=SERIES)


@requires_series
def test_grouped_static_families_and_contact_sheet_generate(
    cleanup_module, primary_dx, tmp_path
):
    run = primary_dx.parents[1]
    acquisition = cleanup_module._load_acquisition(run)
    paths = cleanup_module.baseline_families(acquisition, tmp_path, "TEST-RUN-001")
    names = {path.name for path in paths}
    assert "01a_full_spectrum_before_baseline_phased_referenced.png" in names
    assert "01d_full_spectrum_removed_baseline_difference.png" in names
    assert "02b_full_spectrum_estimated_baseline_zoomed_y.png" in names
    assert "03c_product_region_after_baseline_5p60_to_6p00ppm.png" in names
    sheet = cleanup_module.contact_sheet(
        paths[:4],
        tmp_path,
        "TEST-RUN-001",
        "Contact sheet test",
        "contact_sheet.png",
    )
    assert sheet.is_file()


def test_cleanup_generator_refuses_existing_output(cleanup_module, tmp_path):
    with pytest.raises(SystemExit, match="refusing to overwrite"):
        cleanup_module.main([str(SERIES), "--output", str(tmp_path)])


def test_final_package_manifest_and_notebook_are_valid():
    manifest = json.loads((FINAL_PACKAGE / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["dataset_display_name"] == "081026 PhSi4 automated demo v3"
    assert "09_contact_sheets/A_groups_01_02_overview.png" in manifest["files"]
    assert "09_contact_sheets/E_515pull_worked_example.png" in manifest["files"]
    notebook = json.loads(
        (REPO_ROOT / "notebooks/interactive_nmr_processing_explorer.ipynb").read_text(
            encoding="utf-8"
        )
    )
    assert notebook["nbformat"] == 4
    assert any(
        "create_notebook_explorer" in "".join(cell.get("source", []))
        for cell in notebook["cells"]
    )
