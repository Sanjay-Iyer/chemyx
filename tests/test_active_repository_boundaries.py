import sys
from pathlib import Path

import pytest

from chemyx_lab.workflows import instrument_operations
from chemyx_lab.workflows.si6_automated_nmr import load_si6_config


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_workflow_01_entry_points_are_not_active():
    assert not (REPO_ROOT / "scripts" / "01_first_real_chemyx_nmr.py").exists()
    assert not (REPO_ROOT / "scripts" / "first_real_test.py").exists()
    assert not (
        REPO_ROOT / "chemyx_lab" / "workflows" / "first_real_chemyx_nmr.py"
    ).exists()
    assert not (
        REPO_ROOT / "configs" / "experiments" / "01_first_real_chemyx_nmr.yaml"
    ).exists()


def test_archived_workflow_is_isolated_from_active_imports():
    active_python = list((REPO_ROOT / "chemyx_lab").rglob("*.py")) + list(
        (REPO_ROOT / "scripts").rglob("*.py")
    )
    for path in active_python:
        text = path.read_text(encoding="utf-8")
        assert "first_real_chemyx_nmr" not in text, path
        assert "archive.legacy_workflows" not in text, path
    assert not any(name.startswith("archive") for name in sys.modules)


def test_active_si6_schema_rejects_legacy_event_language(tmp_path):
    legacy = tmp_path / "legacy.yaml"
    legacy.write_text(
        "\n".join(
            [
                "workflow:",
                "  name: legacy",
                "  sequence:",
                "    - event: W",
                "pump: {}",
                "nmr: {}",
                "analysis: {}",
                "output: {}",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unknown workflow field"):
        load_si6_config(legacy)


def test_shared_helpers_remain_active_without_legacy_module():
    assert instrument_operations.move_seconds(5.0, 5.0, 0) == 60.0
    assert instrument_operations.format_seconds(61) == "1 min 1 s"
