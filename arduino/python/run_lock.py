"""Cross-platform, Windows-compatible exclusive process lock."""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from .errors import ProcessLockError


class PortProcessLock:
    def __init__(self, port: str, directory: str | Path | None = None) -> None:
        digest = hashlib.sha256(str(port).casefold().encode("utf-8")).hexdigest()[:16]
        root = Path(directory) if directory else Path(tempfile.gettempdir()) / "chemyx_arduino_locks"
        self.path = root / f"arduino_{digest}.lock"
        self._handle = None

    def acquire(self) -> "PortProcessLock":
        if self._handle is not None:
            return self
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+b")
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if __import__("os").name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, IOError) as exc:
            handle.close()
            raise ProcessLockError(
                f"Arduino port is already locked by another process: {self.path}"
            ) from exc
        self._handle = handle
        return self

    def release(self) -> None:
        if self._handle is None:
            return
        handle, self._handle = self._handle, None
        try:
            handle.seek(0)
            if __import__("os").name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()

    def __enter__(self) -> "PortProcessLock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False

