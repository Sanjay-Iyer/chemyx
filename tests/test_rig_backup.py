"""Mock filesystem evidence; state backup never opens hardware or restores."""
import json
from pathlib import Path
import pytest
from chemyx_lab import rig_backup as rb


def rig(tmp_path):
    root = tmp_path / "rig"
    for name, data in {
        "runs/arduino/needle_state.json": {"version": 1, "position_valid": False, "reason": "UNKNOWN"},
        "runs/arduino/needle_state.json.home_pending.json": {"position_valid": False},
        "runs/arduino/home_journal.jsonl": {"event_type": "historical_position_fault"},
        "runtime/si6_doses/exp1.json": {"experiment_id": "exp1", "reserved": True, "uncertain": True},
        "results/experiments/20261015_exp1/logs/journal.jsonl": {"event_type": "dose_reserved"},
    }.items():
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data))
    return root


def test_inventory_backup_preserves_uncertainty(tmp_path):
    root = rig(tmp_path)
    before = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
    snapshot = rb.inventory(root)
    assert len(snapshot["files"]) == 5
    folder = rb.backup(root, tmp_path / "backups")
    result = rb.verify(folder)
    assert result["schema"] == rb.SCHEMA
    assert any(r["uncertainty"] for r in result["files"] if "uncertainty" in r)
    assert all(r["manual_physical_verification_required"] for r in result["files"])
    assert all(p.read_bytes() == b for p, b in before.items())
    assert all(r["comparison"] == "identical" for r in rb.compare(folder)["files"])


@pytest.mark.parametrize("fault", ["missing", "corrupt", "manifest"])
def test_bad_backup_fails(tmp_path, fault):
    folder = rb.backup(rig(tmp_path), tmp_path / "backups")
    obj = rb.verify(folder)
    p = folder / obj["files"][0]["backup_path"]
    if fault == "missing":
        p.unlink()
    elif fault == "corrupt":
        p.write_text("corrupt")
    else:
        (folder / "backup_manifest.json").write_text('{}')
    with pytest.raises((ValueError, FileNotFoundError)):
        rb.verify(folder)


def test_conflict_software_replacement_and_dose_no_rollback(tmp_path):
    root = rig(tmp_path)
    folder = rb.backup(root, tmp_path / "backups")
    dose = root / "runtime/si6_doses/exp1.json"
    dose.write_text('{"uncertain": true, "newer": true}')
    # Source software replacement is immaterial to backup validity.
    (root / "software.py").write_text("new version")
    result = rb.compare(folder)
    assert not result["automatic_restore_allowed"]
    assert next(r for r in result["files"] if r["original_filename"] == "exp1.json")["comparison"].startswith("conflict")
    assert json.loads(dose.read_text())["newer"]
    second = tmp_path / "other_rig"
    plan = rb.compare(folder, current_root=second)
    assert all(r["comparison"] == "missing_current" for r in plan["files"])
    assert not second.exists()  # no implicit creation/restore/trust


def test_empty_rig_missing_locations_reported(tmp_path):
    result = rb.inventory(tmp_path)
    assert result["missing_locations"] and not result["files"]


def test_dispatch_intent_and_truncated_journal_remain_blocking_evidence(tmp_path):
    root = rig(tmp_path)
    dose = root / "runtime/si6_doses/exp1.json"
    dose.write_text(json.dumps({"schema": "chemyx.si6-dose.v1", "experiment_id": "exp1", "status": "DISPATCH_INTENT"}))
    journal = root / "runs/arduino/home_journal.jsonl"
    journal.write_text('{"schema_version":1,"experiment_id":"exp1"}\n{"truncated":')
    data = rb.inventory(root)
    receipt = next(r for r in data["files"] if r["original_filename"] == "exp1.json")
    assert receipt["uncertainty"] and receipt["blocks_repeat_dosing"]
    log = next(r for r in data["files"] if r["original_filename"] == journal.name)
    assert log["journal_parse_errors"] == 1 and log["experiment_ids"] == ["exp1"]
