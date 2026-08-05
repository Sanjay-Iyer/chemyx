"""Stage 3: limited homing and two conservative needle DOWN/UP cycles."""

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

from arduino.python.config import require_live, test3_limit_preflight_missing, test3_missing
from arduino.python.protocol import bool_field
from arduino.python.results import matching_live_result, unresolved_live_motion_failure
from arduino.python.workflows import HardDeadline, run_test_03, validate_axis_geometry

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "arduino.example.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded vertical needle-axis hello world")
    add_standard_arguments(parser, motion_capable=True, default_config=DEFAULT_CONFIG)
    return parser


def _mock_cfg(cfg: dict) -> dict:
    value = deepcopy(cfg)
    value["firmware"]["motion_enabled"] = True
    value["firmware"]["limits_enabled"] = True
    value["signal_interface"]["signal_inverted"] = False
    value["driver"]["enable_active_low"] = True
    value["limits"].update({"upper_active_low": False, "lower_active_low": False})
    value["motion"].update(
        {
            "home_backoff_steps": 100,
            "safe_up_position_steps": 100,
            "test_down_position_steps": 500,
            "maximum_travel_steps": 1000,
            "maximum_speed_steps_s": 300,
            "maximum_acceleration_steps_s2": 300,
            "home_speed_steps_s": 100,
        }
    )
    return value


def _interactive_limit_check(controller, deadline) -> None:
    for name in ("upper", "lower"):
        for expected, instruction in (
            (True, f"Activate and hold the {name} normally closed limit switch."),
            (False, f"Release the {name} limit switch."),
        ):
            print(instruction)
            stable_samples = 0
            while True:
                deadline.check("Test 3 limit-switch verification")
                status = controller.status()
                if bool_field(status, "limit_up") and bool_field(status, "limit_down"):
                    raise RuntimeError("Both limit switches appear active")
                if bool_field(status, f"limit_{name}") is expected:
                    stable_samples += 1
                    if stable_samples >= 5:
                        break
                else:
                    stable_samples = 0
                __import__("time").sleep(min(0.05, deadline.remaining_s))


def _mock_limit_check(controller, _deadline) -> None:
    transport = controller.transport
    transport.limit_up = True
    assert bool_field(controller.status(), "limit_up")
    transport.limit_up = False
    assert not bool_field(controller.status(), "limit_up")
    transport.limit_down = True
    assert bool_field(controller.status(), "limit_down")
    transport.limit_down = False
    assert not bool_field(controller.status(), "limit_down")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    cfg = load_cli_config(args)
    if args.list_ports:
        return 0
    mode = execution_mode(args)
    test2_valid = matching_live_result(cfg["results"]["run_root_dir"], "test_02_unloaded_motor", cfg) is not None
    if mode == "validate_only":
        print("Configuration syntax valid. No port was opened.")
        print(f"Live Test 3 missing requirements: {len(test3_missing(cfg, test2_record_valid=test2_valid))}")
        return 0
    if mode == "dry_run":
        print("Test 3 preflight only. No port was opened and no motion occurred.")
        missing = test3_missing(cfg, test2_record_valid=test2_valid, limit_record_valid=False)
        for item in missing:
            print(f"MISSING: {item}")
        return 0
    run_cfg = _mock_cfg(cfg) if mode == "mock" else cfg
    failure_context = {"motion_attempted": False}

    if args.preflight_only:
        def preflight_runner(_run_dir):
            missing = test3_limit_preflight_missing(
                run_cfg, test2_record_valid=test2_valid or mode == "mock"
            )
            if mode == "live":
                require_live("TEST 3 LIMIT-SWITCH PREFLIGHT", missing)
                confirm_live("RUN ARDUINO TEST 3 PREFLIGHT")
            deadline = HardDeadline(min(59.0, run_cfg["arduino"]["overall_timeout_s"]))
            with controller_session(
                run_cfg, mode, allow_motion=False, apply_runtime_config=True
            ) as controller:
                failure_context["firmware_version"] = controller.identity.get("version")
                status = controller.status()
                from arduino.python.workflows import verify_firmware_motion_configuration

                verify_firmware_motion_configuration(status, run_cfg, include_limits=True)
                (_mock_limit_check if mode == "mock" else _interactive_limit_check)(
                    controller, deadline
                )
                final = controller.status()
                events = serialized_events(controller)
                return (
                    final,
                    controller.identity.get("version"),
                    {"upper_and_lower_active_release_stable_samples": 5},
                    events,
                )

        return run_recorded(
            test_name="test_03_limit_switch_preflight",
            mode=mode,
            cfg=run_cfg,
            runner=preflight_runner,
            failure_context=failure_context,
        )

    limit_valid = matching_live_result(
        cfg["results"]["run_root_dir"], "test_03_limit_switch_preflight", cfg
    ) is not None

    def runner(_run_dir):
        if mode == "live":
            missing = test3_missing(
                run_cfg,
                test2_record_valid=test2_valid,
                limit_record_valid=limit_valid,
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
            state = run_test_03(
                controller,
                run_cfg,
                deadline,
                # The durable preflight is a prerequisite, and the bounded
                # check is deliberately repeated immediately before motion.
                verify_limits=_mock_limit_check if mode == "mock" else _interactive_limit_check,
            )
            return state, controller.identity.get("version"), {"limit_state_changes_observed": True}, serialized_events(controller)

    return run_recorded(
        test_name="test_03_needle_axis",
        mode=mode,
        cfg=run_cfg,
        runner=runner,
        failure_context=failure_context,
    )


if __name__ == "__main__":
    raise SystemExit(main())
