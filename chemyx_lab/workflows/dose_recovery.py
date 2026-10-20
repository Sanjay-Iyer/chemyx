"""Explicit retirement of old dose admissions; never erase delivery evidence."""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from ..runtime_state import replay_journal, write_json_atomic
from .dose_guard import validate_experiment_id


def reconciliation_command(experiment_id, status="RESERVED", *, journal=None,
                           config="configs/chemyx/manual_control.yaml", machine_config=None):
    """The reconcile-dose form this record can accept: unused only for RESERVED.

    Without a known journal the flag is omitted (reconcile accepts that), so the
    printed command runs as-is in PowerShell instead of carrying a <placeholder>.
    """
    resolution = "unused" if status == "RESERVED" else "retired"
    journal = f' --journal "{journal}"' if journal else ""
    machine = f' --machine-config "{machine_config}"' if machine_config else ""
    return ("python scripts/chemyx_control.py reconcile-dose --channel 2 --live "
            f"--experiment-id {experiment_id}{journal} "
            f'--resolution {resolution} --config "{config}"{machine}')


def find_run_journal(results_root, run_id):
    """The dose record's own run journal in the standard live result folders.

    Fixed-time runs use results/fixed_time/live/<run_id>/operation_journal.jsonl;
    production experiments use results/experiments/<run_id>/logs/experiment_journal.jsonl.
    """
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,120}", run_id):
        return None
    from ..experiment_paths import journal_path
    found = []
    for parent in ("fixed_time/live", "experiments"):
        run = Path(results_root) / parent / run_id
        try:
            if run.is_dir() and journal_path(run).is_file():
                found.append(journal_path(run))
        except OSError:
            continue
    return found[0] if len(found) == 1 else None


def reconcile(root, experiment_id, journal_path, resolution):
    """Caller holds the shared pump lock, confirms STOP and operator facts.

    `unused` requires an intact RESERVED admission and no dispatch evidence.
    A missing/empty journal is allowed only for that pre-dispatch state: the
    canonical DISPATCH_INTENT barrier must be durable before any dose command.
    `retired` also permits unavailable/corrupt old evidence, because it requires
    retiring the reactor rather than making a claim about delivery completion.
    The original status and all command evidence remain in the canonical file.
    """
    path = Path(root) / f"{validate_experiment_id(experiment_id)}.json"
    original = path.read_bytes()
    try:
        data = json.loads(original.decode("utf-8"))
    except (ValueError, UnicodeError):
        data = None
    intact = (isinstance(data, dict) and data.get("schema") == "chemyx.si6-dose.v1"
              and data.get("experiment_id") == experiment_id and isinstance(data.get("run_id"), str) and bool(data["run_id"])
              and data.get("status") in {"RESERVED", "DISPATCH_INTENT", "CONFIRMED"})
    original_status = data["status"] if intact else "UNREADABLE"
    journal_path = Path(journal_path).resolve() if journal_path is not None else None
    replay = None
    journal_problem = None
    try:
        if journal_path is not None and journal_path.is_file() and journal_path.stat().st_size:
            replay = replay_journal(journal_path)
    except Exception as exc:
        journal_problem = f"{type(exc).__name__}: {exc}"
    records = replay.records if replay is not None else []
    journal_valid = (replay is not None and replay.valid and
                     all(r.get("run_id") == (data or {}).get("run_id") for r in records)) if intact else False
    dispatched = [r for r in records if r.get("event_type") == "dose_dispatch_intent" or
                  (r.get("channel") == 2 and r.get("operation_type") == "infuse" and
                   r.get("lifecycle_state") in {"dispatch_started", "completed", "uncertain"})]
    if resolution == "unused":
        if original_status != "RESERVED" or dispatched:
            raise ValueError("Dose may have been dispatched. unused is refused; isolate/retire the old reaction and use --resolution retired only for an intentionally NEW reaction")
        if journal_problem is not None or replay is not None and not journal_valid:
            raise ValueError("Journal must be valid and belong to the dose record's run_id; unused is refused; preserve evidence and use retired only after retiring the old reactor")
    elif resolution != "retired":
        raise ValueError("resolution must be unused or retired")
    evidence = {"resolution": resolution, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "journal": str(journal_path) if journal_path is not None else None,
                "journal_evidence": "valid" if journal_valid else "missing_or_unreadable",
                "journal_read_error": journal_problem,
                "operator_verified": True,
                "physical_facts": ("Old run stopped; no reagent delivered; syringe and paths physically verified" if resolution == "unused" else
                                   "Old reactor isolated and retired; reagent syringe serviced for a new reaction; no continuation of old chemistry"),
                "original_status": original_status, "automatic_replay_allowed": False}
    if not intact:
        # Preserve exact bytes, including invalid JSON, before replacing the
        # admission with an explicit retired-evidence record. Never erase it.
        backup = path.with_name(f"{path.name}.unreadable-{uuid4().hex}.bak")
        with backup.open("xb") as stream:
            stream.write(original)
            stream.flush()
            os.fsync(stream.fileno())
        evidence["original_record_backup"] = str(backup)
        data = {"schema": "chemyx.si6-dose.v1", "experiment_id": experiment_id,
                "status": "UNREADABLE", "automatic_replay_allowed": False,
                "preserved_fields": data if isinstance(data, dict) else None}
    prior = data.get("reconciliation_history", [])
    history = list(prior) if isinstance(prior, list) else []
    history.append(evidence)
    write_json_atomic(path, dict(data, reconciliation=evidence, reconciliation_history=history))
    return path
