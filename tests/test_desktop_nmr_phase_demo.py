from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts/nmr/desktop_nmr_phase_demo.py"
DEFAULT_DX = (
    REPO_ROOT
    / "results/runs/automated/chemyx_demo_081026_v3"
    / "20260810_171441_si6/raw_nmr"
    / "20260810_171806_081626_phsi4_0001_8scan_gain12.dx"
)
PRIMARY_DX = (
    REPO_ROOT
    / "results/runs/automated/chemyx_demo_081026_v3"
    / "20260810_154505_si6/raw_nmr"
    / "20260810_154822_081626_phsi4_0001_8scan_gain12.dx"
)


def _module():
    spec = importlib.util.spec_from_file_location("desktop_nmr_phase_demo", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.skipif(
    not (DEFAULT_DX.is_file() and PRIMARY_DX.is_file()),
    reason="the audited demo .dx files are not present in this checkout",
)
def test_real_spectrum_phase_controls_reset_and_second_file_load():
    module = _module()
    model = module.PhaseSpectrumModel(DEFAULT_DX)
    assert model.production_p0 == 5.0
    assert model.production_p1 == -10.0
    np.testing.assert_allclose(
        model.phased(
            model.production_p0,
            model.production_p1,
            model.production_pivot_ppm,
        ),
        model.production_spectrum,
    )
    assert not np.allclose(
        model.phased(25.0, model.production_p1, model.production_pivot_ppm),
        model.production_spectrum,
    )
    assert not np.allclose(
        model.phased(model.production_p0, 30.0, model.production_pivot_ppm),
        model.production_spectrum,
    )
    model.load(PRIMARY_DX)
    assert model.path == PRIMARY_DX.resolve()
    assert model.ppm.size == 65536
