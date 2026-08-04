"""Sequential staged test procedures, separated from their CLIs."""

from __future__ import annotations

import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable

from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.workflows.instrument_operations import (
    configure_pump,
    move_seconds,
    run_nmr_acquisition,
)
from chemyx_lab.workflows.si6_automated_nmr import (
    PumpSafetyState,
    build_instrument_settings,
    load_si6_config,
    run_safe_metered_move,
)

from .controller import NeedleController
from .errors import HardRuntimeExceeded, MotionInterlockError, ProtocolError
from .protocol import bool_field

TEST_01_COMMANDS = ("PING", "STATUS", "LED ON", "STATUS", "LED OFF", "STATUS", "BLINK", "STATUS")


class HardDeadline:
    def __init__(self, seconds: float, monotonic_fn: Callable[[], float] = time.monotonic) -> None:
        self.seconds = float(seconds)
        self._monotonic = monotonic_fn
        self._deadline = monotonic_fn() + self.seconds

    @property
    def remaining_s(self) -> float:
        return max(0.0, self._deadline - self._monotonic())

    def check(self, label: str = "test") -> None:
        if self._monotonic() >= self._deadline:
            raise HardRuntimeExceeded(f"{label} exceeded hard runtime limit of {self.seconds:g} s")


def _status_bool(status: dict[str, str], name: str) -> bool:
    return bool_field(status, name)


def verify_firmware_motion_configuration(
    status: dict[str, str], cfg: dict[str, Any], *, include_limits: bool
) -> None:
    expected = {
        "motion_commissioned": bool(cfg["firmware"].get("motion_enabled")),
        "signal_inverted": bool(cfg["signal_interface"].get("signal_inverted")),
        "enable_active_low": bool(cfg["driver"].get("enable_active_low")),
    }
    if include_limits:
        expected.update(
            {
                "limits_commissioned": bool(cfg["firmware"].get("limits_enabled")),
                "upper_active_low": bool(cfg["limits"].get("upper_active_low")),
                "lower_active_low": bool(cfg["limits"].get("lower_active_low")),
            }
        )
    for field, configured in expected.items():
        reported = bool_field(status, field)
        if reported != configured:
            raise ProtocolError(
                f"Firmware {field}={reported} does not match reviewed config {configured}"
            )
    if include_limits:
        for field in (
            "maximum_travel_steps",
            "maximum_speed_steps_s",
            "maximum_acceleration_steps_s2",
            "home_speed_steps_s",
        ):
            try:
                reported_number = int(status[field])
                configured_number = int(cfg["motion"][field])
            except (KeyError, TypeError, ValueError) as exc:
                raise ProtocolError(f"Missing or invalid commissioned firmware field {field}") from exc
            if reported_number != configured_number:
                raise ProtocolError(
                    f"Firmware {field}={reported_number} does not match reviewed config {configured_number}"
                )


def run_test_01(controller: NeedleController, deadline: HardDeadline) -> dict[str, Any]:
    """Arduino-only hello world; this function contains no motor commands."""
    deadline.check("Test 1")
    controller.ping()
    initial = controller.status()
    if _status_bool(initial, "enabled"):
        raise ProtocolError("Test 1 requires motor state disabled")
    if _status_bool(initial, "led"):
        raise ProtocolError("Test 1 requires LED off at startup")
    led_cleanup_needed = True
    try:
        controller.led_on()
        if not _status_bool(controller.status(), "led"):
            raise ProtocolError("LED did not report on")
        controller.led_off()
        led_cleanup_needed = False
        if _status_bool(controller.status(), "led"):
            raise ProtocolError("LED did not report off")
        # If BLINK completes without its DONE response, LED state is unknown;
        # a best-effort LED OFF is therefore an explicit cleanup action.
        led_cleanup_needed = True
        controller.blink()
        final = controller.status()
        if _status_bool(final, "led"):
            raise ProtocolError("BLINK did not finish with LED off")
        led_cleanup_needed = False
        if _status_bool(final, "enabled") or _status_bool(final, "moving"):
            raise ProtocolError("Test 1 changed the motor state")
        deadline.check("Test 1")
        return final
    finally:
        if led_cleanup_needed:
            try:
                controller.led_off()
            except BaseException:
                pass


def run_test_02(
    controller: NeedleController,
    cfg: dict[str, Any],
    deadline: HardDeadline,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    motion = cfg["motion"]
    steps = int(motion["test_02_steps"])
    speed = int(motion["test_02_speed_steps_s"])
    if steps <= 0 or speed <= 0:
        raise ValueError("Test 2 steps and speed must be positive")
    if steps > 200000 or speed > 5000:
        raise ValueError("Test 2 exceeds the immutable firmware step or speed cap")
    estimated_seconds = 2.0 * steps / speed + 4.0 * speed / 500.0 + 11.0
    if estimated_seconds > deadline.remaining_s:
        raise HardRuntimeExceeded(
            "The complete forward/pause/reverse Test 2 sequence cannot fit before ENABLE "
            f"(needs about {estimated_seconds:.1f} s)"
        )
    initial = controller.status()
    verify_firmware_motion_configuration(initial, cfg, include_limits=False)
    deadline.check("Test 2")
    try:
        controller.enable()
        controller.jog(steps, speed)
        sleep_fn(min(1.0, deadline.remaining_s))
        deadline.check("Test 2")
        controller.jog(-steps, speed)
        final = controller.status()
        controller.stop()
        controller.disable()
    except BaseException:
        controller.stop_best_effort()
        try:
            controller.disable()
        except BaseException:
            pass
        raise
    deadline.check("Test 2")
    final = controller.status()
    if _status_bool(final, "moving") or _status_bool(final, "enabled"):
        raise ProtocolError("Test 2 did not finish stopped and disabled")
    return final


def run_test_03(
    controller: NeedleController,
    cfg: dict[str, Any],
    deadline: HardDeadline,
    *,
    verify_limits: Callable[[NeedleController, HardDeadline], None],
) -> dict[str, Any]:
    motion = cfg["motion"]
    speed = int(motion["maximum_speed_steps_s"])
    home_speed = int(motion["home_speed_steps_s"])
    backoff = int(motion["home_backoff_steps"])
    safe_up = int(motion["safe_up_position_steps"])
    down = int(motion["test_down_position_steps"])
    maximum = int(motion["maximum_travel_steps"])
    validate_axis_geometry(cfg)
    acceleration = int(motion["maximum_acceleration_steps_s2"])

    def movement_budget(steps: int, selected_speed: int) -> float:
        return abs(steps) / selected_speed + 2.0 * selected_speed / acceleration + 5.0

    estimated_seconds = (
        movement_budget(maximum, home_speed)
        + movement_budget(backoff, home_speed)
        + movement_budget(safe_up - backoff, speed)
        + 4.0 * movement_budget(down - safe_up, speed)
    )
    if estimated_seconds > deadline.remaining_s:
        raise HardRuntimeExceeded(
            "The complete homing/backoff/two-cycle Test 3 plan cannot fit before ENABLE "
            f"(needs about {estimated_seconds:.1f} s)"
        )
    initial = controller.status()
    verify_firmware_motion_configuration(initial, cfg, include_limits=True)
    if _status_bool(initial, "moving") or initial.get("fault", "NONE") != "NONE":
        raise ProtocolError("Test 3 requires idle firmware with no latched fault")
    if _status_bool(initial, "limit_up") and _status_bool(initial, "limit_down"):
        raise ProtocolError("Both limit switches are active")
    verify_limits(controller, deadline)
    deadline.check("Test 3")
    controller.enable()
    try:
        controller.home()
        if backoff <= 0:
            raise ValueError("Home backoff must be positive")
        controller.jog(backoff, home_speed)
        controller.move_absolute(safe_up, speed)
        for _ in range(2):
            deadline.check("Test 3")
            controller.move_absolute(down, speed)
            controller.move_absolute(safe_up, speed)
        final = controller.status()
    except BaseException:
        controller.stop_best_effort()
        raise
    deadline.check("Test 3")
    if int(final.get("commanded_position_steps", -1)) != safe_up:
        raise ProtocolError("Test 3 did not finish at commanded safe UP")
    if _status_bool(final, "moving"):
        raise ProtocolError("Test 3 finished while firmware reports moving")
    return final


def validate_axis_geometry(cfg: dict[str, Any]) -> None:
    motion = cfg["motion"]
    try:
        values = (
            motion["safe_up_position_steps"],
            motion["test_down_position_steps"],
            motion["maximum_travel_steps"],
        )
        if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
            raise TypeError
        safe_up, down, maximum = values
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Axis positions must be configured integers") from exc
    if not (0 < safe_up < down < maximum):
        raise ValueError("Require 0 < safe UP < test DOWN < maximum travel")


@dataclass
class SequentialInstrumentInterlock:
    nmr_active: bool = False

    def motion_allowed(self) -> bool:
        return not self.nmr_active

    def assert_nmr_can_start(self, arduino_status: dict[str, str]) -> None:
        if _status_bool(arduino_status, "moving"):
            raise MotionInterlockError("NMR cannot start while Arduino reports movement")

    def run_nmr(self, arduino_status: dict[str, str], operation: Callable[[], Path]) -> Path:
        self.assert_nmr_can_start(arduino_status)
        self.nmr_active = True
        try:
            return operation()
        finally:
            self.nmr_active = False


def run_test_04a(
    controller: NeedleController,
    pump: Pump,
    nmr_client,
    deadline: HardDeadline,
) -> dict[str, Any]:
    controller.ping()
    arduino_status = controller.status()
    if _status_bool(arduino_status, "moving"):
        raise ProtocolError("Connection preflight requires Arduino motion idle")
    if _status_bool(arduino_status, "enabled"):
        raise ProtocolError("Connection preflight requires Arduino driver disabled")
    if arduino_status.get("fault", "NONE") != "NONE":
        raise ProtocolError("Connection preflight requires no latched Arduino fault")
    chemyx_identity = pump.help()
    nmr_status = nmr_client.ping()
    if chemyx_identity is None or (
        isinstance(chemyx_identity, (str, bytes, list, tuple, dict))
        and len(chemyx_identity) == 0
    ):
        raise ProtocolError("Chemyx HELP returned no readiness response")
    if nmr_status is None or nmr_status is False or nmr_status == "":
        raise ProtocolError("NMR PING returned no positive readiness response")
    if isinstance(nmr_status, dict) and any(
        nmr_status.get(key) is False for key in ("ready", "Ready", "ok", "Ok")
    ):
        raise ProtocolError(f"NMR PING explicitly reported not ready: {nmr_status!r}")
    deadline.check("Test 4A")
    return {
        "arduino": arduino_status,
        "chemyx": {"help_response": chemyx_identity},
        "nmr": {"ping": nmr_status},
        "physical_actions": [],
    }


def resolve_integrated_settings(cfg: dict[str, Any]):
    integrated = cfg["integrated"]
    source = Path(cfg.get("_source_path", Path.cwd()))

    def resolve(value: str) -> Path:
        path = Path(value)
        if path.is_absolute():
            return path
        repo_candidate = Path(__file__).resolve().parents[2] / path
        return repo_candidate if repo_candidate.exists() else source.parent / path

    experiment_path = resolve(str(integrated["experiment_config_path"]))
    machine_path = resolve(str(integrated["machine_config_path"]))
    raw = load_si6_config(experiment_path)
    pump_cfg, nmr_cfg = build_instrument_settings(raw, machine_path)
    cycle = raw["workflow"]["cycle"]

    def selected(index_value: Any, label: str):
        if index_value in (None, ""):
            return None
        index = int(index_value)
        if not 1 <= index <= len(cycle):
            raise ValueError(f"{label} must select an existing 1-based cycle item")
        event = cycle[index - 1]
        if event.get("action") not in {"infuse", "withdraw"}:
            raise ValueError(f"{label} must select an existing pump action")
        return event

    return (
        pump_cfg,
        nmr_cfg,
        selected(integrated["pump_action_cycle_index"], "pump_action_cycle_index"),
        selected(integrated.get("pump_return_cycle_index"), "pump_return_cycle_index"),
    )


def run_test_04b(
    controller: NeedleController,
    pump: Pump,
    nmr_operation: Callable[[Path, float], Path],
    cfg: dict[str, Any],
    pump_cfg,
    pump_action: dict[str, Any],
    pump_return_action: dict[str, Any] | None,
    run_dir: Path,
    deadline: HardDeadline,
    interlock: SequentialInstrumentInterlock,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
    enforce_wall_clock: bool = True,
    event_log: list[dict[str, Any]] | None = None,
    minimum_nmr_budget_s: float = 10.0,
) -> dict[str, Any]:
    events = event_log if event_log is not None else []
    motion = cfg["motion"]
    integrated = cfg["integrated"]
    safe_up = int(motion["safe_up_position_steps"])
    down = int(motion["test_down_position_steps"])
    speed = int(motion["maximum_speed_steps_s"])
    if pump_return_action is None:
        raise MotionInterlockError("Full integration requires an approved pump return action")
    forward_seconds = move_seconds(
        float(pump_action["volume_ml"]), pump_cfg.rate, pump_cfg.units
    )
    return_seconds = move_seconds(
        float(pump_return_action["volume_ml"]), pump_cfg.rate, pump_cfg.units
    )
    if pump_return_action["action"] == pump_action["action"]:
        raise MotionInterlockError("Pump return action must use the opposite direction")
    if float(pump_return_action["volume_ml"]) != float(pump_action["volume_ml"]):
        raise MotionInterlockError("Pump return action must match the diagnostic volume")
    acceleration = float(motion["maximum_acceleration_steps_s2"])
    needle_move_seconds = (
        abs(down - safe_up) / speed + 2.0 * speed / acceleration + 5.0
    )
    required_seconds = (
        forward_seconds
        + return_seconds
        + float(integrated["post_motion_settle_s"])
        + float(integrated["post_pump_settle_s"])
        + float(minimum_nmr_budget_s)
        + 2.0 * needle_move_seconds
        + 3.0
    )
    if enforce_wall_clock and required_seconds > deadline.remaining_s:
        raise HardRuntimeExceeded(
            "The complete approved sequence cannot fit the hard runtime before any "
            f"hardware action (needs at least {required_seconds:.1f} s; "
            f"{deadline.remaining_s:.1f} s remains)"
        )
    initial = controller.status()
    verify_firmware_motion_configuration(initial, cfg, include_limits=True)
    if not _status_bool(initial, "homed") or not _status_bool(initial, "position_known"):
        raise MotionInterlockError("Full integration requires a homed, known needle position")
    if int(initial.get("commanded_position_steps", -1)) != safe_up:
        raise MotionInterlockError("Full integration must start at commanded safe UP")
    if not _status_bool(initial, "enabled"):
        raise MotionInterlockError("Full integration requires holding torque enabled")
    if _status_bool(initial, "moving") or initial.get("fault", "NONE") != "NONE":
        raise MotionInterlockError("Full integration requires idle firmware with no fault")
    events.append({"instrument": "arduino", "operation": "MOVE_DOWN", "state": "planned"})
    controller.move_absolute(down, speed)
    events.append({"instrument": "arduino", "operation": "MOVE_DOWN", "state": "completed"})
    deadline.check("Test 4B")
    def bounded_sleep(seconds: float, label: str) -> None:
        seconds = max(0.0, float(seconds))
        if enforce_wall_clock and seconds > deadline.remaining_s:
            raise HardRuntimeExceeded(f"Insufficient hard-runtime budget for {label}")
        sleep_fn(seconds)
        deadline.check("Test 4B")

    bounded_sleep(float(integrated.get("post_motion_settle_s") or 0), "post-motion settling")

    state = PumpSafetyState()
    configure_pump(pump, pump_cfg)
    if enforce_wall_clock and forward_seconds + 5.0 > deadline.remaining_s:
        raise HardRuntimeExceeded(
            "Approved pump diagnostic cannot finish inside the remaining hard-runtime budget"
        )
    events.append({"instrument": "chemyx", "operation": pump_action["action"], "state": "planned", "source": "existing experiment config"})
    run_safe_metered_move(
        pump,
        pump_cfg,
        pump_action["action"],
        float(pump_action["volume_ml"]),
        state,
        extra_seconds=0,
        sleep_fn=lambda _label, seconds: bounded_sleep(seconds, "pump diagnostic"),
    )
    events.append({"instrument": "chemyx", "operation": pump_action["action"], "state": "completed"})
    bounded_sleep(float(integrated.get("post_pump_settle_s") or 0), "post-pump settling")
    idle_status = controller.status()
    interlock.assert_nmr_can_start(idle_status)
    suffix = str(integrated["expected_nmr_artifact_suffix"])
    expected_path = run_dir / "nmr" / f"integrated_diagnostic{suffix}"
    reserved_after_nmr_s = return_seconds + needle_move_seconds + 3.0
    nmr_budget_s = deadline.remaining_s - reserved_after_nmr_s
    if enforce_wall_clock and nmr_budget_s < minimum_nmr_budget_s:
        raise HardRuntimeExceeded(
            "Insufficient budget for NMR while reserving safe-UP and the mandatory pump return"
        )
    events.append({"instrument": "nmr", "operation": "configured_1d_diagnostic", "state": "planned"})
    artifact = interlock.run_nmr(
        idle_status,
        lambda: nmr_operation(
            expected_path,
            max(0.1, nmr_budget_s if enforce_wall_clock else deadline.remaining_s),
        ),
    )
    events.append({"instrument": "nmr", "operation": "configured_1d_diagnostic", "state": "completed", "artifact": str(artifact)})
    deadline.check("Test 4B")
    if not artifact.exists() or artifact.suffix.casefold() != suffix.casefold():
        raise ProtocolError(f"Expected NMR output artifact was not created: {artifact}")

    events.append({"instrument": "arduino", "operation": "MOVE_SAFE_UP", "state": "planned"})
    controller.move_absolute(safe_up, speed)
    events.append({"instrument": "arduino", "operation": "MOVE_SAFE_UP", "state": "completed"})
    if pump_return_action is not None:
        if enforce_wall_clock and return_seconds + 5.0 > deadline.remaining_s:
            raise HardRuntimeExceeded(
                "Approved pump return cannot finish inside the remaining hard-runtime budget"
            )
        events.append({"instrument": "chemyx", "operation": pump_return_action["action"], "state": "planned", "source": "existing experiment config"})
        run_safe_metered_move(
            pump,
            pump_cfg,
            pump_return_action["action"],
            float(pump_return_action["volume_ml"]),
            state,
            extra_seconds=0,
            sleep_fn=lambda _label, seconds: bounded_sleep(seconds, "pump return"),
        )
        events.append({"instrument": "chemyx", "operation": pump_return_action["action"], "state": "completed"})
    deadline.check("Test 4B")
    final = controller.status()
    if int(final.get("commanded_position_steps", -1)) != safe_up:
        raise ProtocolError("Full integration did not finish at safe UP")
    return {"arduino": final, "nmr_artifact": str(artifact), "pump_state_uncertain": state.uncertain}


def existing_nmr_operation(nmr_cfg, output_path: Path, max_wait_s: float) -> Path:
    available = float(max_wait_s)
    # iFlow performs four setup/start requests before polling. Reserve five
    # request timeouts, one poll sleep, and cleanup margin inside the caller's
    # already bounded overall deadline.
    bounded_timeout = min(float(nmr_cfg.timeout), max(0.1, available / 10.0))
    overhead = 5.0 * bounded_timeout + float(nmr_cfg.poll_seconds) + 1.0
    if available <= overhead:
        raise HardRuntimeExceeded(
            f"Insufficient NMR runtime budget: {available:.1f} s remains, "
            f"but bounded RPC overhead reserves {overhead:.1f} s"
        )
    bounded_cfg = replace(
        nmr_cfg,
        timeout=bounded_timeout,
        max_wait_seconds=min(float(nmr_cfg.max_wait_seconds), available - overhead),
    )
    created = run_nmr_acquisition(bounded_cfg, output_path.parent, label=output_path.stem)
    return created
