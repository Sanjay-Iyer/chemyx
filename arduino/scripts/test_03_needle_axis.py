"""Stage 3: supervised software-HOME and two bounded needle DOWN/UP cycles."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import _bootstrap  # noqa: F401
from _common import (
    add_standard_arguments,
    configure_logging,
    confirm_live,
    controller_session,
    execution_mode,
    load_cli_config,
    run_recorded,
    serialized_events,
)

from arduino.python.config import require_live, test3_missing
from arduino.python.needle_state import TrackedNeedle
from arduino.python.results import matching_live_result, unresolved_live_motion_failure
from arduino.python.workflows import HardDeadline, run_test_03, validate_axis_geometry

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "arduino.example.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded vertical needle-axis hello world")
    add_standard_arguments(parser, motion_capable=True, default_config=DEFAULT_CONFIG)
    parser.add_argument("--confirm-home", action="store_true", help="After physical inspection, explicitly set software HOME=0")
    return parser


def _mock_cfg(cfg: dict) -> dict:
    value = deepcopy(cfg)
    value["firmware"]["motion_enabled"] = True
    value["firmware"]["limits_enabled"] = False
    value["signal_interface"]["signal_inverted"] = False
    value["needle"].update({"steps_per_unit": 20, "up_step_sign": 1})
    value["motion"].update(
        {
            "maximum_speed_steps_s": 100,
            "maximum_acceleration_steps_s2": 300,
        }
    )
    return value


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    cfg = load_cli_config(args)
    if args.list_ports:
        return 0
    mode = execution_mode(args)
    test2_valid = matching_live_result(cfg["results"]["run_root_dir"], "test_02_unloaded_motor", cfg) is not None
    if mode == "validate_only" and not args.preflight_only:
        print("Configuration syntax valid. No port was opened.")
        print(f"Live Test 3 missing requirements: {len(test3_missing(cfg, test2_record_valid=test2_valid))}")
        return 0
    if mode == "dry_run" and not args.preflight_only:
        print("Test 3 preflight only. No port was opened and no motion occurred.")
        missing = test3_missing(cfg, test2_record_valid=test2_valid)
        for item in missing:
            print(f"MISSING: {item}")
        return 0
    run_cfg = _mock_cfg(cfg) if mode == "mock" else cfg
    failure_context = {"motion_attempted": False}

    if args.preflight_only:
        print("Test 3 preflight: no physical switches or ENABLE connection are used.")
        missing = test3_missing(run_cfg, test2_record_valid=test2_valid or mode == "mock")
        for item in missing:
            print(f"MISSING: {item}")
        return 0 if mode != "live" or not missing else 1

    def runner(run_dir):
        if mode == "live":
            missing = test3_missing(
                run_cfg,
                test2_record_valid=test2_valid,
            )
            if unresolved_live_motion_failure(run_cfg["results"]["run_root_dir"], run_cfg):
                missing.append("Documented operator inspection after the latest failed live motion")
            require_live("LIVE NEEDLE AXIS TEST", missing)
            validate_axis_geometry(run_cfg)
            confirm_live("RUN ARDUINO TEST 3")
        deadline = HardDeadline(min(120.0, run_cfg["safety"]["hard_runtime_limit_s"]))
        with controller_session(
            run_cfg,
            mode,
            allow_motion=True,
            motion_dispatch_callback=lambda: failure_context.update(motion_attempted=True),
            apply_runtime_config=True,
        ) as controller:
            failure_context["firmware_version"] = controller.identity.get("version")
            needle = TrackedNeedle(controller, run_cfg, state_path=run_dir / "mock_needle_state.json" if mode == "mock" else None)
            if mode == "mock" or args.confirm_home:
                if mode == "live":
                    confirm_live("CONFIRM NEEDLE AT HOME ZERO")
                needle.confirm_home(operator_confirmed=True)
            state = run_test_03(needle, run_cfg, deadline)
            return state, controller.identity.get("version"), {"software_home_confirmed": mode == "mock" or args.confirm_home}, serialized_events(controller)

    return run_recorded(
        test_name="test_03_needle_axis",
        mode=mode,
        cfg=run_cfg,
        runner=runner,
        failure_context=failure_context,
    )


if __name__ == "__main__":
    raise SystemExit(main())
