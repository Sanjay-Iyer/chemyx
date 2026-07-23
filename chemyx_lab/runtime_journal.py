"""Durable append-only event journal and journal-backed state recorder."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol

from .runtime_state import (
    JOURNAL_SCHEMA_VERSION,
    replay_journal,
    write_state_atomic,
)


class JournalError(RuntimeError):
    """Base class for required persistence failures."""


class JournalWriteError(JournalError):
    """Raised when a record cannot be made durable."""


class SnapshotWriteError(JournalError):
    """Raised when the rebuildable state projection cannot be replaced."""


class TerminalJournalError(JournalError):
    """Raised when an event would violate the terminal-event barrier."""


class EventRecorder(Protocol):
    def record(self, event_type: str, **fields: Any) -> dict[str, Any]: ...


def sanitize_error_message(value: Any, limit: int = 1000) -> str:
    """Return a bounded, single-line diagnostic suitable for the journal."""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value)).strip()
    return text[:limit]


def discover_git_commit(repository_root: Path) -> str | None:
    """Read the current loose Git ref without invoking Git or any network."""
    try:
        head = (Path(repository_root) / ".git" / "HEAD").read_text(
            encoding="ascii"
        ).strip()
        if head.startswith("ref: "):
            ref_path = Path(repository_root) / ".git" / head[5:]
            value = ref_path.read_text(encoding="ascii").strip()
        else:
            value = head
        if re.fullmatch(r"[0-9a-fA-F]{40}", value):
            return value.lower()
    except OSError:
        pass
    return None


class OperationJournal:
    """Write one flushed and fsynced JSON object per line."""

    def __init__(
        self,
        path: Path,
        run_id: str,
        *,
        monotonic_fn: Callable[[], float] = time.monotonic,
        utc_now_fn: Callable[[], datetime] | None = None,
        event_id_fn: Callable[[], str] | None = None,
        software_version: str | None = None,
    ) -> None:
        self.path = Path(path)
        self.run_id = str(run_id)
        self._monotonic_fn = monotonic_fn
        self._utc_now_fn = utc_now_fn or (lambda: datetime.now(timezone.utc))
        self._event_id_fn = event_id_fn or (lambda: str(uuid.uuid4()))
        self._started = float(monotonic_fn())
        self._sequence = 0
        self._terminal = False
        self.software_version = software_version
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_size:
            replay = replay_journal(self.path)
            if replay.errors:
                raise JournalWriteError(
                    "Cannot append to an invalid existing journal: "
                    + "; ".join(issue.message for issue in replay.errors)
                )
            self._sequence = replay.state.last_applied_sequence
            self._terminal = replay.state.terminal_status is not None

    @property
    def next_sequence(self) -> int:
        return self._sequence + 1

    def append(self, event_type: str, **fields: Any) -> dict[str, Any]:
        post_terminal = bool(fields.pop("post_terminal_cleanup", False))
        if self._terminal and not post_terminal:
            raise TerminalJournalError(
                "The journal already has a terminal event; no further normal "
                "events may be appended"
            )
        if post_terminal and fields.get("lifecycle_state") == "dispatch_started":
            raise TerminalJournalError(
                "A physical dispatch cannot begin after the terminal event"
            )

        now = self._utc_now_fn()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        record: dict[str, Any] = {
            "schema_version": JOURNAL_SCHEMA_VERSION,
            "run_id": self.run_id,
            "sequence": self.next_sequence,
            "event_id": self._event_id_fn(),
            "timestamp_utc": now.astimezone(timezone.utc).isoformat(),
            "monotonic_elapsed_seconds": max(
                0.0, float(self._monotonic_fn()) - self._started
            ),
            "event_type": str(event_type),
        }
        if self.software_version:
            record["software_version"] = self.software_version
        record.update(
            {key: value for key, value in fields.items() if value is not None}
        )
        if "error_message" in record:
            record["error_message"] = sanitize_error_message(
                record["error_message"]
            )
        try:
            payload = (
                json.dumps(
                    record,
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise JournalWriteError(
                f"Journal record is not serializable: {exc}"
            ) from exc

        try:
            self._durable_write(payload)
        except BaseException as exc:
            if isinstance(exc, JournalWriteError):
                raise
            raise JournalWriteError(
                f"Could not durably append journal sequence "
                f"{self.next_sequence}: {type(exc).__name__}: {exc}"
            ) from exc
        self._sequence += 1
        if event_type == "terminal":
            self._terminal = True
        return record

    def _durable_write(self, payload: bytes) -> None:
        with self.path.open("ab", buffering=0) as handle:
            written = handle.write(payload)
            if written != len(payload):
                raise JournalWriteError(
                    f"Short journal write: expected {len(payload)} bytes, "
                    f"wrote {written}"
                )
            handle.flush()
            os.fsync(handle.fileno())


class RunRecorder:
    """Append authoritative events and then atomically refresh derived state."""

    def __init__(self, journal: OperationJournal, state_path: Path) -> None:
        self.journal = journal
        self.state_path = Path(state_path)

    @property
    def run_id(self) -> str:
        return self.journal.run_id

    def record(self, event_type: str, **fields: Any) -> dict[str, Any]:
        record = self.journal.append(event_type, **fields)
        replay = replay_journal(self.journal.path)
        if replay.errors:
            raise SnapshotWriteError(
                "Newly written journal did not replay cleanly: "
                + "; ".join(issue.message for issue in replay.errors)
            )
        try:
            write_state_atomic(self.state_path, replay.state)
        except BaseException as exc:
            if isinstance(exc, SnapshotWriteError):
                raise
            raise SnapshotWriteError(
                f"Journal sequence {record['sequence']} is durable, but the "
                "state snapshot could not be replaced: "
                f"{type(exc).__name__}: {exc}"
            ) from exc
        return record
