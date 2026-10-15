import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import pytest
from chemyx_lab import config
from chemyx_lab.release_inventory import source_files, load_inventory

ROOT = config.REPO_ROOT


def test_inventory_complete_excludes_physical_state():
    names = {p.relative_to(ROOT).as_posix() for p in source_files(ROOT)}
    assert set(load_inventory(ROOT)["required_resources"]) <= names
    assert "chemyx_lab/analysis/phase_methods.py" in names
    assert "chemyx_lab/testing/nmr_validation/algorithm_validation.py" in names
    assert not any(n.startswith(("runtime/", "runs/", "rig_backups/", "results/")) for n in names)
    assert not any(n.startswith("offline/resources/") for n in names)


def test_archive_immutable_and_active_entry_migrated():
    for path in (ROOT / "results/100426_algovalidation/code").glob("*.py"):
        original = subprocess.check_output(["git", "show", f"HEAD:{path.relative_to(ROOT).as_posix()}"], cwd=ROOT)
        # Git stores LF; existing checkout may use CRLF.
        assert original.replace(b'\r\n', b'\n') == path.read_bytes().replace(b'\r\n', b'\n')
    entry = (ROOT / "scripts/nmr/run_algorithm_validation.py").read_text()
    assert "results/100426_algovalidation/code" not in entry
    from chemyx_lab.testing.nmr_validation import algorithm_validation as maintained
    assert maintained.ROOT == ROOT
    assert maintained.refinement_module().ROOT == ROOT


@pytest.mark.skipif(not (ROOT / "results/100426_algovalidation/code/algorithm_validation.py").is_file(), reason="Optional immutable archive is not a deployment source dependency")
def test_migrated_numerical_helpers_match_archived():
    from chemyx_lab.testing.nmr_validation import algorithm_validation as maintained
    spec = importlib.util.spec_from_file_location('immutable_validation_core', ROOT / "results/100426_algovalidation/code/algorithm_validation.py")
    archived = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(archived)
    # These helpers feed phase-quality, timeseries and report calculations.
    for a, b in ((1., 2.), (0., 3.), (-4., 2.)):
        assert maintained.change(a, b) == archived.change(a, b)
    import numpy as np
    np.testing.assert_array_equal(maintained.WINDOWS['target'], archived.WINDOWS['target'])


@pytest.mark.skipif(not (ROOT / "results/100426_algovalidation/code/algorithm_validation.py").is_file(), reason="Optional immutable archive is not a deployment source dependency")
def test_migration_preserves_all_scientific_function_bodies():
    import ast
    for name in ("algorithm_validation.py", "robustness_refinement.py"):
        original = ast.parse((ROOT / "results/100426_algovalidation/code" / name).read_text(encoding="utf-8"))
        current = ast.parse((ROOT / "chemyx_lab/testing/nmr_validation" / name).read_text(encoding="utf-8"))
        functions = lambda tree: {n.name: ast.dump(n, include_attributes=False) for n in tree.body
                                  if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name not in {"main", "parse"}}
        assert functions(original) == functions(current)


def test_offline_deep_resource_integrity():
    from chemyx_lab.analysis.phase_methods import deep_inventory
    assert deep_inventory()["files"]["node.exe"]
