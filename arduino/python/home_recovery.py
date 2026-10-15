"""Supervised logical HOME recovery; never a physical switch/encoder home."""
from __future__ import annotations

from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable
from uuid import uuid4

from chemyx_lab.runtime_journal import OperationJournal, discover_git_commit
from chemyx_lab.runtime_state import write_json_atomic
from .config import REPO_ROOT
from .errors import LiveExecutionBlocked, MotionInterlockError, PositionUncertainError
from .needle_state import NeedleState, NeedleStateStore, TrackedNeedle
from .protocol import bool_field
from .results import create_run_dir


# Only a restored communication link plus a fresh physical reference resolves
# this firmware latch. Motor timeouts, limit faults and unknown codes stay put.
REFERENCE_FAULTS = {"COMMUNICATION_LOSS"}
STOP_UNRESOLVED = {"stop_status_unknown", "manual_stop_failed", "stop_failed",
                   "manual_stop_during_possible_motion", "home_recovery_stop_failed"}

UNKNOWN_PROMPT = (
    "Needle position is UNKNOWN.\n\n"
    "Physically place the needle at HOME=0.\n"
    "Confirm the needle is stopped and at the\n"
    "correct sampling position.\n\n"
    "Establish current position as HOME=0? [y/N]: "
)


def ready_status(controller, *, allow_reference_fault=False):
    controller.ping()  # Sequence-checked PONG, not merely an open serial port.
    status = controller.status()
    if bool_field(status, "moving"):
        raise MotionInterlockError("HOME rejected: controller reports active motion")
    if bool_field(status, "limit_up") or bool_field(status, "limit_down"):
        raise MotionInterlockError("HOME rejected: active limit input requires inspection")
    allowed = {"NONE"} | (REFERENCE_FAULTS if allow_reference_fault else set())
    if status.get("fault") not in allowed:
        raise MotionInterlockError(f"HOME rejected: unrelated/unresolved controller fault {status.get('fault')!r}")
    return status


def reference_is_valid(state, cfg):
    needle = cfg["needle"]
    return (state.position_valid and state.reason != "assumed_home_demo"
            and state.steps_per_unit == needle["steps_per_unit"]
            and state.up_step_sign == needle["up_step_sign"]
            and needle["min_position"] <= state.logical_position <= needle["max_position"])


def restore_home(controller, cfg, state_path: Path, *, confirm: Callable[[str], bool],
                 force_reference=False, mode="live"):
    """One confirmation, strict readback, and an append-only per-action journal.

    Existing experiment journals/dose/pump records are deliberately not changed.
    The canonical needle record is the only active host interlock we resolve.
    """
    store = NeedleStateStore(state_path)
    run_dir = create_run_dir(cfg["results"]["run_root_dir"], f"needle_home_{uuid4().hex[:8]}")
    journal = OperationJournal(run_dir / "operation_journal.jsonl", run_dir.name,
                               software_version=discover_git_commit(REPO_ROOT))
    prior = None
    changed = False
    motion_attempted = False
    controller.allow_motion = False
    # Allow supervised physical placement at the one prompt. Individual RPC
    # deadlines and the firmware's 120-second motion watchdog stay unchanged.
    controller.overall_timeout_s = max(controller.overall_timeout_s, 900.0)
    try:
        try:
            prior = store.load()
            prior_error = None
        except PositionUncertainError as exc:
            prior_error = str(exc)
        journal.append("needle_home_requested", mode=mode, state_path=str(state_path),
                       prior_state=asdict(prior) if prior is not None else None,
                       prior_state_error=prior_error, physical_position_measured=False,
                       previous_pending_guard=store.home_pending_path.read_text(encoding="utf-8") if store.home_pending_path.exists() else None)
        print(f"HOME recovery journal: {journal.path}")
        if prior is not None and prior.reason in STOP_UNRESOLVED:
            raise MotionInterlockError("Unresolved STOP verification failure; verify the standalone stop command before HOME. No position fault cleared.")
        initial = ready_status(controller, allow_reference_fault=True)
        journal.append("needle_home_controller_checked", controller_status=initial)
        n, m = cfg["needle"], cfg["motion"]
        if (type(n.get("steps_per_unit")) is not int or n["steps_per_unit"] <= 0
                or type(n.get("up_step_sign")) is not int or n["up_step_sign"] not in (-1, 1)
                or not n["min_position"] <= 0 <= n["max_position"]
                or not m.get("maximum_speed_steps_s") or not m.get("maximum_acceleration_steps_s2")):
            raise MotionInterlockError("Verified HOME geometry, steps/unit, direction and motion calibration are required")
        manual = (force_reference or store.home_pending_path.exists() or prior is None or not reference_is_valid(prior, cfg)
                  or initial["fault"] in REFERENCE_FAULTS)
        prompt = UNKNOWN_PROMPT if manual and not force_reference else (
            "Physically place the stopped needle at HOME=0. Establish current position as HOME=0? [y/N]: "
            if manual else "Move needle to absolute HOME=0 using the saved reference? [y/N]: "
        )
        if not confirm(prompt):
            journal.append("needle_home_declined", active_position_fault_cleared=False)
            raise LiveExecutionBlocked("HOME confirmation", ["HOME cancelled; no motion or reference reset"])
        journal.append("needle_home_confirmed", recovery_basis="operator_confirmed_reference" if manual else "completed_absolute_movement")
        ready_status(controller, allow_reference_fault=manual)  # Recheck after human input.
        if manual:
            if prior is None:
                backup = store.quarantine_corrupt()
                journal.append("needle_home_corrupt_record_preserved", backup_path=str(backup))
                prior = store.load()
            journal.append("needle_home_reference_intent", target=0, motor_movement=False)
            write_json_atomic(store.home_pending_path, {"journal": str(journal.path), "position_valid": False})
            changed = True
            pending = NeedleState(logical_position=0, position_valid=False,
                                  last_updated=datetime.now(timezone.utc).isoformat(),
                                  reason="home_recovery_pending", steps_per_unit=n["steps_per_unit"], up_step_sign=n["up_step_sign"])
            store.save(pending)
            if store.load() != pending:
                raise PositionUncertainError("Pending HOME record failed persistence readback")
            expected = replace(pending, position_valid=True, reason="operator_confirmed_home")
        else:
            # Revalidate the exact state inspected before confirmation.
            if store.load() != prior:
                raise PositionUncertainError("Needle record changed during confirmation; HOME refused")
            controller.allow_motion = True
            if cfg["firmware"].get("runtime_configurable"):
                controller.configure_runtime(cfg)
            ready_status(controller)
            needle = TrackedNeedle(controller, cfg, state_path=state_path)
            journal.append("needle_home_motion_intent", from_position=prior.logical_position, target=0)
            write_json_atomic(store.home_pending_path, {"journal": str(journal.path), "position_valid": False})
            changed = True
            motion_attempted = prior.logical_position != 0
            needle.return_to_home()
            ready_status(controller)
            expected = replace(needle.state, logical_position=0, position_valid=True, reason="motion_completed",
                               last_updated=datetime.now(timezone.utc).isoformat())
        ready_status(controller, allow_reference_fault=manual)
        store.save(expected)
        if store.load() != expected:
            raise PositionUncertainError("HOME record failed persistence readback")
        status = ready_status(controller, allow_reference_fault=manual)
        if status["fault"] in REFERENCE_FAULTS:
            journal.append("needle_home_position_latch_clear_intent", original_controller_fault=status["fault"])
            controller.clear_fault()
        final = ready_status(controller)
        if store.load() != expected:
            raise PositionUncertainError("Saved HOME changed during final verification")
        journal.append("needle_home_completed", recovery_basis="operator_confirmed_reference" if manual else "completed_absolute_movement",
                       saved_state=asdict(expected), controller_status=final,
                       active_position_fault_cleared=True, unrelated_interlocks_changed=False,
                       physical_position_measured=False,
                       activation_condition="verified HOME pending marker removed after this durable event")
        store.home_pending_path.unlink()
        if store.home_pending_path.exists():
            raise PositionUncertainError("HOME pending guard could not be removed")
        print("\nARDUINO HOME COMPLETE\n\nSaved position: HOME = 0\nController: READY\n"
              "Position tracking: VALID\nActive position fault: CLEARED\n\nNeedle ready for normal operation.")
        print("Reference: operator-confirmed HOME; no motor movement." if manual else
              "Reference: completed absolute HOME command; software position estimate.")
        print("Physical position was not independently measured.")
        return expected
    except BaseException as exc:
        if changed:
            if not store.home_pending_path.exists():
                write_json_atomic(store.home_pending_path, {"journal": str(journal.path), "position_valid": False})
            stop_failed = motion_attempted and not controller.stop_best_effort()
            # A failed final STATUS/readback/journal must not leave a new valid
            # reference usable by the next experiment.
            try:
                current = store.load()
                store.save(replace(current, position_valid=False,
                                   reason="home_recovery_stop_failed" if stop_failed else "home_recovery_verification_failed",
                                   last_updated=datetime.now(timezone.utc).isoformat()))
            except BaseException as persistence_error:
                journal.append("needle_home_invalidation_failed", error_message=str(persistence_error))
        journal.append("needle_home_failed", error_message=f"{type(exc).__name__}: {exc}", active_position_fault_cleared=False)
        raise
