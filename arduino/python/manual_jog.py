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
