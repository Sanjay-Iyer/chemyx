"""First-pass pump/NMR workflow helpers derived from the Si6 SOP."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Iterable


@dataclass(frozen=True)
class WorkflowStep:
    kind: str
    label: str
    direction: str | None = None
    volume_ml: float | None = None
    pause_seconds: float | None = None
    needle_position: str | None = None


@dataclass(frozen=True)
class WorkflowSettings:
    cycles: int = 1
    rate_ml_min: float = 1.0
    volume_scale: float = 1.0
    pause_scale: float = 0.0
    nmr_pause_seconds: float = 300.0
    move_start_delay: float = 0.0


def build_si6_sampling_steps(cycles=1) -> list[WorkflowStep]:
    """Build one or more SOP sampling cycles.

    This intentionally models the current manual process, including the future
    needle-lift actuator as explicit placeholder steps.
    """
    steps: list[WorkflowStep] = []
    for idx in range(1, int(cycles) + 1):
        prefix = f"cycle {idx}"
        steps.extend(
            [
                WorkflowStep(
                    "pump",
                    f"{prefix}: withdraw transfer volume with needle out",
                    "withdraw",
                    8.0,
                ),
                WorkflowStep(
                    "needle",
                    f"{prefix}: lower needle into solution",
                    needle_position="down",
                ),
                WorkflowStep(
                    "pump",
                    f"{prefix}: withdraw sample volume",
                    "withdraw",
                    5.0,
                ),
                WorkflowStep(
                    "pause",
                    f"{prefix}: hold before NMR acquisition",
                    pause_seconds=300.0,
                ),
                WorkflowStep("nmr", f"{prefix}: run or ingest NMR"),
                WorkflowStep("pump", f"{prefix}: infuse combined volume", "infuse", 13.0),
                WorkflowStep(
                    "needle",
                    f"{prefix}: raise needle out of solution",
                    needle_position="up",
                ),
                WorkflowStep(
                    "pump",
                    f"{prefix}: withdraw line conditioning volume",
                    "withdraw",
                    5.0,
                ),
                WorkflowStep(
                    "pump",
                    f"{prefix}: infuse line conditioning volume",
                    "infuse",
                    5.0,
                ),
            ]
        )
    return steps


def execute_workflow(
    steps: Iterable[WorkflowStep],
    pump,
    settings: WorkflowSettings,
    logger: Callable[[str], None] = print,
    nmr_callback: Callable[[WorkflowStep], None] | None = None,
    sleep: Callable[[float], None] = time.sleep,
):
    """Execute workflow steps against a real or mock pump object."""
    for index, step in enumerate(steps, start=1):
        logger(f"[{index:02d}] {step.label}")

        if step.kind == "needle":
            logger(f"     needle placeholder -> {step.needle_position}")
            continue

        if step.kind == "pause":
            seconds = float(step.pause_seconds or 0.0) * float(settings.pause_scale)
            logger(f"     pause {seconds:.1f} s")
            if seconds > 0:
                sleep(seconds)
            continue

        if step.kind == "nmr":
            if nmr_callback is None:
                logger("     NMR placeholder -> no instrument call configured")
            else:
                nmr_callback(step)
            continue

        if step.kind == "pump":
            volume = float(step.volume_ml or 0.0) * float(settings.volume_scale)
            if volume <= 0:
                logger("     skipped zero-volume pump move")
                continue
            move = pump.infuse if step.direction == "infuse" else pump.withdraw
            response = move(
                volume,
                rate=settings.rate_ml_min,
                start_delay=settings.move_start_delay,
            )
            logger(
                f"     {step.direction} {volume:.4g} mL at "
                f"{settings.rate_ml_min:.4g} mL/min -> {response!r}"
            )
            # For this first workflow we command target volume and then issue a
            # stop as an explicit cleanup/safety command. The pump should stop
            # itself at target volume on real hardware.
            logger(f"     stop -> {pump.stop()!r}")
            continue

        raise ValueError(f"Unknown workflow step kind: {step.kind}")
