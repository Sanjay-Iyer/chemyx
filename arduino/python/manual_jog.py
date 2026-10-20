"""One-command, supervised relative jog without a logical HOME reference."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .controller import CommandResult, NeedleController
from .errors import MotionInterlockError, ProtocolError
from .needle_state import NeedleStateStore
from .protocol import bool_field


def tracked_relative_jog(controller, cfg, direction, *, state_path, steps=None):
    """One manual increment; share absolute state when calibrated.

    Before calibration the same state file holds a durable relative count, but
    position_valid stays false so an experiment cannot mistake it for HOME.
    An interrupted command is never a new relative origin.

    ``steps`` replaces the calibrated increment (``steps_per_unit``) for this
    one move. A whole number of increments keeps the count and any saved HOME.
    Any other size cannot be expressed as a count: the count becomes unknown
    and a saved HOME is cleared until the operator runs set-home again.
    """
    from dataclasses import replace
    from datetime import datetime, timezone
    from chemyx_lab.runtime_journal import OperationJournal
    from .errors import PositionUncertainError
    from .needle_state import TrackedNeedle

    n = cfg["needle"]
    if direction not in {"up", "down"}:
        raise ValueError("direction must be up or down")
    if not n.get("steps_per_unit") or n.get("up_step_sign") not in (-1, 1):
        raise MotionInterlockError("Relative movement needs the existing verified steps_per_unit and up_step_sign calibration")
    unit = n["steps_per_unit"]
    if steps is None:
        steps = unit
    if type(steps) is not int or steps < 1:
        raise ValueError("Needle steps must be a positive whole number of motor steps")
    # No limit switches: one custom move may not exceed the configured travel span.
    span = (n["max_position"] - n["min_position"]) * unit
    if steps > span:
        raise MotionInterlockError(f"{steps} steps exceeds the configured needle travel of {span} steps "
                                   f"({n['min_position']} to {n['max_position']} x {unit} steps)")
    units, remainder = divmod(steps, unit)
    store = NeedleStateStore(state_path)
    state = store.load()
    if store.home_pending_path.exists():
        raise PositionUncertainError("Arduino HOME verification is pending. Physically verify stopped HOME, then run needle_control.py set-home --live --config arduino/configs/arduino_real_COM3.yaml")
    if state.position_valid:
        # This class also checks saved geometry, limits, persistence and DONE.
        needle = TrackedNeedle(controller, cfg, state_path=state_path)
        start = needle.state.logical_position
    else:
        if state.reason not in {"uninitialized", "manual_relative_completed"}:
            raise PositionUncertainError(f"Arduino movement uncertain ({state.reason}) in {store.path}. Verify STOP with needle_control.py stop --live --config arduino/configs/arduino_real_COM3.yaml, physically verify HOME, then run needle_control.py set-home --live --config arduino/configs/arduino_real_COM3.yaml")
        if state.reason == "manual_relative_completed" and state.logical_position is not None and (state.steps_per_unit != unit or state.up_step_sign != n["up_step_sign"]):
            raise PositionUncertainError("Manual count calibration changed; physically verify HOME and run set-home before more movement")
        start = state.logical_position if state.logical_position is not None else 0
    target = None
    if remainder == 0:
        target = start + (units if direction == "up" else -units)
        if not n["min_position"] <= target <= n["max_position"]:
            raise MotionInterlockError(f"Arduino travel limit: {start:+d} -> {target:+d} exceeds [{n['min_position']}, {n['max_position']}]. Inspect physical travel; return within the tracked range. Do not reset HOME to bypass a travel limit.")
    journal = OperationJournal(store.path.with_name(store.path.stem + "_manual_operations.jsonl"), "manual_needle")
    journal.append("needle_relative_intent", from_position=start, target=target, steps=steps,
                   calibrated_home=state.position_valid, clears_home=state.position_valid and target is None)
    try:
        if state.position_valid and target is not None:
            needle.move_to(target)
        else:
            status = controller.status()
            if bool_field(status, "moving") or status.get("fault") != "NONE":
                raise MotionInterlockError(f"Arduino must be idle and fault-free: {status}")
            speed = cfg["motion"]["maximum_speed_steps_s"]
            # Reuse the existing bounded-jog validation, with physical polarity.
            plan_manual_jog(direction, steps, speed, cfg["motion"]["maximum_acceleration_steps_s2"])
            signed = steps * n["up_step_sign"] * (1 if direction == "up" else -1)
            pending = replace(state, logical_position=start, position_valid=False,
                              reason="manual_relative_pending", last_updated=datetime.now(timezone.utc).isoformat(),
                              steps_per_unit=unit, up_step_sign=n["up_step_sign"])
            store.save(pending)  # Durable uncertainty BEFORE dispatch.
            controller.enable()
            result = controller.jog(signed, speed)
            final = controller.status()
            if (int(result.fields.get("position_steps", "invalid")) != int(status["commanded_position_steps"]) + signed
                    or int(final.get("commanded_position_steps", "invalid")) != int(status["commanded_position_steps"]) + signed
                    or bool_field(final, "moving") or final.get("fault") != "NONE"):
                raise ProtocolError("Manual movement DONE/idle verification failed")
            store.save(replace(pending, logical_position=target, reason="manual_relative_completed"))
        journal.append("needle_relative_completed", target=target, steps=steps,
                       calibrated_home=state.position_valid and target is not None)
    except BaseException:
        # Persisted pending state survives interruption; never assert success.
        try:
            controller.stop_best_effort()
        except BaseException:
            pass  # Keep the original movement/persistence failure visible.
        raise
    return store.load()


@dataclass(frozen=True)
class ManualJogPlan:
    direction: str
    signed_steps: int
    speed_steps_s: int
    acceleration_steps_s2: int

    @property
    def command(self) -> str:
        return f"JOG {self.signed_steps} {self.speed_steps_s}"


def plan_manual_jog(
    direction: str, steps: int, speed_steps_s: int = 100,
    acceleration_steps_s2: int = 500,
) -> ManualJogPlan:
    if direction not in ("up", "down"):
        raise ValueError("Manual direction must be up or down")
    if type(steps) is not int or not 1 <= steps <= 200000:
        raise ValueError("Manual steps must be an integer from 1 to 200000")
    if type(speed_steps_s) is not int or not 1 <= speed_steps_s <= 100:
        raise ValueError("Manual speed must be an integer from 1 to 100 steps/s")
    if type(acceleration_steps_s2) is not int or not 1 <= acceleration_steps_s2 <= 50000:
        raise ValueError("Manual acceleration must be an integer from 1 to 50000 steps/s^2")
    estimated_s = steps / speed_steps_s + 2 * speed_steps_s / acceleration_steps_s2 + 5
    if estimated_s >= 120:
        raise ValueError("Manual jog cannot fit the firmware's 120-second movement timeout")
    return ManualJogPlan(
        direction, steps if direction == "up" else -steps,
        speed_steps_s, acceleration_steps_s2,
    )


def manual_runtime_config(cfg: dict[str, Any], plan: ManualJogPlan) -> dict[str, Any]:
    if cfg["firmware"].get("runtime_configurable") is not True:
        raise MotionInterlockError("Manual jog requires runtime-configurable firmware")
    if cfg["signal_interface"].get("signal_inverted") not in (None, False):
        raise MotionInterlockError("Manual jog requires the proven non-inverted D3/D4 polarity")
    manual_cfg = deepcopy(cfg)
    manual_cfg["firmware"]["motion_enabled"] = True
    manual_cfg["motion"]["maximum_speed_steps_s"] = plan.speed_steps_s
    manual_cfg["motion"]["maximum_acceleration_steps_s2"] = plan.acceleration_steps_s2
    return manual_cfg


def execute_manual_jog(
    controller: NeedleController, plan: ManualJogPlan, *, state_path: str | Path,
) -> CommandResult:
    status = controller.status()
    if bool_field(status, "moving") or status.get("fault") != "NONE":
        raise MotionInterlockError("Manual jog requires idle, fault-free firmware")
    raw_start = int(status["commanded_position_steps"])
    # Persist uncertainty before ENABLE/JOG. No prior HOME or valid state is required.
    NeedleStateStore(state_path).invalidate_for_manual_jog()
    try:
        controller.enable()
        result = controller.jog(plan.signed_steps, plan.speed_steps_s)
        if int(result.fields.get("position_steps", "invalid")) != raw_start + plan.signed_steps:
            raise ProtocolError("Manual JOG completion count differs from requested STEP pulses")
    except BaseException:
        # jog() already sends STOP if a dispatched move fails. Do not send a
        # second STOP for a command that was rejected before dispatch.
        try:
            controller.disable()
        except BaseException:
            pass
        raise
    controller.disable()
    return result
