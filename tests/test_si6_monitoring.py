import copy
from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from chemyx_lab.runtime_journal import OperationJournal, RunRecorder
from chemyx_lab.runtime_state import replay_journal
from chemyx_lab.workflows.si6_automated_nmr import (
    MeasurementObservation,
    RunOutcome,
    Stage,
    StageOutcome,
    TerminalStatus,
    build_stages,
    load_si6_config,
    run_monitoring_stage,
    run_stage_sequence,
    scheduled_measurement_offset_seconds,
)

CONFIG = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "experiments"
    / "02_si6_automated_nmr.yaml"
)


class FakeClock:
    def __init__(self, *, sleep_overshoot=0.0):
        self.seconds = 0.0
        self.origin = datetime(2026, 7, 22, 12, 0, 0)
        self.sleep_overshoot = sleep_overshoot

    def monotonic(self):
        return self.seconds

    def wall_now(self):
        return self.origin + timedelta(seconds=self.seconds)

    def sleep(self, _label, seconds):
        self.seconds += max(0.0, seconds) + self.sleep_overshoot

    def advance(self, seconds):
        self.seconds += seconds


class MemoryRecorder:
    def __init__(self):
        self.records = []

    def record(self, event_type, **fields):
        record = {"event_type": event_type, **fields}
        self.records.append(record)
        return record


def stage(**overrides):
    values = dict(
        name="monitoring",
        operator_prompt="prepare",
        interval_minutes=1.0,
        max_hours=1.0,
        measure_immediately=False,
        plateau_stopping_enabled=False,
        max_measurements=3,
    )
    values.update(overrides)
    return Stage(**values)


def observation(*, plateau=False, valid=True, clock=None, duration=0.0):
    if clock is not None:
        started = clock.wall_now().isoformat(timespec="seconds")
        clock.advance(duration)
        completed = clock.wall_now().isoformat(timespec="seconds")
    else:
        started = completed = "2026-07-22T12:00:00"
    return MeasurementObservation(valid, plateau, started, completed, completed)


def run_with(stage_value, callback, *, clock=None, recorder=None):
    clock = clock or FakeClock()
    return run_monitoring_stage(
        stage_value,
        callback,
        recorder=recorder,
        monotonic_fn=clock.monotonic,
        wall_now_fn=clock.wall_now,
        sleep_fn=clock.sleep,
    )


def test_disabled_mode_ignores_early_plateau_but_records_metrics():
    recorder = MemoryRecorder()
    seen = []

    def measure(schedule):
        seen.append(schedule.scheduled_measurement_number)
        return observation(plateau=True)

    result = run_with(stage(), measure, recorder=recorder)

    assert seen == [1, 2, 3]
    assert result.outcome.status is TerminalStatus.COMPLETED
    assert result.outcome.stage_outcome is StageOutcome.SCHEDULED_MONITORING_COMPLETED
    ignored = [
        record
        for record in recorder.records
        if record["event_type"] == "plateau_detection"
    ]
    assert len(ignored) == 3
    assert all(item["result_classification"] == "detected_but_ignored" for item in ignored)
    assert all(
        "plateau_detected" in record
        for record in recorder.records
        if record["event_type"] == "measurement_completed"
    )


def test_enabled_mode_stops_early_on_plateau():
    seen = []

    def measure(schedule):
        seen.append(schedule.scheduled_measurement_number)
        return observation(plateau=schedule.scheduled_measurement_number == 2)

    result = run_with(
        stage(plateau_stopping_enabled=True, max_measurements=6), measure
    )

    assert seen == [1, 2]
    assert result.outcome.status is TerminalStatus.COMPLETED
    assert result.outcome.stage_outcome is StageOutcome.PLATEAU_REACHED
    assert result.plateau_measurement_number == 2


def test_enabled_mode_limit_is_noncompletion_and_blocks_next_stage():
    stages = [
        stage(name="one", plateau_stopping_enabled=True, max_measurements=2),
        stage(name="two"),
    ]
    prompts = []

    outcome = run_stage_sequence(
        stages,
        prompts.append,
        lambda value: run_with(
            value, lambda _schedule: observation(plateau=False)
        ).outcome,
    )

    assert outcome.status is TerminalStatus.PLATEAU_NOT_REACHED_WITHIN_LIMIT
    assert outcome.stage_outcome is StageOutcome.PLATEAU_NOT_REACHED_WITHIN_LIMIT
    assert prompts == ["prepare"]


def test_fixed_count_completion_is_distinct_from_runtime_limit():
    completed = run_with(stage(max_measurements=1), lambda _s: observation())
    clock = FakeClock()
    timed_out = run_with(
        stage(max_measurements=2, max_hours=0.025),
        lambda _s: observation(clock=clock, duration=40),
        clock=clock,
    )

    assert completed.outcome.stage_outcome is StageOutcome.SCHEDULED_MONITORING_COMPLETED
    assert timed_out.outcome.status is TerminalStatus.MAXIMUM_DURATION_REACHED
    assert timed_out.outcome.stage_outcome is StageOutcome.RUNTIME_LIMIT_REACHED


@pytest.mark.parametrize(
    ("immediate", "expected"),
    [(False, [60.0, 120.0, 180.0]), (True, [0.0, 60.0, 120.0])],
)
def test_first_measurement_behavior_is_explicit(immediate, expected):
    value = stage(measure_immediately=immediate)
    assert [
        scheduled_measurement_offset_seconds(value, number)
        for number in range(1, 4)
    ] == expected


def test_twenty_four_hourly_measurements_have_exact_slots():
    value = stage(
        interval_minutes=60,
        max_measurements=24,
        max_hours=26,
        measure_immediately=False,
    )
    starts = []
    result = run_with(
        value,
        lambda schedule: (
            starts.append(schedule.scheduled_monotonic) or observation()
        ),
    )

    assert result.scheduled_measurements == 24
    assert starts == [3600.0 * number for number in range(1, 25)]


def test_cycle_duration_does_not_accumulate_schedule_drift():
    clock = FakeClock()
    starts = []

    def measure(schedule):
        starts.append(schedule.actual_cycle_start_monotonic)
        return observation(clock=clock, duration=20)

    run_with(stage(), measure, clock=clock)

    assert starts == [60.0, 120.0, 180.0]


def test_delayed_cycle_records_lateness():
    clock = FakeClock(sleep_overshoot=7.5)
    recorder = MemoryRecorder()
    run_with(stage(max_measurements=1), lambda _s: observation(), clock=clock, recorder=recorder)

    started = next(
        item for item in recorder.records if item["event_type"] == "measurement_started"
    )
    assert started["scheduling_delay_seconds"] == pytest.approx(7.5)


def test_invalid_analysis_consumes_slot_but_not_valid_analysis_count():
    values = iter([False, True, True])
    result = run_with(
        stage(),
        lambda _schedule: observation(valid=next(values)),
    )

    assert result.scheduled_measurements == 3
    assert result.acquisition_attempts == 3
    assert result.valid_analysis_count == 2
    assert result.outcome.stage_outcome is StageOutcome.SCHEDULED_MONITORING_COMPLETED


def test_replay_reconstructs_monitoring_mode_and_counts(tmp_path):
    clock = FakeClock()
    journal = OperationJournal(
        tmp_path / "operation_journal.jsonl",
        "run-1",
        monotonic_fn=clock.monotonic,
        utc_now_fn=clock.wall_now,
    )
    recorder = RunRecorder(journal, tmp_path / "run_state.json")
    result = run_with(stage(max_measurements=2), lambda _s: observation(), clock=clock, recorder=recorder)

    replay = replay_journal(journal.path)
    progress = replay.state.monitoring_progress
    assert result.outcome.stage_outcome is StageOutcome.SCHEDULED_MONITORING_COMPLETED
    assert progress["monitoring_mode"] == "fixed_scheduled_count"
    assert progress["max_measurements"] == 2
    assert progress["scheduled_measurement_number"] == 2
    assert progress["valid_analysis_count"] == 2
    assert progress["result_classification"] == "scheduled_monitoring_completed"


def test_active_configuration_has_deliberate_monitoring_policies():
    raw = load_si6_config(CONFIG)
    stages = build_stages(raw["workflow"])

    assert stages[0].measure_immediately is False
    assert stages[0].plateau_stopping_enabled is False
    assert stages[0].max_measurements == 24
    assert stages[0].max_hours == 26
    assert all(stage_value.plateau_stopping_enabled for stage_value in stages[1:])
    assert all(stage_value.max_measurements == 6 for stage_value in stages[1:])
    assert all(stage_value.max_hours == 2 for stage_value in stages[1:])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("plateau_stopping_enabled", "yes", "must be Boolean"),
        ("measure_immediately", 1, "must be Boolean"),
        ("max_measurements", 0, "positive integer"),
        ("max_measurements", 1.5, "positive integer"),
        ("max_hours", 0, "must be positive"),
    ],
)
def test_invalid_stage_monitoring_fields_fail_validation(tmp_path, field, value, message):
    raw = copy.deepcopy(load_si6_config(CONFIG))
    raw["workflow"]["initial_stage"][field] = value
    path = tmp_path / "invalid.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))

    with pytest.raises(ValueError, match=message):
        load_si6_config(path)


def test_unknown_stage_field_and_duplicate_names_are_rejected(tmp_path):
    raw = copy.deepcopy(load_si6_config(CONFIG))
    raw["workflow"]["initial_stage"]["peak_threshold_enabled"] = True
    path = tmp_path / "unknown.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    with pytest.raises(ValueError, match="Unknown workflow.initial_stage"):
        load_si6_config(path)

    del raw["workflow"]["initial_stage"]["peak_threshold_enabled"]
    raw["workflow"]["first_addition_stage"]["name"] = "initial_reaction"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))
    with pytest.raises(ValueError, match="unique name"):
        load_si6_config(path)


def test_runtime_ceiling_must_extend_beyond_last_scheduled_slot(tmp_path):
    raw = copy.deepcopy(load_si6_config(CONFIG))
    raw["workflow"]["initial_stage"]["max_hours"] = 24
    path = tmp_path / "unsafe_schedule.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False))

    with pytest.raises(ValueError, match="extend beyond"):
        load_si6_config(path)
