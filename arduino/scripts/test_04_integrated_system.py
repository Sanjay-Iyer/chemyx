"""Stage 4: strictly sequential Arduino, Chemyx, and NMR integration."""

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

from arduino.mock.fake_instruments import FakeNmrClient
from arduino.python.config import require_live, test1_missing, test4_full_missing
from arduino.python.discovery import ensure_distinct_ports
from arduino.python.results import matching_live_result, unresolved_live_motion_failure
from arduino.python.workflows import (
    HardDeadline,
    SequentialInstrumentInterlock,
    existing_nmr_operation,
    resolve_integrated_settings,
    run_test_04a,
    run_test_04b,
)
from chemyx_lab import config as lab_config
from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.instruments.nmr import NmrRpcClient, NmrRpcConfig

DEFAULT_CONFIG = Path(__file__).resolve().parents[1] / "configs" / "integrated_hello_world.example.yaml"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Sequential Arduino/Chemyx/NMR hello world")
    add_standard_arguments(parser, motion_capable=True, default_config=DEFAULT_CONFIG)
    return parser


def _resolve(cfg: dict, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    repo = Path(__file__).resolve().parents[2] / path
    return repo if repo.exists() else Path(cfg["_source_path"]).parent / path


def _machine_objects(cfg: dict, mode: str):
    path_value = cfg["integrated"].get("machine_config_path")
    if not path_value:
        raise ValueError("integrated.machine_config_path is required")
    machine = lab_config.load_machine_config(_resolve(cfg, str(path_value)))
    pump_cfg = lab_config.load_pump_config(
        load_local=False,
        port=machine.chemyx.serial_port,
        baud_rate=machine.chemyx.baud_rate,
        timeout=machine.chemyx.timeout_seconds,
        response_delay=machine.chemyx.response_delay_seconds,
    )
    nmr_cfg = lab_config.load_nmr_settings(
        load_local=False,
        host=machine.nmr.host,
        port=machine.nmr.port,
        scheme=machine.nmr.scheme,
        timeout=machine.nmr.timeout_seconds,
        poll_seconds=machine.nmr.poll_seconds,
        max_wait_seconds=machine.nmr.max_wait_seconds,
    )
    if mode == "live" and not pump_cfg.port:
        raise ValueError("Chemyx COM port is missing from machine configuration")
    if mode == "live" and not nmr_cfg.host:
        raise ValueError("NMR host is missing from machine configuration")
    pump = Pump(
        port=pump_cfg.port,
        baud_rate=pump_cfg.baud_rate,
        channel=pump_cfg.channel,
        units=pump_cfg.units,
        timeout=pump_cfg.timeout,
        response_delay=pump_cfg.response_delay,
        mock=mode == "mock",
    )
    nmr = (
        FakeNmrClient()
        if mode == "mock"
        else NmrRpcClient(
            NmrRpcConfig(
                host=nmr_cfg.host,
                port=nmr_cfg.port,
                scheme=nmr_cfg.scheme,
                timeout=nmr_cfg.timeout,
                poll_seconds=nmr_cfg.poll_seconds,
                max_wait_seconds=nmr_cfg.max_wait_seconds,
            )
        )
    )
    return machine, pump, nmr


def _mock_cfg(cfg: dict) -> dict:
    value = deepcopy(cfg)
    value["firmware"]["motion_enabled"] = True
    value["firmware"]["limits_enabled"] = True
    value["signal_interface"]["signal_inverted"] = False
    value["driver"]["enable_active_low"] = True
    value["limits"].update({"upper_active_low": False, "lower_active_low": False})
    value["motion"].update(
        {
            "safe_up_position_steps": 100,
            "test_down_position_steps": 500,
            "maximum_travel_steps": 1000,
            "maximum_speed_steps_s": 300,
            "maximum_acceleration_steps_s2": 300,
            "home_speed_steps_s": 100,
        }
    )
    value["integrated"].update(
        {
            "machine_config_path": "configs/machines/00_machine.example.yaml",
            "experiment_config_path": "configs/experiments/02_si6_automated_nmr.yaml",
            "pump_action_cycle_index": 3,
            "pump_return_cycle_index": 9,
            "post_motion_settle_s": 0,
            "post_pump_settle_s": 0,
            "nmr_diagnostic": "mock_si6_configured_1d",
            "expected_nmr_artifact_suffix": ".dx",
            "test3_state_continuity_confirmed": True,
        }
    )
    return value


def _preflight_live_missing(cfg: dict) -> list[str]:
    missing = test1_missing(cfg)
    if not cfg["integrated"].get("machine_config_path"):
        missing.append("Machine configuration path")
    return missing


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = build_parser().parse_args(argv)
    cfg = load_cli_config(args)
    if args.list_ports:
        return 0
    mode = execution_mode(args)
    preflight_only = bool(args.preflight_only)
    if mode == "validate_only":
        print("Configuration syntax valid. No hardware endpoint was opened.")
        return 0
    if mode == "dry_run":
        print(f"Test 4{'A' if preflight_only else 'B'} dry run. No hardware endpoint was opened.")
        return 0
    run_cfg = _mock_cfg(cfg) if mode == "mock" else cfg
    combined_events: list[dict] = []
    failure_context = {"motion_attempted": False}

    if preflight_only:
        def preflight_runner(_run_dir):
            if mode == "live":
                require_live("INTEGRATED TEST 4A", _preflight_live_missing(run_cfg))
                confirm_live("RUN ARDUINO TEST 4A")
            deadline = HardDeadline(min(59.0, run_cfg["arduino"]["overall_timeout_s"]))
            machine, pump, nmr = _machine_objects(run_cfg, mode)
            deadline.check("Test 4A setup")
            with controller_session(run_cfg, mode, allow_motion=False) as controller:
                failure_context["firmware_version"] = controller.identity.get("version")
                arduino_port = getattr(controller.transport, "port", run_cfg["arduino"].get("port"))
                ensure_distinct_ports(arduino_port, machine.chemyx.serial_port)
                with pump:
                    report = run_test_04a(
                        controller,
                        pump,
                        nmr,
                        deadline,
                    )
                combined_events.extend(
                    [
                        {"instrument": "arduino", "operation": "PING_STATUS", "physical_action": False},
                        {"instrument": "chemyx", "operation": "HELP", "physical_action": False},
                        {"instrument": "nmr", "operation": "PING", "physical_action": False},
                    ]
                )
                combined_events.extend(serialized_events(controller))
                return report, controller.identity.get("version"), {"connection_only": True}, combined_events

        return run_recorded(
            test_name="test_04a_integrated_preflight",
            mode=mode,
            cfg=run_cfg,
            runner=preflight_runner,
            event_log=combined_events,
            failure_context=failure_context,
        )

    root = run_cfg["results"]["run_root_dir"]
    prereq = {
        "test_01": matching_live_result(root, "test_01_arduino_connection", run_cfg) is not None,
        "test_02": matching_live_result(root, "test_02_unloaded_motor", run_cfg) is not None,
        "test_03": matching_live_result(root, "test_03_needle_axis", run_cfg) is not None,
        "test_04a": matching_live_result(root, "test_04a_integrated_preflight", run_cfg) is not None,
    }

    def full_runner(run_dir):
        deadline = HardDeadline(min(120.0, run_cfg["safety"]["hard_runtime_limit_s"]))
        pump_cfg, nmr_cfg, pump_action, pump_return = resolve_integrated_settings(run_cfg)
        if mode == "live":
            missing = test4_full_missing(run_cfg, prereq)
            if unresolved_live_motion_failure(run_cfg["results"]["run_root_dir"], run_cfg):
                missing.append("Documented operator inspection after the latest failed live motion")
            require_live("FULL INTEGRATED TEST 4B", missing)
            if run_cfg["integrated"].get("nmr_diagnostic") != "si6_configured_1d":
                raise ValueError("Live NMR diagnostic must be explicitly 'si6_configured_1d'")
            if pump_action is None or pump_return is None:
                raise ValueError("Live integration requires approved forward and return pump actions")
            confirm_live("RUN ARDUINO TEST 4B")
        if pump_action is None:
            raise ValueError("No approved pump diagnostic action selected")
        interlock = SequentialInstrumentInterlock()
        machine, pump, nmr_client = _machine_objects(run_cfg, mode)
        deadline.check("Test 4B setup")
        with controller_session(
            run_cfg,
            mode,
            allow_motion=True,
            mock_homed=True,
            motion_guard=interlock.motion_allowed,
            motion_dispatch_callback=lambda: failure_context.update(motion_attempted=True),
        ) as controller:
            failure_context["firmware_version"] = controller.identity.get("version")
            ensure_distinct_ports(
                getattr(controller.transport, "port", run_cfg["arduino"].get("port")),
                machine.chemyx.serial_port,
            )
            with pump:
                try:
                    nmr_client.ping()
                    deadline.check("Test 4B NMR preflight")
                    nmr_operation = (
                        (lambda path, _max_wait: nmr_client.acquire_diagnostic(path))
                        if mode == "mock"
                        else lambda path, max_wait: existing_nmr_operation(nmr_cfg, path, max_wait)
                    )
                    state = run_test_04b(
                        controller,
                        pump,
                        nmr_operation,
                        run_cfg,
                        pump_cfg,
                        pump_action,
                        pump_return,
                        run_dir,
                        deadline,
                        interlock,
                        sleep_fn=(lambda _seconds: None) if mode == "mock" else __import__("time").sleep,
                        enforce_wall_clock=mode != "mock",
                        event_log=combined_events,
                        minimum_nmr_budget_s=(
                            5.0 * min(float(nmr_cfg.timeout), deadline.seconds / 10.0)
                            + float(nmr_cfg.poll_seconds)
                            + 6.0
                        ),
                    )
                except BaseException:
                    controller.stop_best_effort()
                    try:
                        pump.stop()
                    except BaseException:
                        pass
                    combined_events.extend(serialized_events(controller))
                    raise
            combined_events.extend(serialized_events(controller))
            return state, controller.identity.get("version"), {"strictly_sequential": True}, combined_events

    return run_recorded(
        test_name="test_04b_integrated_system",
        mode=mode,
        cfg=run_cfg,
        runner=full_runner,
        event_log=combined_events,
        failure_context=failure_context,
    )


if __name__ == "__main__":
    raise SystemExit(main())
