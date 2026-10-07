"""Count/time-limited toluene soak using the existing Si6 services and SOP."""
from __future__ import annotations

import time
from dataclasses import replace
from datetime import datetime

from .. import config
from ..analysis.peak_tracking import identification_config
from ..analysis.si6_final_qc import write_final_qc
from ..runtime_state import write_json_atomic
from . import pump_channels, si6_automated_nmr as base, three_instrument_si6 as si6
from .dose_guard import validate_experiment_id

STAGE = "toluene_soak"
COLUMNS = [*base.TIME_SERIES_COLUMNS, "timestamp", "peak_found", "channel2_action", "runtime_elapsed_hours"]


def channel2_actions(raw):
    settings = raw["soak_test"]["channel2"]
    return (
        {"action": "needle", "position": "DOWN"},
        {"action": "infuse", "channel": 2, "volume_ml": settings["volume_ml"],
         "rate_ml_min": settings["rate_ml_min"], "needle_position": "DOWN"},
        {"action": "needle", "position": "UP"},
    )


def configure_soak(raw):
    """Derive the shared stage settings from the one editable soak section."""
    settings = base._mapping(raw.get("soak_test"), "soak_test")
    base._reject_unknown(settings, {"iterations", "interval_minutes", "max_hours", "channel2"}, "soak_test")
    count = base._positive_integer(settings.get("iterations"), "soak_test.iterations")
    interval = base._positive(settings.get("interval_minutes"), "soak_test.interval_minutes")
    hours = base._positive(settings.get("max_hours"), "soak_test.max_hours")
    dose = base._mapping(settings.get("channel2"), "soak_test.channel2")
    base._reject_unknown(dose, {"enabled", "trigger_iteration", "volume_ml", "rate_ml_min"}, "soak_test.channel2")
    base._required_bool(dose.get("enabled"), "soak_test.channel2.enabled")
    trigger = base._positive_integer(dose.get("trigger_iteration"), "soak_test.channel2.trigger_iteration")
    if dose["enabled"] and trigger > count:
        raise ValueError("Channel 2 trigger_iteration must be within soak_test.iterations")
    base._positive(dose.get("volume_ml"), "soak_test.channel2.volume_ml")
    base._positive(dose.get("rate_ml_min"), "soak_test.channel2.rate_ml_min")
    workflow = raw["workflow"]
    validate_experiment_id(workflow.get("experiment_id"))
    stage = dict(name=STAGE, operator_prompt="Automatic continuous toluene soak after start confirmation.",
                 interval_minutes=interval, max_hours=hours, measure_immediately=True,
                 plateau_stopping_enabled=False)
    if workflow.get("initial_stage", stage) != stage or workflow.get("first_addition_stage") or workflow.get("repeating_stages") or workflow.get("repeat_addition_rounds", 0):
        raise ValueError("Soak has one derived stage and no chemistry endpoints or stage actions")
    if workflow.get("initial_needle_position") != "UP" or "simulation" in raw:
        raise ValueError("Real soak requires initial_needle_position UP and no simulation section")
    workflow["initial_stage"] = stage
    si6.cycle_values(raw)
    if any(pump_channels.operation_channel(raw, event) != 1 for event in workflow["cycle"] if event["action"] in {"withdraw", "infuse"}):
        raise ValueError("Repeated soak sampling must use Channel 1")
    if any(config.resolve_units(c["units"]) != 0 for c in pump_channels.channel_definitions(raw).values()):
        raise ValueError("Soak rate_ml_min commands require mL/min units")
    if raw["analysis"].get("peak_area") != {"method": "trapezoid"} or set(raw["analysis"]) - {"peak_finding", "peak_area", "area_epsilon"}:
        raise ValueError("Soak uses production peak_finding and moving trapezoidal peak_area only")
    identification_config(raw["analysis"].get("peak_finding"))
    reporting = base._mapping(raw.get("qc_reporting"), "qc_reporting")
    base._required_bool(reporting.get("enabled"), "qc_reporting.enabled")
    if reporting.get("affect_workflow") is not False:
        raise ValueError("Soak QC must be retrospective: affect_workflow=false")
    if raw["three_instrument"].get("initial_plateau_stopping_enabled") is not False:
        raise ValueError("Soak must disable chemistry plateau stopping")
    if dose["enabled"]:
        actions = channel2_actions(raw)
        pump_channels.validate_stage_actions(raw, list(actions), "soak Channel 2")
        pump_channels.validate_channel_capacity(raw, [replace(base.build_stages(workflow)[0], after_monitoring=actions)])
    return settings


def _write_series(s, rows, elapsed):
    for row in rows:
        row.update(timestamp=row.get("acquired_at"), peak_found=row.get("peak_ppm") is not None)
        row.setdefault("channel2_action", "none")
    if rows:
        rows[-1]["runtime_elapsed_hours"] = elapsed / 3600
    base.write_csv(s.paths.time_series_csv, rows, COLUMNS)


def _reports(s, rows, outcome, reason, elapsed):
    """Reuse existing retrospective plots; report failure cannot change outcome."""
    try:
        _write_series(s, rows, elapsed)
        write_json_atomic(s.paths.run_dir / "soak_summary.json", {
            "dataset_display_name": s.dataset_name, "status": outcome.status.value,
            "termination_reason": reason, "recorded_observations": len(rows),
            "runtime_elapsed_hours": elapsed / 3600, "settings": s.raw["soak_test"],
            "channel2": s.dose_guard.data if s.dose_guard else None,
            "chemistry_endpoints_enabled": False,
        })
        figures = write_final_qc(s.paths, rows, base.build_stages(s.raw["workflow"]),
                                 s.raw["qc_reporting"], dataset=s.dataset_name, outcome=outcome)
        s.plot_manifest = [*(s.plot_manifest or []), *figures]
        s.write_manifest()
    except Exception as exc:
        print(f"Soak reporting unavailable ({type(exc).__name__}: {exc}); outcome remains {outcome.status.value}.")


def run_soak(s, *, monotonic_fn=time.monotonic):
    settings = configure_soak(s.raw)
    if s.identity.kind != "soak" or s.dose_guard is None:
        raise si6.VerificationError("Soak requires its dedicated services and durable run reservation")
    if s.dose_guard.data["status"] != "RESERVED":
        raise si6.VerificationError("Soak replay is refused; Channel 2 dispatch state already exists")
    rows = []
    started = datetime.now()
    beginning = monotonic_fn()
    deadline = beginning + settings["max_hours"] * 3600
    interval = settings["interval_minutes"] * 60
    reason = "iteration_count"
    try:
        si6.preflight(s)
        si6.home_and_raise(s)
        for iteration in range(1, settings["iterations"] + 1):
            scheduled = beginning + (iteration - 1) * interval
            delay = min(scheduled, deadline) - monotonic_fn()
            if delay > 0:
                s.sleep("Next soak iteration", delay)
            if monotonic_fn() >= deadline:
                reason = "maximum_duration"
                break
            print(f"SOAK TEST ITERATION {iteration}")
            s.record("soak_iteration", workflow_phase=STAGE, cycle_number=iteration, status="STARTED")
            row = si6.sample_cycle(s, stage=STAGE, cycle=iteration, rows=rows, started=started)
            _write_series(s, rows, monotonic_fn() - beginning)
            if monotonic_fn() >= deadline:
                reason = "maximum_duration"
                break
            dose = settings["channel2"]
            if dose["enabled"] and iteration == dose["trigger_iteration"]:
                # Persist intent before any dose-boundary movement. No retry/resume.
                s.dose_guard.mark("DISPATCH_INTENT", source_stage=STAGE, iteration=iteration,
                                  channel=2, volume_ml=dose["volume_ml"], rate_ml_min=dose["rate_ml_min"])
                row["channel2_action"] = "DISPATCH_INTENT"
                _write_series(s, rows, monotonic_fn() - beginning)
                s.record("dose_dispatch_intent", **s.dose_guard.data)
                print("Executing configured Channel 2 test infusion...")
                si6.run_stage_actions(s, channel2_actions(s.raw), stage=STAGE, phase="after_iteration",
                                      cycle=iteration, rows=rows, started=started)
                si6.confirm_channel2_action(s, dose, confirmation_iteration=iteration)
                row["channel2_action"] = "CONFIRMED"
                _write_series(s, rows, monotonic_fn() - beginning)
                print("Channel 2 test infusion complete.\nContinuing soak test.")
        # Every sample cycle already returned the sample and performed UP cleanup.
        s.stop_all(stage=STAGE)
        s.assert_pump_idle()
        si6.verify_needle(s.needle, si6.positions(s.arduino_cfg, mock=s.mock)[0])
        outcome = base.RunOutcome(base.TerminalStatus.COMPLETED, f"Soak completed {len(rows)} iterations; limit: {reason}.")
        s.record("terminal", workflow_phase=STAGE, terminal_status=outcome.status.value,
                 result_classification=reason, physical_state_certainty="certain")
    except BaseException as exc:
        # Do not attempt extra motion after uncertain hardware state or Ctrl+C.
        s.stop_all(stage=STAGE)
        s.needle.stop_best_effort()
        outcome = base.RunOutcome(base.TerminalStatus.OPERATOR_ABORTED if isinstance(exc, KeyboardInterrupt) else base.TerminalStatus.INSTRUMENT_FAILURE, str(exc))
        try:
            s.record("terminal", workflow_phase=STAGE, terminal_status=outcome.status.value,
                     result_classification="soak_failed", physical_state_certainty="requires_inspection",
                     operator_review_required=True, error_message=str(exc))
        except BaseException:
            pass
        _reports(s, rows, outcome, "operational_failure", monotonic_fn() - beginning)
        raise
    _reports(s, rows, outcome, reason, monotonic_fn() - beginning)
    return outcome
