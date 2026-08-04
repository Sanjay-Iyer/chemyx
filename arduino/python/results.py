"""Durable staged-test results and prerequisite lookup."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from chemyx_lab.runtime_journal import discover_git_commit
from chemyx_lab.runtime_state import write_json_atomic

from .config import REPO_ROOT, hardware_fingerprint


def create_run_dir(root: str | Path, test_name: str, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    base = Path(root)
    run_dir = base / f"{stamp}_{test_name}"
    suffix = 1
    while run_dir.exists():
        run_dir = base / f"{stamp}_{test_name}_{suffix:02d}"
        suffix += 1
    run_dir.mkdir(parents=True)
    return run_dir


def write_result(
    run_dir: Path,
    *,
    test_name: str,
    mode: str,
    cfg: dict[str, Any],
    passed: bool,
    firmware_version: str | None,
    operator_confirmations: dict[str, Any] | None,
    final_known_device_state: dict[str, Any],
    error: str | None = None,
    event_log: list[dict[str, Any]] | None = None,
    motion_attempted: bool = False,
) -> Path:
    timestamp = datetime.now(timezone.utc).isoformat()
    result = {
        "schema_version": 1,
        "test_name": test_name,
        "timestamp_utc": timestamp,
        "git_commit": discover_git_commit(REPO_ROOT),
        "execution_mode": mode,
        "hardware_configuration_fingerprint": hardware_fingerprint(cfg, test_name),
        "operator_inspection_clearance": cfg.get("safety", {}).get(
            "operator_inspection_clearance"
        ),
        "firmware_version": firmware_version,
        "passed": bool(passed),
        "motion_attempted": bool(motion_attempted),
        "operator_confirmations": operator_confirmations or {},
        "final_known_device_state": final_known_device_state,
        "error": error,
    }
    if event_log is not None:
        write_json_atomic(
            Path(run_dir) / "events.json",
            {"schema_version": 1, "events": event_log},
        )
        result["event_log"] = "events.json"
    path = Path(run_dir) / "result.json"
    write_json_atomic(path, result)
    return path


def matching_live_result(run_root: str | Path, test_name: str, cfg: dict[str, Any]) -> Path | None:
    expected = hardware_fingerprint(cfg, test_name)
    candidates = sorted(Path(run_root).glob("*/result.json"), reverse=True)
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            value.get("test_name") == test_name
            and value.get("execution_mode") == "live"
            and value.get("passed") is True
            and value.get("hardware_configuration_fingerprint") == expected
            and value.get("firmware_version") == cfg.get("firmware", {}).get("version")
        ):
            return path
    return None


def unresolved_live_motion_failure(run_root: str | Path, cfg: dict[str, Any]) -> Path | None:
    """Return the newest matching live motion failure, unless superseded."""
    clearance = cfg.get("safety", {}).get("operator_inspection_clearance")
    motion_tests = {
        "test_02_unloaded_motor",
        "test_03_needle_axis",
        "test_04b_integrated_system",
    }
    candidates = sorted(Path(run_root).glob("*/result.json"), reverse=True)
    for path in candidates:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if (
            value.get("test_name") in motion_tests
            and value.get("execution_mode") == "live"
            and value.get("operator_inspection_clearance") == clearance
            and value.get("passed") is False
            and value.get("motion_attempted") is True
        ):
            return path
    return None
