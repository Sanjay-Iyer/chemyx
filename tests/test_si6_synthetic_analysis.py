"""Spectrum-to-completion regression checks, including a genuinely low glitch."""
import csv
import json

import pytest

from chemyx_lab.testing import si6_synthetic_analysis as synthetic


@pytest.fixture(scope="module")
def engine():
    return synthetic.SyntheticSpectra()


@pytest.mark.parametrize("case", synthetic.CASES)
def test_spectral_trajectories_and_counterexamples_use_measured_values(tmp_path, engine, case):
    result = synthetic.validate_case(case, 2, tmp_path, engine)
    assert result["completion_iteration"] == synthetic.CASES[case][2]
    assert all(r["measurement_valid"] for r in result["rows"])
    assert any(r["peak_area"] != r["generated_area"] for r in result["rows"])
    with (tmp_path / f"{case}_trace.csv").open(newline="") as handle:
        traces = list(csv.DictReader(handle))
    assert len(traces) == len(result["rows"])
    if case == "stage1_below_threshold_spike":
        assert result["rows"][3]["peak_area"] < result["rows"][0]["peak_area"] * .025
        assert not any(t["completed"] for t in result["trace"])


def test_synthetic_provenance_distinguishes_processed_arrays_from_raw_fid(tmp_path, engine):
    synthetic.validate_case("stage1_low_changing", 2, tmp_path, engine)
    audit = json.loads(next(tmp_path.rglob("processing_metadata.json")).read_text())
    assert audit["simulation_only"] and audit["validation_level"] == 2
    assert "not synthetic raw/FID" in audit["input_kind"]
    assert audit["phase"]["method"] == "synthetic_already_phased"
    assert audit["model"]["grid_points"] == 1025
    assert audit["spectral_evidence_sha256"]


def test_existing_output_is_not_overwritten(tmp_path):
    with pytest.raises(FileExistsError):
        synthetic.validate_all(tmp_path)
