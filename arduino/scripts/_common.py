"""Shared safe CLI behavior for Arduino staged tests."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.config import load_arduino_config
from arduino.python.controller import NeedleController
from arduino.python.discovery import format_port_table, list_serial_ports, resolve_arduino_port
from arduino.python.errors import LiveExecutionBlocked
from arduino.python.results import create_run_dir, write_result
from arduino.python.run_lock import PortProcessLock
from arduino.python.transport import SerialTransport


def add_standard_arguments(
    parser: argparse.ArgumentParser,
    *,
    motion_capable: bool,
    default_config: Path,
) -> None:
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--validate-only", action="store_true")
    modes.add_argument("--mock", action="store_true")
    modes.add_argument("--dry-run", action="store_true")
    modes.add_argument("--live", action="store_true")
    parser.add_argument("--config", type=Path, default=default_config)
    parser.add_argument("--list-ports", action="store_true")
    if motion_capable:
        parser.add_argument("--preflight-only", action="store_true")


def execution_mode(args: argparse.Namespace) -> str:
    if args.live:
        return "live"
    if args.mock:
        return "mock"
    if args.dry_run:
        return "dry_run"
    return "validate_only"


def load_cli_config(args: argparse.Namespace) -> dict:
    cfg = load_arduino_config(args.config)
    if args.list_ports:
        print(format_port_table(list_serial_ports()))
    return cfg


def confirm_live(exact_text: str, input_fn: Callable[[str], str] = input) -> None:
    if not sys.stdin.isatty():
        raise LiveExecutionBlocked("Live confirmation", [f"Interactive confirmation: {exact_text}"])
    answer = input_fn(f"Type {exact_text} to continue: ").strip()
    if answer != exact_text:
        raise LiveExecutionBlocked("Live confirmation", [f"Exact confirmation text: {exact_text}"])


def bounded_console_input(prompt: str, deadline) -> str:
    """Read one operator line without exceeding a staged-test deadline."""
    if not sys.stdin.isatty():
        raise LiveExecutionBlocked("Operator observations", ["Interactive terminal input"])
    print(prompt, end="", flush=True)
    characters: list[str] = []
    if os.name == "nt":
        import msvcrt

        while True:
            deadline.check("Operator observation entry")
            if not msvcrt.kbhit():
                time.sleep(min(0.05, deadline.remaining_s))
                continue
            char = msvcrt.getwch()
            if char in {"\r", "\n"}:
                print()
                return "".join(characters).strip()
            if char == "\x03":
                raise KeyboardInterrupt
            if char == "\b":
                if characters:
                    characters.pop()
                    print("\b \b", end="", flush=True)
                continue
            if char.isprintable():
                characters.append(char)
                print(char, end="", flush=True)
    else:
        import select

        while True:
            deadline.check("Operator observation entry")
            readable, _, _ = select.select(
                [sys.stdin], [], [], min(0.1, deadline.remaining_s)
            )
            if readable:
                return sys.stdin.readline().strip()


@contextmanager
def controller_session(
    cfg: dict,
    mode: str,
    *,
    allow_motion: bool,
    mock_scenario: str = "normal",
    mock_homed: bool = False,
    motion_guard=None,
    motion_dispatch_callback=None,
    apply_runtime_config: bool = False,
):
    settings = cfg["arduino"]
    lock = None
    if mode == "mock":
        runtime_configurable = bool(cfg["firmware"].get("runtime_configurable"))
        transport = FakeArduinoTransport(
            scenario=mock_scenario,
            device=settings["expected_device"],
            board=settings["expected_board"],
            version=settings.get("expected_version") or cfg["firmware"]["version"],
            homed=mock_homed,
            motion_commissioned=(False if runtime_configurable else bool(cfg["firmware"].get("motion_enabled"))),
            limits_commissioned=(False if runtime_configurable else bool(cfg["firmware"].get("limits_enabled"))),
            maximum_travel_steps=(0 if runtime_configurable else cfg["motion"].get("maximum_travel_steps") or 0),
            maximum_speed_steps_s=(0 if runtime_configurable else cfg["motion"].get("maximum_speed_steps_s") or 0),
            maximum_acceleration_steps_s2=(0 if runtime_configurable else cfg["motion"].get("maximum_acceleration_steps_s2") or 0),
            home_speed_steps_s=(0 if runtime_configurable else cfg["motion"].get("home_speed_steps_s") or 0),
            initial_position_steps=(cfg["motion"].get("safe_up_position_steps") or 0) if mock_homed else 0,
            initially_enabled=mock_homed,
            runtime_configurable=runtime_configurable,
            driver_model=cfg["driver"].get("model") or "",
        )
        if runtime_configurable and mock_homed:
            desired = NeedleController._desired_runtime_configuration(cfg)
            transport.runtime_configured = True
            transport.motion_commissioned = bool(desired["motion_commissioned"])
            transport.limits_commissioned = bool(desired["limits_commissioned"])
            transport.signal_inverted = bool(desired["signal_inverted"])
            transport.enable_active_low = bool(desired["enable_active_low"])
            transport.upper_active_low = bool(desired["upper_active_low"])
            transport.lower_active_low = bool(desired["lower_active_low"])
            transport.maximum_travel_steps = int(desired["maximum_travel_steps"])
            transport.maximum_speed_steps_s = int(desired["maximum_speed_steps_s"])
            transport.maximum_acceleration_steps_s2 = int(
                desired["maximum_acceleration_steps_s2"]
            )
            transport.home_speed_steps_s = int(desired["home_speed_steps_s"])
    elif mode == "live":
        selected = resolve_arduino_port(settings.get("port"), settings.get("fingerprint"))
        lock = PortProcessLock(selected.device).acquire()
        transport = SerialTransport(
            selected.device,
            settings["baud_rate"],
            read_timeout_s=settings["read_timeout_s"],
            write_timeout_s=settings["write_timeout_s"],
        )
    else:
        raise ValueError(f"Controller session is invalid in mode {mode}")
    controller = NeedleController(
        transport,
        expected_device=settings["expected_device"],
        expected_board=settings["expected_board"],
        expected_version=settings.get("expected_version"),
        ready_timeout_s=settings["ready_timeout_s"],
        command_timeout_s=settings["command_timeout_s"],
        overall_timeout_s=(
            min(120.0, float(cfg["safety"]["hard_runtime_limit_s"]))
            if allow_motion
            else min(59.0, float(settings["overall_timeout_s"]))
        ),
        allow_motion=allow_motion,
        motion_guard=motion_guard,
        motion_dispatch_callback=motion_dispatch_callback,
    )
    try:
        with controller:
            if apply_runtime_config and cfg["firmware"].get("runtime_configurable"):
                controller.configure_runtime(cfg)
            yield controller
    finally:
        if lock is not None:
            lock.release()


def run_recorded(
    *,
    test_name: str,
    mode: str,
    cfg: dict,
    runner: Callable[[Path], tuple[dict, str | None, dict, list[dict]]],
    event_log: list[dict] | None = None,
    failure_context: dict | None = None,
) -> int:
    run_dir = create_run_dir(cfg["results"]["run_root_dir"], test_name)
    try:
        state, version, confirmations, events = runner(run_dir)
    except BaseException as exc:
        motion_attempted = bool((failure_context or {}).get("motion_attempted"))
        failure_state = (failure_context or {}).get("final_known_device_state")
        if failure_state is None:
            failure_state = (
                {"known": False, "needle_position": "uncertain"}
                if motion_attempted
                else {"known": True, "motion_attempted": False}
            )
        write_result(
            run_dir,
            test_name=test_name,
            mode=mode,
            cfg=cfg,
            passed=False,
            firmware_version=(failure_context or {}).get("firmware_version"),
            operator_confirmations=(failure_context or {}).get("operator_confirmations", {}),
            final_known_device_state=failure_state,
            error=f"{type(exc).__name__}: {exc}",
            event_log=event_log,
            motion_attempted=motion_attempted,
        )
        if isinstance(exc, LiveExecutionBlocked):
            print(f"\n{test_name.replace('_', ' ').upper()} BLOCKED\n")
            print("Missing requirement:")
            for item in exc.missing:
                print(item)
            if test_name == "test_02_unloaded_motor":
                print("\nTest 1 may still be run safely.\nTest 2 cannot run in live mode.")
        else:
            print(f"FAILED: {type(exc).__name__}: {exc}")
        print(f"Result: {run_dir / 'result.json'}")
        return 1
    write_result(
        run_dir,
        test_name=test_name,
        mode=mode,
        cfg=cfg,
        passed=True,
        firmware_version=version,
        operator_confirmations=confirmations,
        final_known_device_state=state,
        event_log=events,
        motion_attempted=bool((failure_context or {}).get("motion_attempted")),
    )
    print(f"PASS: {test_name}")
    print(f"Result: {run_dir / 'result.json'}")
    return 0


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def serialized_events(controller: NeedleController) -> list[dict]:
    return [
        {"kind": item.kind, "sequence": item.sequence, "detail": item.detail, "raw": item.raw}
        for item in controller.events
    ]
