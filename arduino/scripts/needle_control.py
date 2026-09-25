"""Supervised D3 STEP / D4 DIR needle operation with durable logical state."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import _bootstrap  # noqa: F401
from _common import confirm_live, controller_session

from arduino.python.config import load_arduino_config
from arduino.python.errors import MotionInterlockError, PositionUncertainError
from arduino.python.manual_jog import execute_manual_jog, manual_runtime_config, plan_manual_jog
from arduino.python.needle_state import NeedleStateStore, TrackedNeedle


DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "arduino.local.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "confirm-home", "up", "down", "return-home", "stop", "manual-up", "manual-down"))
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--steps", type=int, help="Positive physical STEP pulses for one manual jog")
    parser.add_argument("--speed", type=int, default=100, help="Manual jog ceiling in steps/s (default: 100)")
    parser.add_argument("--acceleration", type=int, default=500, help="Manual jog ramp in steps/s^2 (default: 500)")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--mock", action="store_true")
    args = parser.parse_args(argv)
    cfg = load_arduino_config(args.config)
    mode_name = "live" if args.live else "mock"
    action = args.action
    if action in ("manual-up", "manual-down"):
        if args.steps is None:
            parser.error("manual-up/manual-down require --steps")
        try:
            plan = plan_manual_jog(action.removeprefix("manual-"), args.steps, args.speed, args.acceleration)
            manual_cfg = manual_runtime_config(cfg, plan)
        except (ValueError, MotionInterlockError) as exc:
            parser.error(str(exc))
        print(f"Manual {plan.direction.upper()}: exactly {abs(plan.signed_steps)} STEP pulses")
        print(f"Signed command: {plan.command}")
        print(f"Requested speed ceiling: {plan.speed_steps_s} steps/s; ramp: {plan.acceleration_steps_s2} steps/s^2")
        print("Inspect clear travel and keep the 24 V driver-power disconnect within reach.")
        if args.live:
            confirm_live(f"Manual {plan.direction.upper()} {abs(plan.signed_steps)} steps")
        with controller_session(manual_cfg, mode_name, allow_motion=True, apply_runtime_config=True) as controller:
            state_path = Path(cfg["results"]["run_root_dir"]) / "mock_needle_state.json" if args.mock else Path(cfg["needle"]["state_path"])
            try:
                result = execute_manual_jog(controller, plan, state_path=state_path)
                print(f"Arduino: {result.ack_raw}")
                print(f"Arduino: {result.done_raw}")
            finally:
                # Leave volatile firmware motion commissioning off after this one move.
                disarmed = deepcopy(manual_cfg)
                disarmed["firmware"]["motion_enabled"] = False
                controller.configure_runtime(disarmed)
        print("Logical position is now UNKNOWN; manual jog did not establish HOME.")
        return 0
    if args.steps is not None or args.speed != 100 or args.acceleration != 500:
        parser.error("--steps/--speed/--acceleration are only for manual-up/manual-down")
    if args.live and action not in ("status", "stop"):
        cfg["firmware"]["motion_enabled"] = True
        confirm_live("Set current needle position as HOME=0" if action == "confirm-home" else f"Move needle {action.upper()}")
    if args.mock:
        cfg["firmware"]["motion_enabled"] = True
        cfg["needle"].update({"steps_per_unit": cfg["needle"].get("steps_per_unit") or 20,
                              "up_step_sign": cfg["needle"].get("up_step_sign") or 1})
        cfg["motion"].update({"maximum_speed_steps_s": cfg["motion"].get("maximum_speed_steps_s") or 100,
                              "maximum_acceleration_steps_s2": cfg["motion"].get("maximum_acceleration_steps_s2") or 300})
    with controller_session(cfg, mode_name, allow_motion=action in ("up", "down", "return-home"), apply_runtime_config=True) as controller:
        state_path = Path(cfg["results"]["run_root_dir"]) / "mock_needle_state.json" if args.mock else Path(cfg["needle"]["state_path"])
        if action == "status":
            saved = NeedleStateStore(state_path).load()
            firmware = controller.status()
            print(f"Last known needle position: {saved.logical_position if saved.logical_position is not None else 'unknown'}; "
                  f"valid={str(saved.position_valid).lower()}; source=software_estimate; "
                  f"firmware moving={firmware['moving']}; fault={firmware['fault']}")
            return 0
        try:
            needle = TrackedNeedle(controller, cfg, state_path=state_path)
        except PositionUncertainError:
            if action != "confirm-home":
                raise
            backup = NeedleStateStore(state_path).quarantine_corrupt()
            print(f"Corrupt state retained at {backup}; physically inspected HOME will establish a new reference")
            needle = TrackedNeedle(controller, cfg, state_path=state_path)
        if action == "confirm-home":
            needle.confirm_home(operator_confirmed=True)
        elif action == "up":
            needle.needle_up()
        elif action == "down":
            needle.needle_down()
        elif action == "return-home":
            needle.return_to_home()
        elif action == "stop":
            needle.stop()
        status = needle.status()
        print(f"Needle logical position: {status['logical_position']}; valid={status['position_valid']}; "
              f"source={status['position_source']}; firmware moving={status['moving']}; fault={status['fault']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
