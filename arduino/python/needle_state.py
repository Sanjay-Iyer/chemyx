"""Durable, software-only position reference for the D3/D4 needle axis.

This is commanded-position bookkeeping, not feedback from an encoder or switch.
The live serial port's process lock must be held by the caller while using it.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from .errors import MotionInterlockError, PositionUncertainError, ProtocolError
from .protocol import bool_field


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class NeedleState:
    version: int = 1
    logical_position: int | None = None
    home_position: int = 0
    position_valid: bool = False
    last_updated: str = ""
    reason: str = "uninitialized"
    steps_per_unit: int | None = None
    up_step_sign: int | None = None


class NeedleStateStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> NeedleState:
        if not self.path.exists():
            return NeedleState()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or set(data) != set(NeedleState.__dataclass_fields__):
                raise ValueError("unexpected state fields")
            state = NeedleState(**data)
            if type(state.version) is not int or state.version != 1:
                raise ValueError("unsupported state version")
            if type(state.home_position) is not int or state.home_position != 0:
                raise ValueError("HOME must be zero")
            if state.logical_position is not None and type(state.logical_position) is not int:
                raise ValueError("logical_position must be an integer or null")
            if type(state.position_valid) is not bool or (state.position_valid and state.logical_position is None):
                raise ValueError("invalid position_valid / logical_position pair")
            if not isinstance(state.last_updated, str) or not isinstance(state.reason, str):
                raise ValueError("invalid timestamp or reason")
            if state.steps_per_unit is not None and (type(state.steps_per_unit) is not int or state.steps_per_unit <= 0):
                raise ValueError("invalid steps_per_unit")
            if state.up_step_sign is not None and (type(state.up_step_sign) is not int or state.up_step_sign not in (-1, 1)):
                raise ValueError("invalid up_step_sign")
            return state
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise PositionUncertainError(
                f"Needle state file {self.path} is corrupt or unreadable; no motion allowed until inspected: {exc}"
            ) from exc

    def save(self, state: NeedleState) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.path.parent,
                prefix=f".{self.path.name}.", suffix=".tmp", delete=False,
            ) as handle:
                tmp_path = Path(handle.name)
                json.dump(asdict(state), handle, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    def quarantine_corrupt(self) -> Path:
        """Retain invalid bytes for review before an explicit HOME re-reference."""
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        try:
            self.load()
        except PositionUncertainError:
            backup = self.path.with_name(f"{self.path.name}.corrupt-{uuid4().hex}.bak")
            self.path.replace(backup)
            return backup
        raise MotionInterlockError("Needle state is valid; corrupt-state recovery is not appropriate")


class TrackedNeedle:
    """Logical UP-positive API around the existing serial JOG implementation."""

    def __init__(self, controller: Any, cfg: dict[str, Any], *, state_path: str | Path | None = None) -> None:
        needle = cfg["needle"]
        if needle.get("steps_per_unit") is None or needle.get("up_step_sign") is None:
            raise MotionInterlockError("Set verified needle.steps_per_unit and needle.up_step_sign before use")
        if cfg["motion"].get("maximum_speed_steps_s") is None or cfg["motion"].get("maximum_acceleration_steps_s2") is None:
            raise MotionInterlockError("Set motion.maximum_speed_steps_s and maximum_acceleration_steps_s2 before use")
        self.controller = controller
        self.minimum = int(needle["min_position"])
        self.maximum = int(needle["max_position"])
        self.steps_per_unit = int(needle["steps_per_unit"])
        self.up_step_sign = int(needle["up_step_sign"])
        self.speed = int(cfg["motion"]["maximum_speed_steps_s"])
        self.acceleration = int(cfg["motion"]["maximum_acceleration_steps_s2"])
        self.store = NeedleStateStore(state_path or needle["state_path"])
        self.state = self.store.load()
        if self.state.position_valid and (
            self.state.steps_per_unit != self.steps_per_unit
            or self.state.up_step_sign != self.up_step_sign
            or not self.minimum <= self.state.logical_position <= self.maximum
        ):
            self.state = replace(self.state, position_valid=False, last_updated=_timestamp(), reason="calibration_or_limits_changed")
            self.store.save(self.state)
        print(f"Last known needle position: {self.state.logical_position if self.state.logical_position is not None else 'unknown'}; "
              f"position state: {'valid software estimate' if self.state.position_valid else 'UNKNOWN'}")

    @property
    def position_certain(self) -> bool:
        return self.state.position_valid

    @property
    def is_open(self) -> bool:
        return self.controller.is_open

    def __getattr__(self, name: str) -> Any:
        return getattr(self.controller, name)

    def _save(self, **updates: Any) -> None:
        new_state = replace(self.state, last_updated=_timestamp(), **updates)
        self.store.save(new_state)
        self.state = new_state

    def _assert_unchanged(self) -> None:
        if self.store.load() != self.state:
            raise PositionUncertainError("Needle state changed on disk; reopen controller and inspect")

    def confirm_home(self, *, operator_confirmed: bool = False) -> None:
        if not operator_confirmed:
            raise MotionInterlockError("Physically inspect needle, then explicitly pass operator_confirmed=True")
        status = self.controller.status()
        if bool_field(status, "moving") or status.get("fault") != "NONE":
            raise MotionInterlockError("Cannot confirm HOME while firmware is moving or faulted")
        self._assert_unchanged()
        self._save(logical_position=0, position_valid=True, reason="operator_confirmed_home",
                   steps_per_unit=self.steps_per_unit, up_step_sign=self.up_step_sign)

    def status(self) -> dict[str, str]:
        status = dict(self.controller.status())
        status["logical_position"] = str(self.state.logical_position) if self.state.logical_position is not None else "unknown"
        status["position_valid"] = str(self.state.position_valid).lower()
        status["position_source"] = "software_estimate"
        return status

    def move_to(self, target: int, speed_steps_s: int | None = None) -> None:
        if type(target) is not int:
            raise ValueError("Logical needle target must be an integer")
        if not self.minimum <= target <= self.maximum:
            raise MotionInterlockError(
                f"Software needle limit exceeded: target {target} outside [{self.minimum}, {self.maximum}]"
            )
        if not self.state.position_valid or self.state.logical_position is None:
            raise PositionUncertainError("Needle position unknown; inspect and explicitly confirm_home first")
        self._assert_unchanged()
        delta = target - self.state.logical_position
        if delta == 0:
            return
        steps = delta * self.steps_per_unit * self.up_step_sign
        if abs(steps) > 200000:
            raise MotionInterlockError("Needle move exceeds firmware 200000-step command cap")
        selected_speed = speed_steps_s or self.speed
        if type(selected_speed) is not int or not 1 <= selected_speed <= 100:
            raise MotionInterlockError("Needle speed must be an integer from 1 to 100 steps/s")
        if self.acceleration <= 0:
            raise MotionInterlockError("Needle acceleration must be a positive integer")
        estimated_s = abs(steps) / selected_speed + 2 * selected_speed / self.acceleration + 5
        if estimated_s >= 120:
            raise MotionInterlockError("Requested needle move cannot fit the firmware 120-second timeout")
        firmware = self.controller.status()
        if bool_field(firmware, "moving") or firmware.get("fault") != "NONE":
            raise MotionInterlockError("Needle firmware is moving or faulted")
        if not bool_field(firmware, "enabled"):
            self.controller.enable()  # A software motion arm; ENA is not connected.
        self.controller._check_motion_allowed()
        raw_start = int(firmware["commanded_position_steps"])
        # Write uncertainty *before* dispatch. A Python crash or power failure
        # leaves an honest record rather than a falsely trusted old position.
        self._save(position_valid=False, reason=f"motion_pending_to_{target}")
        try:
            result = self.controller.jog(steps, selected_speed)
            if int(result.fields.get("position_steps", "invalid")) != raw_start + steps:
                raise ProtocolError("JOG completion step count does not match requested movement")
            self._save(logical_position=target, position_valid=True, reason="motion_completed")
        except BaseException:
            self._save(position_valid=False, reason="motion_failed_or_interrupted")
            raise

    def needle_up(self) -> None:
        if not self.state.position_valid or self.state.logical_position is None:
            raise PositionUncertainError("Needle position unknown; confirm HOME first")
        self.move_to(self.state.logical_position + 1)

    def needle_down(self) -> None:
        if not self.state.position_valid or self.state.logical_position is None:
            raise PositionUncertainError("Needle position unknown; confirm HOME first")
        self.move_to(self.state.logical_position - 1)

    def return_to_home(self) -> None:
        self.move_to(0)

    def home(self) -> None:
        self.return_to_home()

    def move_absolute(self, position_steps: int, speed_steps_s: int) -> None:
        """Compatibility name; argument is now a *logical* target, not motor steps."""
        self.move_to(position_steps, speed_steps_s)

    def stop(self) -> Any:
        moving = bool_field(self.controller.status(), "moving")
        result = self.controller.stop()
        if moving:
            self._save(position_valid=False, reason="stopped_during_motion")
        return result

    def stop_best_effort(self) -> bool:
        try:
            self.stop()
            return True
        except BaseException:
            self._save(position_valid=False, reason="stop_status_unknown")
            return False
