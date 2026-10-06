"""Pure replay and atomic projection for the Si6 operation journal."""

from __future__ import annotations

import json
import math
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


JOURNAL_SCHEMA_VERSION = 1
LIFECYCLE_STATES = {
    "planned", "dispatch_started", "completed", "failed", "uncertain", "skipped"
}
FINAL_OPERATION_STATES = {"completed", "failed", "uncertain", "skipped"}
ALLOWED_TRANSITIONS = {
    None: {"planned"},
    "planned": {"dispatch_started", "failed", "skipped"},
    "dispatch_started": {"completed", "failed", "uncertain"},
}
# Three-instrument cycle statuses that leave the needle, syringe, tubing, or
# NMR sample away from the cycle's rest state.
INCOMPLETE_CYCLE_STATUSES = {"STARTED", "NMR_COMPLETE", "MEASUREMENT_FAILED", "FAILED"}


@dataclass(frozen=True)
class ReplayIssue:
    code: str
    message: str
    sequence: int | None = None


@dataclass
class DerivedRunState:
    run_id: str | None = None
    journal_schema_version: int = JOURNAL_SCHEMA_VERSION
    last_applied_sequence: int = 0
    current_workflow_phase: str | None = None
    current_terminal_status: str | None = None
    active_operation: dict[str, Any] | None = None
    last_operation: dict[str, Any] | None = None
    physical_state_certainty: str = "certain"
    estimated_retained_syringe_volume_ml: float = 0.0
    default_pump_channel: int | None = None
    pump_channels: dict[str, dict[str, Any]] = field(default_factory=dict)
    reagent_doses: dict[str, dict[str, Any]] = field(default_factory=dict)
    completed_cycle_count: int = 0
    plateau_progress: dict[str, Any] = field(default_factory=dict)
    monitoring_progress: dict[str, Any] = field(default_factory=dict)
    last_valid_analysis_result: dict[str, Any] | None = None
    manual_inspection_required: bool = False
    # The rig is at rest, but a scientist must review the stop (for example a
    # failed NMR measurement after a completed cleanup) before a new run.
    operator_review_required: bool = False
    last_update_timestamp: str | None = None

    @property
    def terminal_status(self) -> str | None:
        return self.current_terminal_status

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReplayResult:
    state: DerivedRunState
    records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[ReplayIssue] = field(default_factory=list)
    errors: list[ReplayIssue] = field(default_factory=list)
    incomplete_operations: list[dict[str, Any]] = field(default_factory=list)
    partial_trailing_record: bool = False

    @property
    def valid(self) -> bool:
        return not self.errors


def _issue(
    collection: list[ReplayIssue],
    code: str,
    message: str,
    sequence: int | None = None,
) -> None:
    collection.append(ReplayIssue(code, message, sequence))


def replay_journal(path: Path) -> ReplayResult:
    """Replay a journal without importing hardware modules or writing files."""
    path = Path(path)
    result = ReplayResult(DerivedRunState())
    if not path.exists():
        _issue(result.errors, "journal_missing", f"Journal does not exist: {path}")
        return result
    payload = path.read_bytes()
    if not payload:
        _issue(result.errors, "journal_empty", "Journal is empty")
        return result

    has_final_newline = payload.endswith(b"\n")
    raw_lines = payload.splitlines()
    parsed: list[dict[str, Any]] = []
    for index, raw_line in enumerate(raw_lines, start=1):
        is_final = index == len(raw_lines)
        try:
            record = json.loads(raw_line.decode("utf-8"))
            if not isinstance(record, dict):
                raise ValueError("record is not a JSON object")
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            code = (
                "partial_trailing_record"
                if is_final and not has_final_newline
                else "malformed_record"
            )
            _issue(result.errors, code, f"Journal line {index} is invalid: {exc}")
            if code == "partial_trailing_record":
                result.partial_trailing_record = True
            continue
        parsed.append(record)
    if not has_final_newline and parsed and len(parsed) == len(raw_lines):
        _issue(
            result.errors,
            "missing_final_newline",
            "Final JSON record is complete but lacks its required durable newline",
            parsed[-1].get("sequence"),
        )

    operation_states: dict[str, str] = {}
    operation_records: dict[str, dict[str, Any]] = {}
    operation_channels: dict[str, Any] = {}
    event_ids: set[str] = set()
    expected_sequence = 1
    canonical_run_id: str | None = None
    terminal_seen = False
    last_cycle_status: str | None = None
    needle_motion_open = False
    stage_actions_open = False

    for record in parsed:
        sequence = record.get("sequence")
        schema = record.get("schema_version")
        run_id = record.get("run_id")
        event_id = record.get("event_id")
        if schema != JOURNAL_SCHEMA_VERSION:
            _issue(
                result.errors,
                "unknown_schema_version",
                f"Unsupported schema version {schema!r}",
                sequence,
            )
        if canonical_run_id is None:
            canonical_run_id = str(run_id) if run_id is not None else None
            result.state.run_id = canonical_run_id
        elif run_id != canonical_run_id:
            _issue(
                result.errors,
                "inconsistent_run_id",
                f"Expected run ID {canonical_run_id!r}, got {run_id!r}",
                sequence,
            )
        if not isinstance(sequence, int) or sequence != expected_sequence:
            code = (
                "duplicate_sequence"
                if isinstance(sequence, int) and sequence < expected_sequence
                else "sequence_gap"
            )
            _issue(
                result.errors,
                code,
                f"Expected sequence {expected_sequence}, got {sequence!r}",
                sequence if isinstance(sequence, int) else None,
            )
            if isinstance(sequence, int):
                expected_sequence = sequence
        expected_sequence += 1
        if not isinstance(event_id, str) or not event_id:
            _issue(result.errors, "missing_event_id", "Event ID is missing", sequence)
        elif event_id in event_ids:
            _issue(
                result.errors,
                "duplicate_event_id",
                f"Duplicate event ID {event_id}",
                sequence,
            )
        else:
            event_ids.add(event_id)

        event_type = record.get("event_type")
        lifecycle = record.get("lifecycle_state")
        physical = bool(record.get("physical_state_effect", False))
        if terminal_seen and physical and lifecycle in LIFECYCLE_STATES:
            _issue(
                result.errors,
                "physical_operation_after_terminal",
                "Physical operation appears after terminal event",
                sequence,
            )
            result.state.physical_state_certainty = "uncertain"
            result.state.manual_inspection_required = True
        if lifecycle is not None:
            operation_id = record.get("operation_id")
            if not isinstance(operation_id, str) or not operation_id:
                _issue(
                    result.errors,
                    "missing_operation_id",
                    "Lifecycle event lacks operation ID",
                    sequence,
                )
            elif lifecycle not in LIFECYCLE_STATES:
                _issue(
                    result.errors,
                    "unknown_lifecycle_state",
                    f"Unknown lifecycle state {lifecycle!r}",
                    sequence,
                )
            else:
                previous = operation_states.get(operation_id)
                if lifecycle not in ALLOWED_TRANSITIONS.get(previous, set()):
                    _issue(
                        result.errors,
                        "invalid_state_transition",
                        f"Operation {operation_id} cannot transition from "
                        f"{previous!r} to {lifecycle!r}",
                        sequence,
                    )
                operation_states[operation_id] = lifecycle
                channel = record.get("channel")
                if operation_id in operation_channels and operation_channels[operation_id] != channel:
                    _issue(result.errors, "inconsistent_operation_channel", f"Operation {operation_id} changes Chemyx channel", sequence)
                operation_channels.setdefault(operation_id, channel)
                operation_records[operation_id] = record
                result.state.last_operation = _operation_summary(record)
                channel_state = None
                if channel is not None and (type(channel) is not int or channel not in (0, 1, 2)):
                    _issue(result.errors, "invalid_operation_channel", f"Invalid Chemyx channel {channel!r}", sequence)
                elif channel is not None:
                    channel_state = result.state.pump_channels.setdefault(str(channel), {})
                    channel_state["last_operation"] = _operation_summary(record)
                    if lifecycle == "dispatch_started" and record.get("operation_type") in {"withdraw", "infuse"}:
                        channel_state["motion_active"] = True
                    if record.get("operation_type") == "pump_stop" and lifecycle in FINAL_OPERATION_STATES:
                        channel_state["last_stop_status"] = record.get("result_classification")
                        if lifecycle == "completed":
                            channel_state["motion_active"] = False
                    if lifecycle == "uncertain" or record.get("physical_state_certainty") == "uncertain":
                        channel_state["uncertain"] = True
                if lifecycle not in FINAL_OPERATION_STATES:
                    result.state.active_operation = _operation_summary(record)
                elif (
                    result.state.active_operation
                    and result.state.active_operation.get("operation_id")
                    == operation_id
                ):
                    result.state.active_operation = None
                if (
                    lifecycle == "completed"
                    and physical
                    and "expected_retained_volume_after_ml" in record
                ):
                    value = record["expected_retained_volume_after_ml"]
                    if (
                        isinstance(value, (int, float))
                        and math.isfinite(float(value))
                    ):
                        if channel_state is not None:
                            channel_state["retained_volume_ml"] = float(value)
                            action = record.get("operation_type")
                            if action in {"withdraw", "infuse"}:
                                key = "cumulative_withdrawn_ml" if action == "withdraw" else "cumulative_infused_ml"
                                volume = (record.get("requested_parameters") or {}).get("volume", 0)
                                if isinstance(volume, (int, float)) and not isinstance(volume, bool) and math.isfinite(volume) and volume >= 0:
                                    channel_state[key] = channel_state.get(key, 0.0) + float(volume)
                                else:
                                    _issue(result.errors, "invalid_transfer_volume", "Completed transfer volume is invalid", sequence)
                        if result.state.default_pump_channel is None or channel == result.state.default_pump_channel:
                            result.state.estimated_retained_syringe_volume_ml = float(value)
                if (
                    lifecycle == "uncertain"
                    or record.get("physical_state_certainty") == "uncertain"
                ):
                    result.state.physical_state_certainty = "uncertain"
                    result.state.manual_inspection_required = True

        if event_type == "phase_transition":
            result.state.current_workflow_phase = (
                record.get("new_state") or record.get("workflow_phase")
            )
            if result.state.last_applied_sequence == 0 and "pump_channels" in record:
                try:
                    default = record["default_pump_channel"]
                    channels = record["pump_channels"]
                    if type(default) is not int or default not in (0, 1, 2) or not isinstance(channels, dict):
                        raise ValueError("invalid default or channel mapping")
                    for ch, settings in channels.items():
                        if str(ch) not in {"0", "1", "2"} or not isinstance(settings, dict):
                            raise ValueError("invalid channel settings")
                        volume = settings.get("retained_volume_ml")
                        if not isinstance(volume, (int, float)) or isinstance(volume, bool) or not math.isfinite(volume) or volume < 0:
                            raise ValueError("invalid initial retained volume")
                    initial_volume = channels[str(default)]["retained_volume_ml"]
                    result.state.default_pump_channel = default
                    result.state.pump_channels = {str(ch): dict(settings) for ch, settings in channels.items()}
                    result.state.estimated_retained_syringe_volume_ml = float(initial_volume)
                except (KeyError, TypeError, ValueError) as exc:
                    _issue(result.errors, "invalid_initial_channels", f"Invalid initial Chemyx state: {exc}", sequence)
            initial_volume = record.get(
                "expected_retained_volume_before_ml"
            )
            if (
                result.state.last_applied_sequence == 0
                and isinstance(initial_volume, (int, float))
                and math.isfinite(float(initial_volume))
            ):
                result.state.estimated_retained_syringe_volume_ml = float(
                    initial_volume
                )
        elif event_type in {"experiment_reserved", "dose_dispatch_intent", "dose_confirmed"}:
            dose_id = record.get("dose_id")
            status = {"experiment_reserved": "RESERVED", "dose_dispatch_intent": "DISPATCH_INTENT", "dose_confirmed": "CONFIRMED"}[event_type]
            previous = result.state.reagent_doses.get(str(dose_id), {}).get("status")
            expected = {"RESERVED": None, "DISPATCH_INTENT": "RESERVED", "CONFIRMED": "DISPATCH_INTENT"}[status]
            if not isinstance(dose_id, str) or not dose_id or previous != expected:
                _issue(result.errors, "dose_lifecycle_invalid", "Reagent dose lifecycle is duplicated or out of order", sequence)
            prior = result.state.reagent_doses.get(str(dose_id), {})
            if previous is not None and (record.get("experiment_id") != prior.get("experiment_id") or record.get("configuration_sha256") != prior.get("configuration_sha256")):
                _issue(result.errors, "dose_identity_mismatch", "Dose experiment/config identity changed within the journal", sequence)
            if status == "CONFIRMED":
                move = operation_records.get(record.get("pump_operation_id"), {})
                params = move.get("requested_parameters", {})
                matched_stop = any(e.get("operation_type") == "pump_stop" and e.get("parent_operation_id") == move.get("operation_id")
                    and e.get("channel") == 2 and e.get("lifecycle_state") == "completed" and e.get("result_classification") == "succeeded"
                    and prior.get("intent_sequence", sequence) < e.get("sequence", 0) < move.get("sequence", 0) for e in operation_records.values())
                if not (move.get("lifecycle_state") == "completed" and move.get("operation_type") == "infuse" and move.get("channel") == 2
                        and move.get("sequence") == record.get("pump_completion_sequence") and move.get("sequence", 0) > prior.get("intent_sequence", sequence)
                        and params.get("volume") == record.get("volume_ml") and params.get("rate") == record.get("rate_ml_min")
                        and record.get("volume_ml") == prior.get("volume_ml") and record.get("rate_ml_min") == prior.get("rate_ml_min")
                        and move.get("result_classification") == "positively_confirmed" and matched_stop):
                    _issue(result.errors, "dose_completion_unproven", "Dose confirmation lacks its matched addressed move/STOP and configured parameters", sequence)
            result.state.reagent_doses[str(dose_id)] = {"status": status, "experiment_id": record.get("experiment_id"),
                "sequence": sequence, "channel": record.get("channel"), "volume_ml": record.get("volume_ml"),
                "rate_ml_min": record.get("rate_ml_min"), "configuration_sha256": record.get("configuration_sha256"),
                "intent_sequence": sequence if status == "DISPATCH_INTENT" else prior.get("intent_sequence")}
        elif event_type == "cycle_completed":
            result.state.completed_cycle_count += 1
        elif event_type == "cycle_status":
            last_cycle_status = record.get("status")
        elif event_type == "stage_actions_status":
            stage_actions_open = record.get("status") != "COMPLETE"
        elif event_type == "manual_inspection_required":
            result.state.manual_inspection_required = True
            if record.get("physical_state_certainty") == "uncertain":
                result.state.physical_state_certainty = "uncertain"
        elif event_type == "needle_transition":
            # A commanded move without its verified completion leaves the
            # needle position unknown.
            needle_motion_open = record.get("result_classification") == "started"
        elif event_type == "pump_configuration":
            channel = record.get("channel")
            if type(channel) is not int or channel not in (0, 1, 2):
                _issue(result.errors, "invalid_configuration_channel", "Pump configuration has an invalid channel", sequence)
            else:
                channel_state = result.state.pump_channels.setdefault(str(channel), {})
                for key in ("syringe_diameter_mm", "rate", "units"):
                    if key in record:
                        channel_state[key] = record[key]
        elif event_type == "analysis_result":
            if record.get("result_classification") == "valid":
                result.state.last_valid_analysis_result = record.get(
                    "analysis_result"
                )
            result.state.plateau_progress = dict(
                record.get("plateau_progress") or {}
            )
        elif event_type in {
            "monitoring_stage_started",
            "measurement_scheduled",
            "measurement_started",
            "measurement_completed",
            "plateau_detection",
            "stage_transition_decision",
        }:
            for key in (
                "workflow_phase",
                "monitoring_mode",
                "plateau_stopping_enabled",
                "measure_immediately",
                "max_measurements",
                "hard_runtime_ceiling_hours",
                "interval_minutes",
                "stage_started_at",
                "scheduled_measurement_number",
                "acquisition_attempt_number",
                "valid_analysis_count",
                "scheduled_measurement_time",
                "actual_cycle_start",
                "scheduling_delay_seconds",
                "plateau_detected",
                "result_classification",
            ):
                if key in record:
                    result.state.monitoring_progress[key] = record[key]
        elif event_type == "terminal":
            if terminal_seen:
                _issue(
                    result.errors,
                    "duplicate_terminal_event",
                    "More than one terminal event is present",
                    sequence,
                )
            else:
                result.state.current_terminal_status = record.get(
                    "terminal_status"
                )
                terminal_seen = True
                certainty = record.get("physical_state_certainty")
                if certainty == "uncertain":
                    result.state.physical_state_certainty = "uncertain"
                if certainty not in (None, "certain"):
                    result.state.manual_inspection_required = True
                if record.get("operator_review_required"):
                    result.state.operator_review_required = True

        result.state.last_applied_sequence = max(
            result.state.last_applied_sequence,
            sequence if isinstance(sequence, int) else 0,
        )
        result.state.last_update_timestamp = record.get("timestamp_utc")
        result.records.append(record)

    if needle_motion_open:
        result.state.physical_state_certainty = "uncertain"
        result.state.manual_inspection_required = True
        _issue(
            result.warnings,
            "needle_motion_incomplete",
            "A commanded needle move has no verified completion; the needle "
            "position is unknown",
        )
    if stage_actions_open:
        result.state.manual_inspection_required = True
        _issue(result.warnings, "stage_actions_incomplete", "A one-time stage action sequence did not complete; inspect both syringes, needle, tubing, and sample")
    if any(dose["status"] == "DISPATCH_INTENT" for dose in result.state.reagent_doses.values()):
        result.state.manual_inspection_required = True
        _issue(result.warnings, "reagent_dose_uncertain", "Reagent dispatch intent lacks confirmed completion; inspect ledger and journal, never replay automatically")
    if last_cycle_status in INCOMPLETE_CYCLE_STATUSES:
        result.state.manual_inspection_required = True
        _issue(
            result.warnings,
            "cycle_cleanup_incomplete",
            f"The last sampling cycle stopped at {last_cycle_status} before "
            "cleanup; reconcile the needle, syringe, tubing, NMR sample, and "
            "flask before any new run",
        )
    for operation_id, lifecycle in operation_states.items():
        if lifecycle not in FINAL_OPERATION_STATES:
            record = operation_records[operation_id]
            summary = _operation_summary(record)
            result.incomplete_operations.append(summary)
            if lifecycle == "dispatch_started" and record.get(
                "physical_state_effect"
            ):
                result.state.physical_state_certainty = "uncertain"
                result.state.manual_inspection_required = True
                channel = record.get("channel")
                if type(channel) is int and channel in (0, 1, 2):
                    result.state.pump_channels.setdefault(str(channel), {})["uncertain"] = True
    if result.incomplete_operations:
        result.state.active_operation = result.incomplete_operations[-1]
    return result


def _operation_summary(record: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "operation_id",
        "parent_operation_id",
        "operation_type",
        "channel",
        "lifecycle_state",
        "workflow_phase",
        "cycle_number",
        "sequence",
        "requested_parameters",
        "expected_retained_volume_before_ml",
        "expected_retained_volume_after_ml",
        "physical_state_certainty",
    )
    return {key: record[key] for key in keys if key in record}


def write_state_atomic(path: Path, state: DerivedRunState) -> None:
    """Flush and fsync a same-directory temporary file, then replace target."""
    write_json_atomic(path, state.to_dict())


def write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    """Durably replace a JSON object through a same-directory temporary file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = (
        json.dumps(
            value, indent=2, sort_keys=True, allow_nan=False
        )
        + "\n"
    ).encode("utf-8")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows indexers and antivirus scanners can briefly hold the prior
        # snapshot open. The journal is already durable; bounded retries keep
        # a transient sharing violation from forcing physical-state recovery.
        for attempt in range(6):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if os.name != "nt" or attempt == 5:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                pass


def load_state_snapshot(path: Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("run_state.json must contain a JSON object")
    return value
