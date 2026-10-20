"""Durable one-experiment admission and at-most-once reagent dispatch barrier.

No automatic resume/replay is supported within this ledger scope. Reusing a
reservation path is refused, including after a crash before reagent delivery.
The fixed-time runner uses a new results-local ledger on each manually prepared
launch; the older workflow uses its shared physical-experiment ledger.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..runtime_state import write_json_atomic
from ..experiment_paths import safe_experiment_id


class DoseReplayBlocked(RuntimeError):
    pass


def validate_experiment_id(value: str) -> str:
    return safe_experiment_id(value)


def configuration_digest(raw: dict) -> str:
    return hashlib.sha256(json.dumps(raw, sort_keys=True, allow_nan=False).encode()).hexdigest()


class DoseGuard:
    def __init__(self, root: Path, experiment_id: str, raw: dict, run_id: str):
        self.path = root / f"{validate_experiment_id(experiment_id)}.json"
        self.data = {"schema": "chemyx.si6-dose.v1", "experiment_id": experiment_id,
                     "dose_id": experiment_id + ":transition", "configuration_sha256": configuration_digest(raw),
                     "run_id": run_id, "status": "RESERVED", "automatic_replay_allowed": False}

    def reserve(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with self.path.open("x", encoding="utf-8") as handle:
                json.dump(self.data, handle, allow_nan=False)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError as exc:
            raise DoseReplayBlocked(f"Experiment already reserved: {self.path}. Inspect its run and reconcile the rig; automatic restart/dosing is refused.") from exc

    def mark(self, status: str, **evidence):
        allowed = {"RESERVED": "DISPATCH_INTENT", "DISPATCH_INTENT": "CONFIRMED"}
        if allowed.get(self.data["status"]) != status:
            raise DoseReplayBlocked("Dose state does not permit dispatch or replay")
        updated = dict(self.data, status=status, **evidence)
        write_json_atomic(self.path, updated)
        # Never report an in-memory CONFIRMED transition whose durable write
        # failed. The old state continues to forbid automatic replay.
        self.data = updated
