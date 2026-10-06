"""Durable one-experiment admission and at-most-once reagent dispatch barrier.

No automatic resume/replay is supported. A prior reservation always blocks a
fresh live run, including a crash before reagent delivery. Keep this ledger with
the physical experiment and its journal when copying the repository.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

from ..runtime_state import write_json_atomic


class DoseReplayBlocked(RuntimeError):
    pass


def validate_experiment_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", value):
        raise ValueError("workflow.experiment_id must be 1–80 letters/digits/underscore/dot/hyphen")
    return value


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
        self.data = dict(self.data, status=status, **evidence)
        write_json_atomic(self.path, self.data)
