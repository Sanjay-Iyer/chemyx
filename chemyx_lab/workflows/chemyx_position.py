"""Persistent software HOME reference and plunger-displacement ledger.

HOME is a custom software reference, exactly like the Arduino needle HOME. It is
NOT a native Chemyx command and NOT a measured plunger position. An operator
physically places a plunger, confirms it, and the ledger records that point as
HOME = 0 mL. Every tracked infusion or withdrawal then changes the saved
displacement, and a later HOME return is calculated from it.

Sign convention (matches ``Pump.infuse``/``Pump.withdraw`` and
``run_safe_metered_move``, which sends +volume to infuse and -volume to
withdraw):

    withdraw = +displacement      infuse = -displacement      HOME = 0

Volumes are stored as integer nanolitres so repeated moves never accumulate
floating-point error.

One ledger directory holds the authoritative record for the rig's pump:

    channel_1_state.json, channel_2_state.json   atomic per-channel projections
    operations.jsonl                             append-only fsynced journal
    ledger.lock / owner.json                     single-owner process lock

Every state change is appended to the journal first (with a revision number),
then the channel file is atomically replaced and read back. A crash between the
two leaves the revisions disagreeing, which reads as UNKNOWN. A motion intent is
made durable before dispatch and the position advances only after the existing
completion evidence (timed move, positively confirmed STOP, durable run-journal
completion). Anything else leaves the channel UNKNOWN; nothing here ever guesses
a distance.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from arduino.python.errors import ProcessLockError
from arduino.python.run_lock import PortProcessLock

from .. import config
from ..runtime_journal import OperationJournal, discover_git_commit
from ..runtime_state import replay_journal, write_json_atomic


SCHEMA = "chemyx.syringe-home.v1"
JOURNAL_RUN_ID = "chemyx_syringe_home_ledger"
CHANNELS = (1, 2)
NL_PER_ML = 1_000_000
STATE_DIR_ENV = "CHEMYX_POSITION_STATE_DIR"
COMPLETION_EVIDENCE = "timed_move_positively_confirmed_stop_durable_journal"
STATE_KEYS = {
    "schema", "channel", "home_defined", "home_reference_ml",
    "current_displacement_ml", "current_displacement_nl", "position_known",
    "pending_motion", "pending_operation", "reference_valid", "position_state",
    "uncertainty_reason", "last_trusted_displacement_ml", "home",
    "pump_identity", "syringe", "configuration_identity",
    "configuration_sha256", "last_confirmed_movement", "tracked_since_home",
    "revision", "last_journal_sequence", "updated_at_utc", "updated_by",
}


class PositionLedgerError(RuntimeError):
    """The displacement ledger could not be read, written or trusted."""


class LedgerBusyError(PositionLedgerError):
    """Another process owns the pump's displacement ledger."""


class HomeRefused(PositionLedgerError):
    """A HOME return or manual move is not supported by the saved evidence."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_root(mock: bool) -> Path:
    """The live rig ledger, or the separate mock ledger.

    ``CHEMYX_POSITION_STATE_DIR`` relocates both (used by the test suite so
    tests never touch the rig's record).
    """
    base = os.environ.get(STATE_DIR_ENV)
    root = Path(base) if base else config.REPO_ROOT / "runtime"
    return root / ("chemyx_state_mock" if mock else "chemyx_state")


def pump_identity(port: Any, *, mock: bool) -> str:
    """The pump a reference belongs to: its serial port, or the mock pump."""
    if mock:
        return "mock"
    text = str(port or "").strip()
    if not text:
        raise PositionLedgerError("A live Chemyx serial port is required to identify the pump")
    return f"serial:{text.upper()}"


def to_nl(volume_ml: Any, label: str = "volume_ml") -> int:
    if isinstance(volume_ml, bool):
        raise ValueError(f"{label} must be a number")
    try:
        value = Decimal(str(volume_ml))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label} must be a number") from exc
    if not value.is_finite():
        raise ValueError(f"{label} must be finite")
    return int((value * NL_PER_ML).to_integral_value(rounding=ROUND_HALF_EVEN))


def to_ml(volume_nl: int | None) -> float | None:
    return None if volume_nl is None else volume_nl / NL_PER_ML


def ml_text(volume_nl: int | None, *, signed: bool = False) -> str:
    """Exact decimal text, e.g. ``+5``, ``-1.8`` or ``0.25``."""
    if volume_nl is None:
        return "unknown"
    text = format((Decimal(volume_nl) / NL_PER_ML).normalize(), "f")
    if signed and volume_nl > 0:
        text = "+" + text
    return f"{text} mL"


@dataclass(frozen=True)
class ChannelIdentity:
    """What a HOME reference means: pump, syringe geometry and the reference."""

    channel: int
    pump_identity: str
    diameter_mm: float
    capacity_ml: float
    reference_description: str
    syringe_volume_at_home_ml: float | None

    def reference_fields(self) -> dict[str, Any]:
        return {
            "pump_identity": self.pump_identity,
            "diameter_mm": float(self.diameter_mm),
            "capacity_ml": float(self.capacity_ml),
            "reference_description": self.reference_description,
            "syringe_volume_at_home_ml": (
                None if self.syringe_volume_at_home_ml is None
                else float(self.syringe_volume_at_home_ml)
            ),
        }


def motion_fields(pump: str, diameter_mm: float, capacity_ml: float) -> dict[str, Any]:
    return {"pump_identity": pump, "diameter_mm": float(diameter_mm), "capacity_ml": float(capacity_ml)}


def identity_changes(saved: dict[str, Any] | None, current: dict[str, Any]) -> list[str]:
    """Readable differences between a saved reference identity and now."""
    changes = []
    saved = saved or {}
    for key, value in current.items():
        old = saved.get(key)
        if isinstance(value, float) and isinstance(old, (int, float)) and not isinstance(old, bool):
            same = abs(float(old) - value) <= 1e-9
        else:
            same = old == value
        if not same:
            changes.append(f"{key}: {old!r} -> {value!r}")
    return changes


def _digest(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def blank_state(channel: int) -> dict[str, Any]:
    return _finalize({
        "schema": SCHEMA, "channel": channel, "home_defined": False,
        "home_reference_ml": 0.0, "current_displacement_ml": None,
        "current_displacement_nl": None, "position_known": False,
        "pending_motion": False, "pending_operation": None,
        "reference_valid": False, "position_state": "NO_HOME",
        "uncertainty_reason": "No HOME reference has been established",
        "last_trusted_displacement_ml": None, "home": None,
        "pump_identity": None, "syringe": None, "configuration_identity": None,
        "configuration_sha256": None, "last_confirmed_movement": None,
        "tracked_since_home": {"withdrawn_nl": 0, "infused_nl": 0, "movements": 0},
        "revision": 0, "last_journal_sequence": 0, "updated_at_utc": None,
        "updated_by": None,
    })


def _finalize(state: dict[str, Any]) -> dict[str, Any]:
    """Derive the display fields from the authoritative ones."""
    state = dict(state)
    if not state["position_known"]:
        state["current_displacement_nl"] = None
    state["current_displacement_ml"] = to_ml(state["current_displacement_nl"])
    state["reference_valid"] = bool(
        state["home_defined"] and state["position_known"] and not state["pending_motion"]
    )
    if state["pending_motion"]:
        state["position_state"] = "MOTION_PENDING"
    elif not state["home_defined"]:
        state["position_state"] = "NO_HOME"
    elif state["position_known"]:
        state["position_state"] = "KNOWN"
    else:
        state["position_state"] = "UNKNOWN"
    if state["reference_valid"]:
        state["uncertainty_reason"] = None
    return state


def validate_state(data: Any, channel: int) -> dict[str, Any]:
    """Reject any record whose fields could make an unknown position look known."""
    if not isinstance(data, dict) or set(data) != STATE_KEYS:
        raise ValueError("unexpected or missing state fields")
    if data["schema"] != SCHEMA or data["channel"] != channel:
        raise ValueError("schema or channel does not match")
    for key in ("home_defined", "position_known", "pending_motion", "reference_valid"):
        if type(data[key]) is not bool:
            raise ValueError(f"{key} must be true or false")
    if type(data["revision"]) is not int or data["revision"] < 0:
        raise ValueError("revision must be a nonnegative integer")
    if data["position_known"]:
        if not data["home_defined"] or type(data["current_displacement_nl"]) is not int:
            raise ValueError("a known position needs HOME and an integer displacement")
        if not isinstance(data["configuration_identity"], dict) or not isinstance(data["home"], dict):
            raise ValueError("a known position needs its HOME reference identity")
    if data["pending_motion"] and not (
        isinstance(data["pending_operation"], dict) and data["pending_operation"].get("operation_id")
    ):
        raise ValueError("pending motion lacks its operation")
    if _finalize(data) != data:
        raise ValueError("derived fields disagree with the authoritative fields")
    return data


def _read_state_file(path: Path, channel: int) -> tuple[dict[str, Any] | None, bytes | None, str | None]:
    """Return (state, raw bytes, problem); a missing file is (None, None, None)."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None, None, None
    except OSError as exc:
        return None, None, f"state file unreadable: {exc}"
    try:
        return validate_state(json.loads(raw.decode("utf-8")), channel), raw, None
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
        return None, raw, f"state file corrupt: {exc}"


def _journal_index(path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """Valid ledger journal records, or a reason the journal cannot be trusted."""
    if not path.exists() or path.stat().st_size == 0:
        return [], None
    replay = replay_journal(path)
    if replay.errors:
        return [], "; ".join(issue.message for issue in replay.errors)
    if any(record.get("run_id") != JOURNAL_RUN_ID for record in replay.records):
        return [], "journal belongs to a different ledger"
    return replay.records, None


def _latest_by_channel(records: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    latest = {}
    for record in records:
        if record.get("channel") in CHANNELS and isinstance(record.get("revision"), int):
            latest[record["channel"]] = record
    return latest


def _disagreement(state: dict[str, Any], record: dict[str, Any] | None) -> str | None:
    """Why a state file does not match its journal record, or None."""
    if record is None or record["revision"] != state["revision"]:
        return (f"state file revision {state['revision']} disagrees with journal revision "
                f"{record['revision'] if record else 'none'} (stale, restored or conflicting record)")
    content = {key: value for key, value in state.items() if key != "last_journal_sequence"}
    if record.get("state") != content or record.get("sequence") != state["last_journal_sequence"]:
        return "state file content differs from its journal record (edited or corrupted)"
    return None


def _journal_snapshot(channel: int, record: dict[str, Any] | None) -> dict[str, Any]:
    """Last journaled state (for display/HOME metadata) when the file is unusable."""
    snapshot = (record or {}).get("state")
    if not isinstance(snapshot, dict):
        return blank_state(channel)
    merged = dict(blank_state(channel))
    merged.update({key: value for key, value in snapshot.items() if key in STATE_KEYS})
    try:
        return _finalize(merged)
    except (KeyError, TypeError):
        return blank_state(channel)


@dataclass(frozen=True)
class MovePlan:
    """A calculated, not yet executed, transfer for one channel."""

    channel: int
    direction: str | None
    volume_nl: int
    displacement_before_nl: int
    displacement_after_nl: int
    revision: int

    @property
    def volume_ml(self) -> float:
        return self.volume_nl / NL_PER_ML

    @property
    def moves(self) -> bool:
        return self.direction is not None


def _usable_reference(state: dict[str, Any], identity: ChannelIdentity) -> int:
    """The trusted displacement, or HomeRefused explaining what is missing."""
    ch = identity.channel
    if state["pending_motion"]:
        op = state["pending_operation"] or {}
        raise HomeRefused(
            f"Channel {ch}: a tracked {op.get('direction', 'motion')} of "
            f"{op.get('volume_ml', '?')} mL has no completion evidence, so the plunger "
            "may have moved partially. Automatic HOME is refused."
        )
    if not state["home_defined"]:
        raise HomeRefused(
            f"Channel {ch}: no HOME reference exists. Physically place the plunger at "
            "its HOME position, then run set-home."
        )
    if not state["position_known"]:
        raise HomeRefused(
            f"Channel {ch}: position UNKNOWN ({state['uncertainty_reason']}). Automatic "
            "HOME is refused; physically re-position the plunger and run set-home."
        )
    changes = identity_changes(state["configuration_identity"], identity.reference_fields())
    if changes:
        raise HomeRefused(
            f"Channel {ch}: the configuration no longer matches the saved HOME reference "
            f"({'; '.join(changes)}). Restore the previous settings, or physically "
            "re-establish HOME with set-home."
        )
    return state["current_displacement_nl"]


def _check_fill(identity: ChannelIdentity, displacement_nl: int, label: str) -> None:
    capacity_nl = to_nl(identity.capacity_ml)
    if identity.syringe_volume_at_home_ml is None:
        if abs(displacement_nl) > capacity_nl:
            raise HomeRefused(f"Channel {identity.channel}: {label} exceeds the {identity.capacity_ml:g} mL syringe capacity")
        return
    fill = to_nl(identity.syringe_volume_at_home_ml) + displacement_nl
    if not 0 <= fill <= capacity_nl:
        raise HomeRefused(
            f"Channel {identity.channel}: {label} implies a syringe volume of "
            f"{ml_text(fill)}, outside 0 to {identity.capacity_ml:g} mL"
        )


def plan_home(state: dict[str, Any], identity: ChannelIdentity) -> MovePlan:
    """required = HOME - current: positive withdraws, negative infuses, zero stays."""
    displacement = _usable_reference(state, identity)
    _check_fill(identity, displacement, f"the recorded displacement {ml_text(displacement, signed=True)}")
    required = -displacement
    direction = "withdraw" if required > 0 else "infuse" if required < 0 else None
    return MovePlan(identity.channel, direction, abs(required), displacement, 0, state["revision"])


def plan_transfer(state: dict[str, Any], identity: ChannelIdentity, direction: str, volume_ml: float) -> MovePlan:
    """A manual move must stay inside the syringe from a trusted HOME."""
    if direction not in {"withdraw", "infuse"}:
        raise ValueError("direction must be withdraw or infuse")
    volume = to_nl(volume_ml)
    if volume <= 0:
        raise ValueError("Manual transfer volume must be positive")
    displacement = _usable_reference(state, identity)
    if identity.syringe_volume_at_home_ml is None:
        raise HomeRefused(
            f"Channel {identity.channel}: set home.syringe_volume_at_home_ml in the manual "
            "control YAML so syringe travel limits can be checked before a manual move."
        )
    after = displacement + (volume if direction == "withdraw" else -volume)
    _check_fill(identity, after, f"{direction} {ml_text(volume)}")
    return MovePlan(identity.channel, direction, volume, displacement, after, state["revision"])


class _LedgerLock(PortProcessLock):
    """The existing Windows/POSIX exclusive process lock, at the ledger's path."""

    def __init__(self, root: Path) -> None:
        super().__init__("chemyx-syringe-home-ledger", root)
        self.path = Path(root) / "ledger.lock"


def ledger_owner(root: Path) -> dict[str, Any] | None:
    """The owning process when the ledger is locked, else None."""
    root = Path(root)
    if not root.is_dir():
        return None
    lock = _LedgerLock(root)
    try:
        lock.acquire()
    except ProcessLockError:
        try:
            return json.loads((root / "owner.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"owner": "unknown process"}
    lock.release()
    return None


def inspect_ledger(root: Path) -> dict[int, dict[str, Any]]:
    """Read-only view for status/verify; never writes, never takes ownership.

    Each channel view is the stored state with ``effective_state`` and
    ``problems`` that already account for crashes, stale files and conflicts.
    """
    root = Path(root)
    owner = ledger_owner(root)
    records, journal_problem = _journal_index(root / "operations.jsonl")
    latest = _latest_by_channel(records)
    views = {}
    for ch in CHANNELS:
        state, _raw, problem = _read_state_file(root / f"channel_{ch}_state.json", ch)
        record = latest.get(ch)
        base = state or _journal_snapshot(ch, record)
        problems = [problem] if problem else []
        if journal_problem:
            problems.append(f"operations journal cannot be trusted: {journal_problem}")
        # While an owner holds the ledger its two-step writes may be mid-flight.
        if owner is None and not journal_problem:
            if state is not None and _disagreement(state, record):
                problems.append(_disagreement(state, record))
            if state is None and problem is None and record is not None:
                problems.append(f"state file missing although the journal has revision {record['revision']}")
        if owner is None and state is not None and state["pending_motion"]:
            op = state["pending_operation"] or {}
            problems.append(
                f"previous process ended during tracked {op.get('direction')} of "
                f"{op.get('volume_ml')} mL (operation {op.get('operation_id')}); the plunger may have moved partially"
            )
        if owner is not None:
            effective = "IN_USE"
        elif problems:
            effective = "UNKNOWN" if (problem or base["home_defined"]) else "NO_HOME"
        else:
            effective = base["position_state"]
        views[ch] = dict(base, effective_state=effective, problems=problems, owner=owner)
    return views


class PositionLedger:
    """The single authoritative displacement record, owned by one process."""

    def __init__(self, root: Path, *, owner: str) -> None:
        self.root = Path(root)
        self.owner = owner
        self.journal_path = self.root / "operations.jsonl"
        self.notices: list[str] = []
        self._lock: _LedgerLock | None = None
        self._journal: OperationJournal | None = None
        self._states: dict[int, dict[str, Any]] = {}
        self._disk: dict[int, bytes | None] = {}
        self._revisions: dict[int, int] = {}

    # -- ownership -------------------------------------------------------
    def __enter__(self) -> "PositionLedger":
        return self.acquire()

    def __exit__(self, *_exc) -> bool:
        self.release()
        return False

    def acquire(self) -> "PositionLedger":
        if self._lock is not None:
            return self
        self.root.mkdir(parents=True, exist_ok=True)
        lock = _LedgerLock(self.root)
        try:
            lock.acquire()
        except ProcessLockError as exc:
            holder = ledger_owner(self.root) or {}
            raise LedgerBusyError(
                f"The Chemyx position ledger {self.root} is owned by another process "
                f"({holder.get('owner', 'unknown')}, pid {holder.get('pid', '?')}). Only one "
                "program may control the pump; finish or stop it first."
            ) from exc
        self._lock = lock
        try:
            write_json_atomic(self.root / "owner.json", {"owner": self.owner, "pid": os.getpid(), "acquired_at_utc": _now()})
            self._open_journal()
            for ch in CHANNELS:
                self._reconcile(ch)
        except BaseException:
            self.release()
            raise
        return self

    def release(self) -> None:
        if self._lock is None:
            return
        try:
            (self.root / "owner.json").unlink(missing_ok=True)
        except OSError:
            pass
        lock, self._lock = self._lock, None
        self._journal = None
        lock.release()

    def _open_journal(self) -> None:
        records, problem = _journal_index(self.journal_path)
        self._journal_problem = problem
        if problem:
            backup = self.journal_path.with_name(f"{self.journal_path.name}.corrupt-{uuid.uuid4().hex[:12]}.bak")
            self.journal_path.replace(backup)
            self.notices.append(f"Position journal could not be trusted ({problem}); preserved as {backup.name}")
            self._journal_problem = f"{problem} (preserved as {backup.name})"
        self._latest = _latest_by_channel(records)
        self._revisions = {ch: record["revision"] for ch, record in self._latest.items()}
        self._journal = OperationJournal(self.journal_path, JOURNAL_RUN_ID, software_version=discover_git_commit(config.REPO_ROOT))

    def _reconcile(self, ch: int) -> None:
        """Turn every crash, stale file or conflict into an explicit UNKNOWN."""
        path = self.state_path(ch)
        state, raw, problem = _read_state_file(path, ch)
        self._disk[ch] = raw
        record = self._latest.get(ch)
        reason = None
        if problem:
            backup = self._preserve(path)
            reason = f"{problem}; preserved as {backup}"
            state = _journal_snapshot(ch, record)
        elif self._journal_problem:
            if state is None:
                self._states[ch] = blank_state(ch)
                return
            reason = f"operations journal could not be trusted: {self._journal_problem}"
        elif state is None:
            if record is None:
                self._states[ch] = blank_state(ch)
                return
            state = _journal_snapshot(ch, record)
            reason = f"state file missing although the journal has revision {record['revision']}"
        elif _disagreement(state, record):
            reason = f"{_disagreement(state, record)}; preserved as {self._preserve(path)}"
        elif state["pending_motion"]:
            op = state["pending_operation"]
            reason = (f"a previous process ended during tracked {op.get('direction')} of "
                      f"{op.get('volume_ml')} mL (operation {op.get('operation_id')}, "
                      f"source {op.get('source')}); the plunger may have moved partially")
        self._states[ch] = state
        if reason is not None:
            self.notices.append(f"Channel {ch}: {reason}")
            self._commit_unknown(ch, state, reason)

    def _preserve(self, path: Path) -> str | None:
        if not path.exists():
            return None
        backup = path.with_name(f"{path.name}.corrupt-{uuid.uuid4().hex[:12]}.bak")
        shutil.copy2(path, backup)
        return backup.name

    def _require_owner(self) -> None:
        if self._lock is None or self._journal is None:
            raise PositionLedgerError("The position ledger is not owned by this process")

    # -- state access ----------------------------------------------------
    def state_path(self, ch: int) -> Path:
        return self.root / f"channel_{ch}_state.json"

    def _current(self, ch: int) -> dict[str, Any]:
        """Cached state, after proving nobody changed the file underneath us."""
        self._require_owner()
        if ch not in CHANNELS:
            raise ValueError("Chemyx channel must be 1 or 2")
        path = self.state_path(ch)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            raw = None
        if raw != self._disk.get(ch):
            backup = self._preserve(path)
            return self._commit_unknown(
                ch, self._states[ch],
                f"state file changed outside the owning session (preserved as {backup})",
            )
        return self._states[ch]

    def state(self, ch: int) -> dict[str, Any]:
        return dict(self._current(ch))

    def _commit(self, ch: int, state: dict[str, Any], event_type: str, **fields: Any) -> dict[str, Any]:
        """Journal first, then atomic projection, then read-back verification."""
        self._require_owner()
        revision = max(int(state.get("revision", 0)), self._revisions.get(ch, 0)) + 1
        new = _finalize(dict(state, revision=revision, updated_at_utc=_now(), updated_by=self.owner))
        validate_state(new, ch)
        snapshot = {key: value for key, value in new.items() if key != "last_journal_sequence"}
        record = self._journal.append(event_type, channel=ch, revision=revision, state=snapshot, **fields)
        self._revisions[ch] = revision
        new["last_journal_sequence"] = record["sequence"]
        path = self.state_path(ch)
        write_json_atomic(path, new)
        raw = path.read_bytes()
        if json.loads(raw.decode("utf-8")) != new:
            raise PositionLedgerError(f"Channel {ch} position record failed read-back verification")
        self._disk[ch] = raw
        self._states[ch] = new
        return new

    def _commit_unknown(self, ch: int, state: dict[str, Any], reason: str, **fields: Any) -> dict[str, Any]:
        last = state.get("current_displacement_ml") if state.get("position_known") else state.get("last_trusted_displacement_ml")
        new = dict(state, position_known=False, pending_motion=False, pending_operation=None,
                   current_displacement_nl=None, uncertainty_reason=reason,
                   last_trusted_displacement_ml=last)
        return self._commit(ch, new, "chemyx_position_unknown", reason=reason,
                            previous_pending_operation=state.get("pending_operation"), **fields)

    # -- operator reference ----------------------------------------------
    def set_home(self, identity: ChannelIdentity, *, receipt_id: str, established_by: str) -> dict[str, Any]:
        """Save the operator-confirmed physical position as HOME = 0. No motion."""
        ch = identity.channel
        previous = self._current(ch)
        reference = identity.reference_fields()
        new = dict(blank_state(ch), revision=previous["revision"])
        new.update(
            home_defined=True, position_known=True, current_displacement_nl=0,
            home={
                "established_at_utc": _now(), "receipt_id": receipt_id,
                "established_by": established_by, "operator_confirmed": True,
                "reference_description": identity.reference_description,
                "syringe_volume_at_home_ml": reference["syringe_volume_at_home_ml"],
                "previous_position_state": previous["position_state"],
                "previous_displacement_ml": previous["current_displacement_ml"],
                "previous_uncertainty_reason": previous["uncertainty_reason"],
                "physical_position_measured": False,
            },
            pump_identity=identity.pump_identity,
            syringe={"diameter_mm": reference["diameter_mm"], "capacity_ml": reference["capacity_ml"]},
            configuration_identity=reference, configuration_sha256=_digest(reference),
            uncertainty_reason=None,
        )
        saved = self._commit(ch, new, "chemyx_position_home_set", receipt_id=receipt_id,
                             operator_confirmed=True, previous_position_state=previous["position_state"])
        on_disk, _raw, problem = _read_state_file(self.state_path(ch), ch)
        if problem or on_disk != saved or not saved["reference_valid"] or saved["current_displacement_nl"] != 0:
            raise PositionLedgerError(f"Channel {ch} HOME record failed persistence verification")
        return saved

    # -- motion tracking -------------------------------------------------
    def record_manual_return_home(self, identity: ChannelIdentity, *, run_id: str) -> dict[str, Any]:
        """Record manual preparation at an EXISTING HOME, without redefining it.

        The fixed-time workflow requires the operator to restore its starting
        loading before launching. This records that return, including after an
        interrupted previous run. It sends no motor command and preserves the
        original HOME receipt, geometry, identity and movement totals.
        """
        ch = identity.channel
        previous = self._current(ch)
        if not previous["home_defined"] or not isinstance(previous["home"], dict):
            raise HomeRefused(f"Channel {ch}: no established Chemyx HOME to preserve. "
                              "Establish it with the standalone pump tool during manual setup.")
        changes = identity_changes(previous["configuration_identity"], identity.reference_fields())
        if changes:
            raise HomeRefused(f"Channel {ch}: saved Chemyx HOME does not match this setup: "
                              + "; ".join(changes) + ". The experiment will not redefine it.")
        _check_fill(identity, 0, "manually restored HOME loading")
        new = dict(previous, position_known=True, current_displacement_nl=0,
                   pending_motion=False, pending_operation=None, uncertainty_reason=None)
        return self._commit(ch, new, "chemyx_position_manual_return_home", manual_return_run_id=run_id,
                            previous_position_state=previous["position_state"],
                            previous_displacement_ml=previous["current_displacement_ml"],
                            previous_uncertainty_reason=previous["uncertainty_reason"],
                            previous_pending_operation=previous["pending_operation"],
                            reference_redefined=False, physical_motion_commanded=False,
                            physical_position_measured=False,
                            reference_basis="operator_prepared_existing_home_before_launch")

    def tracker(self, *, channel: int, pump: str, diameter_mm: float, capacity_ml: float, source: str, run_id: str | None) -> "ChannelTracker":
        return ChannelTracker(self, channel=channel, identity=motion_fields(pump, diameter_mm, capacity_ml), source=source, run_id=run_id)

    def begin_motion(self, channel: int, identity: dict[str, Any], direction: str, volume_ml: float, *,
                     operation_id: str, source: str, run_id: str | None,
                     workflow_phase: str | None = None, cycle_number: int | None = None) -> dict[str, Any]:
        """Durable intent BEFORE dispatch. Raises if it cannot be recorded."""
        if direction not in {"withdraw", "infuse"}:
            raise ValueError("direction must be withdraw or infuse")
        volume = to_nl(volume_ml)
        if volume <= 0:
            raise ValueError("Tracked transfer volume must be positive")
        if channel not in CHANNELS:
            # Legacy channel 0 sends unaddressed commands: the pump's own default
            # channel moves, so neither syringe's displacement can be attributed.
            for ch in CHANNELS:
                state = self._current(ch)
                if state["home_defined"] and (state["position_known"] or state["pending_motion"]):
                    self._commit_unknown(ch, state, f"{source} sent an unaddressed (channel 0) transfer; "
                                         "the syringe that moved is unknown", operation_id=operation_id)
            return {"channel": channel, "operation_id": operation_id, "finalized": True, "untracked": True}
        state = self._current(channel)
        if state["pending_motion"]:
            state = self._commit_unknown(channel, state, "an earlier tracked motion was never finalized")
        known = state["position_known"]
        if known:
            changes = identity_changes(state["configuration_identity"], identity)
            if changes:
                state = self._commit_unknown(
                    channel, state,
                    f"{source} moved this channel with settings that differ from its HOME reference "
                    f"({'; '.join(changes)}); displacement can no longer be compared",
                    operation_id=operation_id,
                )
                known = False
        delta = volume if direction == "withdraw" else -volume
        intent = {
            "operation_id": operation_id, "direction": direction, "volume_ml": to_ml(volume),
            "volume_nl": volume, "signed_delta_nl": delta, "source": source, "run_id": run_id,
            "workflow_phase": workflow_phase, "cycle_number": cycle_number,
            "displacement_before_nl": state["current_displacement_nl"] if known else None,
            "position_known_before": known, "started_at_utc": _now(), **identity,
        }
        self._commit(channel, dict(state, pending_motion=True, pending_operation=intent),
                     "chemyx_position_motion_intent", operation_id=operation_id, direction=direction,
                     volume_ml=to_ml(volume), source=source, motion_run_id=run_id, position_known_before=known)
        return {"channel": channel, "operation_id": operation_id, "delta_nl": delta, "known": known,
                "direction": direction, "volume_nl": volume, "source": source, "run_id": run_id,
                "workflow_phase": workflow_phase, "cycle_number": cycle_number, "finalized": False}

    def complete_motion(self, token: dict[str, Any]) -> bool:
        """Advance the displacement from completion evidence. Never raises."""
        if token["finalized"]:
            return True
        ch = token["channel"]
        try:
            state = self._current(ch)
            pending = state["pending_operation"] or {}
            if not state["pending_motion"] or pending.get("operation_id") != token["operation_id"]:
                self._commit_unknown(ch, state, "completion evidence does not match the pending motion intent",
                                     operation_id=token["operation_id"])
                token["finalized"] = True
                return False
            before = state["current_displacement_nl"] if token["known"] and state["position_known"] else None
            after = None if before is None else before + token["delta_nl"]
            movement = {
                "operation_id": token["operation_id"], "direction": token["direction"],
                "volume_ml": to_ml(token["volume_nl"]), "source": token["source"],
                "run_id": token["run_id"], "workflow_phase": token["workflow_phase"],
                "cycle_number": token["cycle_number"], "completed_at_utc": _now(),
                "displacement_before_ml": to_ml(before), "displacement_after_ml": to_ml(after),
                "completion_evidence": COMPLETION_EVIDENCE,
            }
            new = dict(state, pending_motion=False, pending_operation=None)
            if after is not None:
                totals = dict(state["tracked_since_home"])
                key = "withdrawn_nl" if token["direction"] == "withdraw" else "infused_nl"
                totals[key] += token["volume_nl"]
                totals["movements"] += 1
                new.update(current_displacement_nl=after, last_confirmed_movement=movement, tracked_since_home=totals)
            self._commit(ch, new, "chemyx_position_motion_completed", operation_id=token["operation_id"],
                         direction=token["direction"], volume_ml=to_ml(token["volume_nl"]),
                         position_known=after is not None, displacement_after_ml=to_ml(after),
                         completion_evidence=COMPLETION_EVIDENCE, motion_run_id=token["run_id"])
            token["finalized"] = True
            return True
        except Exception as exc:  # noqa: BLE001 - the pending intent already reads UNKNOWN
            self.notices.append(f"Channel {ch}: completion could not be recorded ({type(exc).__name__}: {exc})")
            self.mark_unknown(ch, f"completion of operation {token['operation_id']} could not be recorded: {exc}")
            token["finalized"] = True
            return False

    def cancel_motion(self, token: dict[str, Any], reason: str) -> bool:
        """Motion provably never started: clear the intent. Never raises."""
        if token["finalized"]:
            return True
        ch = token["channel"]
        try:
            state = self._current(ch)
            if not state["pending_motion"] or (state["pending_operation"] or {}).get("operation_id") != token["operation_id"]:
                self._commit_unknown(ch, state, "cancellation does not match the pending motion intent")
            else:
                self._commit(ch, dict(state, pending_motion=False, pending_operation=None),
                             "chemyx_position_motion_cancelled", operation_id=token["operation_id"], reason=reason)
            token["finalized"] = True
            return True
        except Exception:  # noqa: BLE001 - a stranded intent reads UNKNOWN
            token["finalized"] = True
            return False

    def mark_unknown(self, ch: int, reason: str, **fields: Any) -> bool:
        """Fail closed. Never raises; a write failure leaves any intent pending."""
        try:
            self._commit_unknown(ch, self._current(ch), reason, **fields)
            return True
        except Exception:  # noqa: BLE001
            return False

    def record_event(self, event_type: str, **fields: Any) -> dict[str, Any]:
        """Audit-only journal record (prepare, STOP, refusals); no state change."""
        self._require_owner()
        return self._journal.append(event_type, **fields)


class ChannelTracker:
    """Binds one channel's pump/syringe context for ``run_safe_metered_move``."""

    def __init__(self, ledger: PositionLedger, *, channel: int, identity: dict[str, Any], source: str, run_id: str | None) -> None:
        self.ledger = ledger
        self.channel = channel
        self.identity = identity
        self.source = source
        self.run_id = run_id

    def begin(self, direction: str, volume_ml: float, *, operation_id: str, workflow_phase: str | None = None, cycle_number: int | None = None) -> dict[str, Any]:
        return self.ledger.begin_motion(self.channel, self.identity, direction, volume_ml, operation_id=operation_id,
                                        source=self.source, run_id=self.run_id, workflow_phase=workflow_phase,
                                        cycle_number=cycle_number)

    def complete(self, token: dict[str, Any]) -> bool:
        return self.ledger.complete_motion(token)

    def cancel(self, token: dict[str, Any], reason: str) -> bool:
        return self.ledger.cancel_motion(token, reason)

    def uncertain(self, token: dict[str, Any], reason: str) -> bool:
        token["finalized"] = True
        if token.get("untracked"):
            return True  # both channels were already made UNKNOWN before dispatch
        return self.ledger.mark_unknown(token["channel"], reason, operation_id=token["operation_id"])


def invalidate_untracked_motion(channels: list[int] | tuple[int, ...], *, source: str, mock: bool = False) -> None:
    """Before an un-ledgered live move: no saved HOME may survive it.

    Raises if the invalidation cannot be recorded, so the move must not start.
    """
    root = state_root(mock)
    if not root.is_dir():
        return
    with PositionLedger(root, owner=f"untracked motion: {source}") as ledger:
        for ch in channels:
            state = ledger.state(ch)
            if state["home_defined"] and (state["position_known"] or state["pending_motion"]):
                ledger._commit_unknown(ch, state, f"{source} moved the pump outside HOME tracking")
