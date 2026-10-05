"""Lightweight existing-file GUI checks; no event-loop launch or hardware."""
import importlib
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from chemyx_lab import config

SPECTRUM = config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"


@pytest.mark.parametrize("module_name,window_name", [
    ("phase1", "PhaseDemoWindow"),
    ("desktop_nmr_phase_demo", "PhaseDemoWindow"),
    ("phase2", "Phase2Window"),
    ("phase3", "Phase3Window"),
    ("phase4", "Phase4Window"),
])
def test_qt_gui_imports_and_loads_bundled_dx(module_name, window_name, monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(config.REPO_ROOT / "scripts/nmr"))
    from PySide6 import QtWidgets
    from chemyx_lab.instruments.nmr import NmrRpcClient
    from chemyx_lab.instruments.chemyx import Pump
    def forbidden(*args, **kwargs):
        pytest.fail("GUI attempted instrument contact")
    monkeypatch.setattr(NmrRpcClient, "_request", forbidden)
    monkeypatch.setattr(Pump, "connect", forbidden)
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    module = importlib.import_module(module_name)
    window_type = getattr(module, window_name)
    window = window_type(SPECTRUM, runs_root=tmp_path) if module_name == "phase4" else window_type(SPECTRUM)
    try:
        app.processEvents()
        assert window.model.path == SPECTRUM.resolve()
        ppm = window.model.base_ppm if module_name == "phase2" else window.model.ppm
        assert ppm.size == 65536
    finally:
        window.close()


def test_matplotlib_explorer_loads_saved_dx_offline(monkeypatch):
    monkeypatch.syspath_prepend(str(config.REPO_ROOT / "scripts/nmr"))
    module = importlib.import_module("interactive_processing_explorer")
    explorer = module.NmrProcessingExplorer(SPECTRUM)
    result = explorer.compute()
    assert result.ppm.size == 65536
    assert result.corrected.size == result.ppm.size
