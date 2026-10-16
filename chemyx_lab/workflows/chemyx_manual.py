"""Standalone Chemyx Fusion 4000 manual control with a custom software HOME.

HOME is never a native pump command. ``set-home`` saves an operator-confirmed
physical plunger position as HOME = 0 mL without moving anything; ``home``
reads the shared displacement ledger and performs the ordinary infusion or
withdrawal that returns the recorded displacement to zero.

Every move uses the experiment's own driver (``Pump``) and its timed,
STOP-confirmed ``run_safe_metered_move``, and is recorded in the same ledger
that the automated experiment updates. A saved position is a software
estimate from tracked commands, never a measurement of the plunger.
"""

from __future__ import annotations

import argparse
import json
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator

from .. import config
from ..instruments.chemyx import Pump
from ..runtime_journal import OperationJournal, RunRecorder, discover_git_commit
from . import chemyx_position as position
from . import pump_channels
from . import si6_automated_nmr as base
from .instrument_operations import configure_pump, format_seconds, move_seconds, sleep_with_progress


DEFAULT_CONFIG = config.REPO_ROOT / "configs" / "chemyx" / "manual_control.yaml"
COMMANDS = ("status", "prepare", "set-home", "home", "infuse", "withdraw", "stop", "verify")
UNITS_ML_MIN = 0
EXTRA_SECONDS = 2.0
TOP_KEYS = {"machine_config", "syringe_config", "dose_ledger_dir", "channels"}
CHANNEL_KEYS = {"name", "syringe", "home", "manual_transfer"}
DISCLAIMER = ("Positions are software estimates from tracked Chemyx commands, not plunger "
              "measurements. Front-panel or other untracked motion is invisible to this record.")
RECOVERY = ("To recover an UNKNOWN or missing reference: stop the pump, physically place the "
            "plunger at the HOME position described in the YAML, then run set-home.")


class ManualControlError(RuntimeError):
    """A manual-control precondition failed; nothing further was commanded."""


@dataclass(frozen=True)
class ChannelSettings:
    channel: int
    name: str
    diameter_mm: float | None
    capacity_ml: float | None
    geometry_source: str
    recipe_diameter_mm: float | None
    recipe_capacity_ml: float | None
    home_enabled: bool
    reference_description: str
    syringe_volume_at_home_ml: float | None
    transfer_volume_ml: float
    rate_ml_min: float

    def identity(self, pump: str) -> position.ChannelIdentity:
        if self.diameter_mm is None or self.capacity_ml is None:
            raise ManualControlError(
                f"Channel {self.channel} syringe diameter/capacity is not configured. Set "
                f"channels.{self.channel}.syringe in the manual control YAML or its syringe_config recipe."
            )
        return position.ChannelIdentity(
            self.channel, pump, self.diameter_mm, self.capacity_ml,
            self.reference_description, self.syringe_volume_at_home_ml,
        )


@dataclass(frozen=True)
class ManualSettings:
    path: Path
    machine_config: Path
    port: str | None
    baud_rate: int
    timeout: float
    response_delay: float
    syringe_config: Path | None
    dose_ledger_dir: Path
    channels: dict[int, ChannelSettings]

    def pump_identity(self, mock: bool) -> str | None:
        if mock:
            return "mock"
        return position.pump_identity(self.port, mock=False) if self.port else None

    def pump_config(self, ch: ChannelSettings, volume_ml: float) -> config.PumpConfig:
        return config.PumpConfig(
            port=self.port or "", baud_rate=self.baud_rate, channel=ch.channel,
            units=UNITS_ML_MIN, diameter=float(ch.diameter_mm), rate=ch.rate_ml_min,
            volume=volume_ml, timeout=self.timeout, response_delay=self.response_delay,
        )


def _mapping(value: Any, label: str, allowed: set[str]) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise config.ConfigError(f"{label} must be a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise config.ConfigError(f"Unknown {label} field(s): {', '.join(sorted(map(str, unknown)))}")
    return value


def _optional_positive(value: Any, label: str) -> float | None:
    return None if value is None else pump_channels.positive(value, label)


def load_manual_config(path: Path) -> ManualSettings:
    path = config.resolve_repo_path(path)
    raw = config.read_mapping_config(path, "Chemyx manual control config")
    _mapping(raw, "manual control", TOP_KEYS)
    machine_path = config.resolve_repo_path(raw.get("machine_config") or "configs/machines/00_machine.local.yaml")
    machine = config.load_machine_config(machine_path).chemyx
    values = {"port": machine.serial_port, "baud_rate": machine.baud_rate,
              "timeout": machine.timeout_seconds, "response_delay": machine.response_delay_seconds}
    overrides = config._explicit_env_pump_overrides()
    values.update({key: value for key, value in overrides.items() if key in values})
    recipe: dict[int, dict[str, Any]] = {}
    syringe_config = None
    if raw.get("syringe_config"):
        syringe_config = config.resolve_repo_path(raw["syringe_config"])
        recipe_raw = config.read_mapping_config(syringe_config, "syringe_config recipe")
        if not isinstance(recipe_raw.get("pump"), dict):
            raise config.ConfigError(f"syringe_config {syringe_config} has no pump section")
        recipe = pump_channels.channel_definitions({"pump": recipe_raw["pump"]})
    sections = raw.get("channels")
    if not isinstance(sections, dict) or not sections:
        raise config.ConfigError("channels must map channel 1 and/or 2 to their settings")
    channels = {}
    for key, value in sections.items():
        ch = pump_channels.channel_number(key)
        if ch in channels:
            raise config.ConfigError(f"Duplicate channel {ch}")
        label = f"channels.{ch}"
        section = _mapping(value, label, CHANNEL_KEYS)
        name = section.get("name")
        if not isinstance(name, str) or not name.strip():
            raise config.ConfigError(f"{label}.name must be a nonempty string")
        syringe = _mapping(section.get("syringe"), f"{label}.syringe", {"diameter_mm", "capacity_ml"})
        defaults = recipe.get(ch, {})
        recipe_diameter = defaults.get("syringe_diameter_mm")
        recipe_capacity = defaults.get("syringe_capacity_ml")
        diameter = _optional_positive(syringe.get("diameter_mm"), f"{label}.syringe.diameter_mm")
        capacity = _optional_positive(syringe.get("capacity_ml"), f"{label}.syringe.capacity_ml")
        explicit = [k for k, v in (("diameter", diameter), ("capacity", capacity)) if v is not None]
        diameter = diameter if diameter is not None else (None if recipe_diameter is None else float(recipe_diameter))
        capacity = capacity if capacity is not None else (None if recipe_capacity is None else float(recipe_capacity))
        source = ("manual control YAML" if len(explicit) == 2
                  else f"{syringe_config.name if syringe_config else 'unset'}" + (f" ({', '.join(explicit)} overridden)" if explicit else ""))
        if diameter is not None and not config.DIAMETER_MIN <= diameter <= config.DIAMETER_MAX:
            raise config.ConfigError(f"{label}.syringe.diameter_mm is outside the Chemyx limits")
        home = _mapping(section.get("home"), f"{label}.home", {"enabled", "reference_description", "syringe_volume_at_home_ml"})
        enabled = home.get("enabled", True)
        if type(enabled) is not bool:
            raise config.ConfigError(f"{label}.home.enabled must be true or false")
        description = home.get("reference_description")
        if not isinstance(description, str) or not description.strip():
            raise config.ConfigError(f"{label}.home.reference_description must describe the physical HOME position")
        at_home = home.get("syringe_volume_at_home_ml")
        if at_home is not None:
            at_home = pump_channels.positive(at_home, f"{label}.home.syringe_volume_at_home_ml", zero=True)
            if capacity is not None and at_home > capacity:
                raise config.ConfigError(f"{label}.home.syringe_volume_at_home_ml exceeds the syringe capacity")
        transfer = _mapping(section.get("manual_transfer"), f"{label}.manual_transfer", {"volume_ml", "rate_ml_min"})
        volume = pump_channels.positive(transfer.get("volume_ml"), f"{label}.manual_transfer.volume_ml")
        rate = pump_channels.validate_rate(transfer.get("rate_ml_min"), UNITS_ML_MIN, f"{label}.manual_transfer")
        if capacity is not None and volume > capacity:
            raise config.ConfigError(f"{label}.manual_transfer.volume_ml exceeds the syringe capacity")
        channels[ch] = ChannelSettings(
            ch, name.strip(), diameter, capacity, source,
            None if recipe_diameter is None else float(recipe_diameter),
            None if recipe_capacity is None else float(recipe_capacity),
            enabled, " ".join(description.split()), at_home, volume, rate,
        )
    return ManualSettings(
        path, machine_path, values["port"] or None,
        int(values["baud_rate"] or config.BAUD_RATE),
        float(values["timeout"] if values["timeout"] is not None else config.TIMEOUT),
        float(values["response_delay"] if values["response_delay"] is not None else config.RESPONSE_DELAY),
        syringe_config, config.resolve_repo_path(raw.get("dose_ledger_dir") or "runtime/si6_doses"), channels,
    )


# -- Channel 2 reagent-dose protection ---------------------------------------
def dose_records(directory: Path) -> list[dict[str, Any]]:
    """Read-only summary of the experiment dose ledger; never modified here."""
    records = []
    if not Path(directory).is_dir():
        return records
    for path in sorted(Path(directory).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            status = data.get("status") if isinstance(data, dict) else None
        except (OSError, ValueError):
            data, status = {}, None
        if status not in {"RESERVED", "DISPATCH_INTENT", "CONFIRMED"}:
            status = "UNREADABLE"
        records.append({"file": path.name, "experiment_id": (data or {}).get("experiment_id") or path.stem,
                        "status": status, "run_id": (data or {}).get("run_id")})
    return records


def channel2_refusal(direction: str, records: list[dict[str, Any]], allow_infusion: bool) -> str | None:
    """Why Channel 2 must not move, or None. Dose records are never cleared."""
    uncertain = [r for r in records if r["status"] not in {"RESERVED", "CONFIRMED"}]
    if uncertain:
        names = ", ".join(f"{r['experiment_id']} ({r['status']})" for r in uncertain)
        return (f"Channel 2 motion refused: reagent dose record(s) {names} are uncertain. Reconcile the "
                "dose against its experiment journal and the physical reactor first; this tool never "
                "changes dose records.")
    if direction == "infuse":
        reserved = [r["experiment_id"] for r in records if r["status"] == "RESERVED"]
        if reserved:
            return (f"Channel 2 infusion refused: experiment(s) {', '.join(reserved)} are RESERVED without a "
                    "confirmed dose, so a manual reagent infusion could dose that reaction outside its "
                    "accounting. Service the reagent syringe by hand if needed, then re-establish HOME.")
        if not allow_infusion:
            return ("Channel 2 infusion pushes reagent through the dosing line. Disconnect that line from "
                    "any reactor, route it to waste, then repeat the command with --allow-channel2-infusion.")
    return None


def fluid_path_note(ch: ChannelSettings, direction: str, volume_nl: int) -> str:
    amount = position.ml_text(volume_nl)
    if ch.channel == 2 and direction == "infuse":
        return (f"Channel 2 ({ch.name}) will INFUSE {amount} of reagent. The reagent line must be "
                "disconnected from any reactor and routed to waste.")
    if ch.channel == 2:
        return (f"Channel 2 ({ch.name}) will WITHDRAW {amount} through the reagent line. Its inlet must "
                "be in the reagent/refill source, not in a reaction mixture.")
    if direction == "infuse":
        return (f"Channel {ch.channel} ({ch.name}) will INFUSE {amount}: syringe contents are pushed out "
                "through the connected line. Confirm where that fluid will go.")
    return (f"Channel {ch.channel} ({ch.name}) will WITHDRAW {amount} through the connected line. "
            "Confirm the inlet is in the intended source.")


# -- instrument helpers -------------------------------------------------------
def make_pump(settings: ManualSettings, *, mock: bool) -> Pump:
    """The existing Chemyx driver; tests replace this factory."""
    if not mock and not settings.port:
        raise ManualControlError(
            f"No live Chemyx serial port. Set chemyx.serial_port in {settings.machine_config}."
        )
    return Pump(port=settings.port, baud_rate=settings.baud_rate, channel=1, units=UNITS_ML_MIN,
                timeout=settings.timeout, response_delay=0 if mock else settings.response_delay, mock=mock)


@contextmanager
def pump_session(settings: ManualSettings, mock: bool) -> Iterator[Pump]:
    pump = make_pump(settings, mock=mock)
    pump.connect()
    try:
        reply = pump.help()
        if not reply:
            raise ManualControlError("Chemyx HELP returned no response; check cable, port and power")
        print(f"Pump communication: HELP answered on {'mock pump' if mock else settings.port}")
        yield pump
    finally:
        pump.disconnect()


@contextmanager
def ledger_session(mock: bool, owner: str) -> Iterator[position.PositionLedger]:
    ledger = position.PositionLedger(position.state_root(mock), owner=owner).acquire()
    try:
        for notice in ledger.notices:
            print(f"LEDGER NOTICE: {notice}")
        yield ledger
    finally:
        ledger.release()


def confirm_idle(pump: Pump, ch: int, recorder: Any = None, phase: str = "manual") -> None:
    """The protocol's positive STOP acknowledgement is the available idle evidence."""
    state = base.PumpSafetyState(channel=ch)
    status = base.attempt_emergency_stop(pump, state, recorder, workflow_phase=phase)
    if status is not base.StopStatus.SUCCEEDED:
        raise ManualControlError(
            f"Channel {ch} STOP was not positively acknowledged ({state.last_stop_error}); "
            "the pump is not proven idle"
        )


def manual_recorder(mock: bool, command: str) -> RunRecorder:
    """Per-invocation run journal, beside the ledger it updates."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = position.state_root(mock) / "manual_runs" / f"{stamp}_{command}_{'mock' if mock else 'live'}_{uuid.uuid4().hex[:8]}"
    journal = OperationJournal(run_dir / "operation_journal.jsonl", run_dir.name,
                               software_version=discover_git_commit(config.REPO_ROOT))
    return RunRecorder(journal, run_dir / "run_state.json")


def confirm(prompt: str, *, mock: bool, input_fn: Callable[[str], str]) -> bool:
    if mock:
        print(f"{prompt}y  [MOCK: confirmation assumed]")
        return True
    try:
        return input_fn(prompt).strip().lower() in ("y", "yes")
    except EOFError:
        return False


def execute_move(pump: Pump, ledger: position.PositionLedger, settings: ManualSettings, ch: ChannelSettings,
                 plan: position.MovePlan, recorder: RunRecorder, *, mock: bool, command: str) -> dict[str, Any]:
    """One tracked, timed, STOP-confirmed transfer through the experiment's move."""
    cfg = settings.pump_config(ch, plan.volume_ml)
    pump.select_channel(ch.channel)
    try:
        configure_pump(pump, cfg)
        tracker = ledger.tracker(channel=ch.channel, pump=settings.pump_identity(mock), diameter_mm=cfg.diameter,
                                 capacity_ml=float(ch.capacity_ml), source=f"chemyx_control {command}",
                                 run_id=recorder.run_id)
        base.run_safe_metered_move(
            pump, cfg, plan.direction, plan.volume_ml, base.PumpSafetyState(channel=ch.channel),
            extra_seconds=EXTRA_SECONDS,
            sleep_fn=(lambda _label, _seconds: None) if mock else sleep_with_progress,
            recorder=recorder, workflow_phase=f"manual_{command}", position_tracker=tracker,
        )
    finally:
        pump.select_channel(1)
    after = ledger.state(ch.channel)
    if after["position_state"] != "KNOWN" or after["current_displacement_nl"] != plan.displacement_after_nl:
        raise ManualControlError(
            f"Channel {ch.channel} move finished, but the ledger did not record the expected position "
            f"({after['position_state']}: {after['uncertainty_reason']})"
        )
    return after


# -- display ------------------------------------------------------------------
def describe_channel(view: dict[str, Any], ch: ChannelSettings | None, pump: str | None) -> list[str]:
    name = f" ({ch.name})" if ch else ""
    lines = [f"Channel {view['channel']}{name}"]
    effective = view.get("effective_state", view["position_state"])
    if effective == "KNOWN":
        lines.append(f"  Position: KNOWN, {position.ml_text(view['current_displacement_nl'], signed=True)} from HOME "
                     "(withdrawal positive, infusion negative)")
    elif effective == "IN_USE":
        owner = view.get("owner") or {}
        lines.append(f"  Position: IN USE by {owner.get('owner')} (pid {owner.get('pid')}); last saved "
                     f"{view['position_state']} {position.ml_text(view['current_displacement_nl'], signed=True)}")
    else:
        lines.append(f"  Position: {effective}")
    home = view.get("home")
    if view["home_defined"] and home:
        lines.append(f"  HOME: \"{home['reference_description']}\", established {home['established_at_utc']} "
                     f"(syringe volume at HOME: {volume_at_home_text(home['syringe_volume_at_home_ml'])})")
        identity = view.get("configuration_identity") or {}
        lines.append(f"  HOME syringe: {identity.get('diameter_mm')} mm diameter, {identity.get('capacity_ml')} mL capacity, "
                     f"pump {identity.get('pump_identity')}")
    else:
        lines.append("  HOME: not established")
    move = view.get("last_confirmed_movement")
    if move:
        lines.append(f"  Last confirmed move: {move['direction']} {move['volume_ml']:g} mL by {move['source']} "
                     f"(run {move['run_id']}, {move['completed_at_utc']})")
    totals = view.get("tracked_since_home") or {}
    if view["home_defined"]:
        lines.append(f"  Tracked since HOME: withdrawn {position.ml_text(totals.get('withdrawn_nl', 0))}, "
                     f"infused {position.ml_text(totals.get('infused_nl', 0))}, {totals.get('movements', 0)} move(s)")
    for problem in view.get("problems") or []:
        lines.append(f"  Problem: {problem}")
    if effective not in {"KNOWN", "IN_USE"} and view.get("uncertainty_reason"):
        lines.append(f"  Reason: {view['uncertainty_reason']}")
        if view.get("last_trusted_displacement_ml") is not None:
            lines.append(f"  Last trusted displacement before the uncertainty: {view['last_trusted_displacement_ml']:+g} mL")
    lines.append("  Return to HOME: " + home_plan_text(view, ch, pump))
    return lines


def volume_at_home_text(volume: float | None) -> str:
    return "not set" if volume is None else f"{volume:g} mL"


def home_plan_text(view: dict[str, Any], ch: ChannelSettings | None, pump: str | None) -> str:
    effective = view.get("effective_state", view["position_state"])
    if effective != "KNOWN":
        return f"unavailable ({effective})"
    if ch is None or pump is None:
        return "not evaluated (channel or pump not configured)"
    try:
        plan = position.plan_home(view, ch.identity(pump))
    except (position.HomeRefused, ManualControlError) as exc:
        return f"refused - {exc}"
    if not plan.moves:
        return "already at HOME; no motion needed"
    seconds = move_seconds(plan.volume_ml, ch.rate_ml_min, UNITS_ML_MIN)
    return (f"{plan.direction.upper()} {position.ml_text(plan.volume_nl)} at {ch.rate_ml_min:g} mL/min "
            f"(about {format_seconds(seconds)})")


def describe_doses(records: list[dict[str, Any]], directory: Path) -> list[str]:
    if not records:
        return [f"Reagent dose records ({directory}): none"]
    return [f"Reagent dose records ({directory}):"] + [
        f"  {r['experiment_id']}: {r['status']}" for r in records
    ]


# -- commands -----------------------------------------------------------------
def cmd_status(args, settings: ManualSettings, channels: list[int], input_fn) -> int:
    root = position.state_root(args.mock)
    print(f"Ledger: {root} ({'MOCK' if args.mock else 'live rig'} record)")
    views = position.inspect_ledger(root)
    owner = views[1]["owner"]
    print("Ledger owner: " + ("none" if owner is None else
                              f"IN USE by {owner.get('owner')} (pid {owner.get('pid')}) since {owner.get('acquired_at_utc')}"))
    pump = settings.pump_identity(bool(args.mock))
    for ch in channels:
        print("\n".join(describe_channel(views[ch], settings.channels.get(ch), pump)))
    print("\n".join(describe_doses(dose_records(args.dose_ledger_dir), args.dose_ledger_dir)))
    if args.live or args.mock:
        with pump_session(settings, bool(args.mock)):
            pass
    else:
        print("Pump: not contacted (add --live to check communication).")
    print(DISCLAIMER)
    return 0


def cmd_verify(args, settings: ManualSettings, channels: list[int], input_fn) -> int:
    results: list[tuple[str, str, str]] = []

    def add(level: str, label: str, detail: str = "") -> None:
        results.append((level, label, detail))

    mock = bool(args.mock)
    pump = settings.pump_identity(mock)
    add("PASS" if pump else "FAIL", "Pump identity", pump or f"no serial port in {settings.machine_config}")
    views = position.inspect_ledger(position.state_root(mock))
    owner = views[1]["owner"]
    add("PASS" if owner is None else "FAIL", "Ledger ownership",
        "free" if owner is None else f"in use by {owner.get('owner')} (pid {owner.get('pid')})")
    records = dose_records(args.dose_ledger_dir)
    for ch in channels:
        cs = settings.channels[ch]
        label = f"Channel {ch} ({cs.name})"
        if cs.diameter_mm is None or cs.capacity_ml is None:
            add("FAIL", f"{label} syringe", "diameter/capacity not configured")
        else:
            add("PASS", f"{label} syringe", f"{cs.diameter_mm:g} mm, {cs.capacity_ml:g} mL from {cs.geometry_source}")
            if (cs.recipe_diameter_mm is not None and abs(cs.recipe_diameter_mm - cs.diameter_mm) > 1e-9) or (
                    cs.recipe_capacity_ml is not None and abs(cs.recipe_capacity_ml - cs.capacity_ml) > 1e-9):
                add("WARN", f"{label} experiment geometry",
                    f"differs from {settings.syringe_config}: experiment moves will make this channel UNKNOWN")
        add("PASS", f"{label} manual transfer", f"{cs.transfer_volume_ml:g} mL at {cs.rate_ml_min:g} mL/min")
        if not cs.home_enabled:
            add("WARN", f"{label} HOME", "home.enabled is false; set-home and home are refused")
        if cs.syringe_volume_at_home_ml is None:
            add("WARN", f"{label} travel limits", "syringe_volume_at_home_ml not set; manual infuse/withdraw refused (HOME return still allowed)")
        view = views[ch]
        text = home_plan_text(view, cs, pump)
        ready = view["effective_state"] == "KNOWN" and not text.startswith(("refused", "not evaluated"))
        detail = text if view["effective_state"] == "KNOWN" else "; ".join(
            view["problems"] or [view.get("uncertainty_reason") or view["effective_state"]])
        add("PASS" if ready else "FAIL", f"{label} HOME reference",
            f"{view['effective_state']}: {detail}" + ("" if ready else f". {RECOVERY}"))
        if ch == 2:
            for direction in ("withdraw", "infuse"):
                reason = channel2_refusal(direction, records, True)
                if reason:
                    add("FAIL" if direction == "withdraw" else "WARN", f"Channel 2 {direction} dose protection", reason)
    if args.live or args.mock:
        try:
            with pump_session(settings, mock) as session:
                add("PASS", "Pump communication", "HELP answered")
                for ch in channels:
                    try:
                        confirm_idle(session, ch)
                        add("PASS", f"Channel {ch} idle", "STOP positively acknowledged")
                    except ManualControlError as exc:
                        add("FAIL", f"Channel {ch} idle", str(exc))
        except Exception as exc:  # noqa: BLE001 - report every readiness failure
            add("FAIL", "Pump communication", f"{type(exc).__name__}: {exc}")
    else:
        add("WARN", "Pump communication", "not checked offline (add --live)")
    for level, label, detail in results:
        print(f"[{level}] {label}" + (f": {detail}" if detail else ""))
    failed = sum(level == "FAIL" for level, _, _ in results)
    print(f"VERIFY: {'READY' if not failed else f'NOT READY ({failed} failure(s))'}. No motion was commanded.")
    return 0 if not failed else 1


def cmd_prepare(args, settings: ManualSettings, channels: list[int], input_fn) -> int:
    mock = bool(args.mock)
    with ledger_session(mock, f"chemyx_control prepare ({mode_name(args)})") as ledger, pump_session(settings, mock) as pump:
        for ch in channels:
            cs = settings.channels[ch]
            cs.identity(settings.pump_identity(mock))  # geometry must be configured
            confirm_idle(pump, ch)
            pump.select_channel(ch)
            try:
                configure_pump(pump, settings.pump_config(cs, cs.transfer_volume_ml))
            finally:
                pump.select_channel(1)
            ledger.record_event("chemyx_manual_prepare", channel=ch, units="mL/min", syringe_diameter_mm=cs.diameter_mm,
                                rate_ml_min=cs.rate_ml_min, mode=mode_name(args))
            state = ledger.state(ch)
            saved = (state.get("configuration_identity") or {}).get("diameter_mm")
            note = ""
            if state["home_defined"] and saved is not None and abs(saved - cs.diameter_mm) > 1e-9:
                note = f" WARNING: HOME was saved with {saved:g} mm; HOME return is refused until HOME is re-established."
            print(f"Channel {ch} ({cs.name}): units mL/min, diameter {cs.diameter_mm:g} mm, rate {cs.rate_ml_min:g} mL/min applied.{note}")
    print("PREPARE complete. No motion was commanded and no position was changed.")
    return 0


def cmd_set_home(args, settings: ManualSettings, channels: list[int], input_fn) -> int:
    mock = bool(args.mock)
    pump_id = settings.pump_identity(mock)
    identities = {}
    for ch in channels:
        cs = settings.channels[ch]
        if not cs.home_enabled:
            raise ManualControlError(f"Channel {ch} has home.enabled: false")
        identities[ch] = cs.identity(pump_id)
    with ledger_session(mock, f"chemyx_control set-home ({mode_name(args)})") as ledger, pump_session(settings, mock) as pump:
        for ch in channels:
            confirm_idle(pump, ch)
            state = ledger.state(ch)
            cs = settings.channels[ch]
            print("\n".join(describe_channel(dict(state, effective_state=state["position_state"]), cs, pump_id)[:3]))
            print(f"  New HOME: \"{cs.reference_description}\" (syringe volume at HOME: "
                  f"{volume_at_home_text(cs.syringe_volume_at_home_ml)}), "
                  f"{cs.diameter_mm:g} mm / {cs.capacity_ml:g} mL syringe")
        if 2 in channels:
            open_doses = [r for r in dose_records(args.dose_ledger_dir) if r["status"] != "CONFIRMED"]
            if open_doses:
                print("NOTE: set-home does not change reagent dose records; these still protect Channel 2: "
                      + ", ".join(f"{r['experiment_id']} ({r['status']})" for r in open_doses))
        prompt = ("Each plunger listed above is physically at its HOME position and stopped. "
                  "Save that position as HOME = 0 mL? This does not move the pump. [y/N]: ")
        if not confirm(prompt, mock=mock, input_fn=input_fn):
            print("SET-HOME cancelled. No reference was changed.")
            return 1
        for ch in channels:
            confirm_idle(pump, ch)  # recheck after the human pause
        for ch in channels:
            saved = ledger.set_home(identities[ch], receipt_id=str(uuid.uuid4()),
                                    established_by=f"chemyx_control set-home ({mode_name(args)})")
            print(f"Channel {ch}: HOME saved and verified (revision {saved['revision']}, journal sequence "
                  f"{saved['last_journal_sequence']}); displacement 0 mL")
    print("SET-HOME complete. No pump motion was commanded; the plunger position was not measured.")
    return 0


def _plan_moves(args, settings: ManualSettings, channels: list[int], ledger: position.PositionLedger,
                command: str) -> tuple[dict[int, position.MovePlan], list[str]]:
    mock = bool(args.mock)
    pump_id = settings.pump_identity(mock)
    records = dose_records(args.dose_ledger_dir)
    plans, refusals = {}, []
    for ch in channels:
        cs = settings.channels[ch]
        try:
            if command == "home" and not cs.home_enabled:
                raise position.HomeRefused(f"Channel {ch} has home.enabled: false")
            identity = cs.identity(pump_id)
            state = ledger.state(ch)
            plan = (position.plan_home(state, identity) if command == "home"
                    else position.plan_transfer(state, identity, command, cs.transfer_volume_ml))
        except (position.HomeRefused, ManualControlError) as exc:
            refusals.append(str(exc))
            continue
        if plan.moves and ch == 2:
            reason = channel2_refusal(plan.direction, records, args.allow_channel2_infusion)
            if reason:
                refusals.append(reason)
                continue
        plans[ch] = plan
    return plans, refusals


def cmd_move(args, settings: ManualSettings, channels: list[int], input_fn, command: str) -> int:
    mock = bool(args.mock)
    label = command.upper()
    with ledger_session(mock, f"chemyx_control {command} ({mode_name(args)})") as ledger:
        plans, refusals = _plan_moves(args, settings, channels, ledger, command)
        if refusals:
            print(f"{label} REFUSED - no pump command was sent:")
            for reason in refusals:
                print(f"  - {reason}")
            if command == "home":
                print(RECOVERY)
            ledger.record_event("chemyx_manual_refused", command=command, channels=channels, reasons=refusals)
            return 1
        for ch, plan in plans.items():
            cs = settings.channels[ch]
            if plan.moves:
                print(f"Channel {ch} ({cs.name}): {plan.direction.upper()} {position.ml_text(plan.volume_nl)} at "
                      f"{cs.rate_ml_min:g} mL/min; displacement {position.ml_text(plan.displacement_before_nl, signed=True)} "
                      f"-> {position.ml_text(plan.displacement_after_nl, signed=True)}")
            else:
                print(f"Channel {ch} ({cs.name}): already at HOME (displacement 0 mL); no motion.")
        moving = [plan for plan in plans.values() if plan.moves]
        if not moving:
            print(f"{label} complete. No command was sent to the pump.")
            return 0
        recorder = manual_recorder(mock, command)
        recorder.record("manual_control_started", command=command, channels=channels, mode=mode_name(args),
                        plans={str(p.channel): {"direction": p.direction, "volume_ml": p.volume_ml} for p in moving})
        print(f"Run journal: {recorder.journal.path}")
        done: list[int] = []
        with pump_session(settings, mock) as pump:
            for plan in moving:
                confirm_idle(pump, plan.channel, recorder, f"manual_{command}")
            print("Fluid path check:")
            for plan in moving:
                print(f"  - {fluid_path_note(settings.channels[plan.channel], plan.direction, plan.volume_nl)}")
            if not confirm("Confirm the fluid paths above and start this pump motion? [y/N]: ", mock=mock, input_fn=input_fn):
                print(f"{label} cancelled. No motion was commanded.")
                return 1
            try:
                for plan in moving:  # sequential: each channel is verified before the next
                    if ledger.state(plan.channel)["revision"] != plan.revision:
                        raise ManualControlError(f"Channel {plan.channel} record changed during confirmation; nothing moved")
                    confirm_idle(pump, plan.channel, recorder, f"manual_{command}")
                    after = execute_move(pump, ledger, settings, settings.channels[plan.channel], plan,
                                         recorder, mock=mock, command=command)
                    done.append(plan.channel)
                    print(f"Channel {plan.channel}: {plan.direction} complete; displacement now "
                          f"{position.ml_text(after['current_displacement_nl'], signed=True)}"
                          + (" (HOME reached, software estimate)" if command == "home" else ""))
            except BaseException as exc:
                print(f"{label} STOPPED: {type(exc).__name__}: {exc}")
                for ch in channels:
                    state = ledger.state(ch)
                    status = "completed" if ch in done else "not completed"
                    print(f"  Channel {ch}: {status}; position {state['position_state']} "
                          f"{position.ml_text(state['current_displacement_nl'], signed=True)}"
                          + (f" ({state['uncertainty_reason']})" if state["uncertainty_reason"] else ""))
                print("Completed channels were not reversed. " + RECOVERY)
                raise
    print(f"{label} complete for channel(s) {', '.join(map(str, done))}.")
    return 0


def cmd_stop(args, settings: ManualSettings, channels: list[int], input_fn) -> int:
    mock = bool(args.mock)
    results: dict[int, str] = {}
    try:
        with pump_session(settings, mock) as pump:
            for ch in channels:
                state = base.PumpSafetyState(channel=ch)
                status = base.attempt_emergency_stop(pump, state)
                results[ch] = status.value
                print(f"Channel {ch}: STOP {status.value}" + (f" ({state.last_stop_error})" if state.last_stop_error else ""))
    except Exception as exc:  # noqa: BLE001 - still invalidate the saved position
        print(f"STOP could not be sent: {type(exc).__name__}: {exc}. If the pump may be moving, use the "
              "Chemyx front-panel STOP or switch it off.")
    try:
        with ledger_session(mock, f"chemyx_control stop ({mode_name(args)})") as ledger:
            for ch in channels:
                state = ledger.state(ch)
                if state["home_defined"] and (state["position_known"] or state["pending_motion"]):
                    ledger.mark_unknown(ch, f"manual STOP issued ({results.get(ch, 'not sent')}); the Chemyx does not "
                                            "report whether it was moving")
                    print(f"Channel {ch}: saved position marked UNKNOWN. {RECOVERY}")
            ledger.record_event("chemyx_manual_stop", channels=channels, stop_results={str(k): v for k, v in results.items()})
    except position.LedgerBusyError as exc:
        print(f"Position ledger not updated: {exc}")
    ok = len(results) == len(channels) and all(value == base.StopStatus.SUCCEEDED.value for value in results.values())
    print("STOP " + ("acknowledged on every selected channel." if ok else "NOT confirmed on every selected channel."))
    return 0 if ok else 1


# -- entry point --------------------------------------------------------------
def mode_name(args) -> str:
    return "live" if args.live else "mock" if args.mock else "offline"


def build_parser(command: str | None = None) -> argparse.ArgumentParser:
    prog = "chemyx_home.py" if command == "home" else "chemyx_control.py"
    parser = argparse.ArgumentParser(
        prog=prog,
        description=("Return Chemyx channel(s) to their saved software HOME using normal infusion/withdrawal."
                     if command == "home" else
                     "Standalone Chemyx Fusion 4000 control with a custom software HOME (not a native pump command)."),
    )
    if command is None:
        parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--channel", choices=("1", "2", "both"), help="Chemyx channel 1, 2 or both")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--live", action="store_true", help="Contact the real pump")
    modes.add_argument("--mock", action="store_true", help="Use the mock pump and the separate mock ledger")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG, help="Manual control YAML")
    parser.add_argument("--dose-ledger-dir", type=Path, help="Reagent dose ledger to respect (default from the YAML)")
    parser.add_argument("--allow-channel2-infusion", action="store_true",
                        help="Authorize a Channel 2 infusion for this one command after routing the reagent line to waste")
    return parser


def main(argv: list[str] | None = None, *, command: str | None = None, input_fn: Callable[[str], str] = input) -> int:
    parser = build_parser(command)
    args = parser.parse_args(argv)
    command = command or args.command
    if args.channel is None:
        if command not in {"status", "verify"}:
            parser.error(f"{command} requires --channel 1, 2 or both")
        args.channel = "both"
    if command not in {"status", "verify"} and not (args.live or args.mock):
        parser.error(f"{command} requires --live or --mock")
    channels = [1, 2] if args.channel == "both" else [int(args.channel)]
    print(f"CHEMYX MANUAL CONTROL | {command.upper()} | {mode_name(args).upper()} | channel(s) {', '.join(map(str, channels))}")
    try:
        settings = load_manual_config(args.config)
        missing = [ch for ch in channels if ch not in settings.channels]
        if missing:
            raise ManualControlError(f"Channel(s) {missing} are not configured in {settings.path}")
        if args.dose_ledger_dir is None:
            args.dose_ledger_dir = settings.dose_ledger_dir
        print(f"Config: {settings.path}\nMachine: {settings.machine_config} | Chemyx port: {settings.port or 'unset'}")
        handlers = {"status": cmd_status, "verify": cmd_verify, "prepare": cmd_prepare, "set-home": cmd_set_home,
                    "stop": cmd_stop}
        if command in handlers:
            return handlers[command](args, settings, channels, input_fn)
        return cmd_move(args, settings, channels, input_fn, command)
    except KeyboardInterrupt:
        print(f"CHEMYX {command.upper()}: INTERRUPTED. Any interrupted move was stopped and its channel left UNKNOWN.")
        return 1
    except Exception as exc:  # noqa: BLE001 - one clear operator-facing failure line
        print(f"CHEMYX {command.upper()}: FAILED ({type(exc).__name__}: {exc})")
        return 1
