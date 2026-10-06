"""HOME-only end-to-end orchestration, configuration and recovery evidence."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.analysis import phase_audit
from chemyx_lab.analysis.stage_completion import completion_evidence
from chemyx_lab.runtime_state import replay_journal
from chemyx_lab.workflows import si6_automated_nmr as base
from chemyx_lab.workflows import three_instrument_si6 as si6
from chemyx_lab.workflows.dose_guard import DoseGuard, DoseReplayBlocked

TEMPLATES = config.REPO_ROOT / "config_templates/experiments"
ARDUINO = config.REPO_ROOT / "arduino/configs/arduino.example.yaml"


def recipe():
    return yaml.safe_load((TEMPLATES / "si6_two_stage_fast_sim.yaml").read_text())


def rig(tmp_path, raw=None):
    raw = deepcopy(raw or recipe())
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    path = tmp_path / "workflow.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return si6.open_services(
        *si6.prepare(path, tmp_path / "no_machine.yaml", ARDUINO, mock=True),
        identity=si6.RunIdentity("si6", True),
    )


def events(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def transfers(records):
    return [
        e
        for e in records
        if e.get("operation_type") in {"withdraw", "infuse"}
        and e.get("lifecycle_state") == "completed"
    ]


@pytest.fixture(scope="module")
def complete_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("complete_two_stage")
    with rig(tmp) as s:
        result = si6.run_experiment(s)
        assert result.status is base.TerminalStatus.COMPLETED
        return s.paths, events(s.paths.journal_jsonl), s


def test_exact_cycle_sequence_and_needle_context(complete_run):
    paths, records, s = complete_run
    context = [
        e
        for e in records
        if e["event_type"] == "pump_needle_context" and e["cycle_number"] == 1
    ]
    assert [
        (e["operation_type"], e["requested_volume_ml"], e["channel"], e["needle_state"])
        for e in context
    ] == [
        ("withdraw", 8, 1, "UP"),
        ("withdraw", 5, 1, "DOWN"),
        ("infuse", 13, 1, "DOWN"),
        ("withdraw", 5, 1, "UP"),
        ("infuse", 5, 1, "UP"),
    ]
    nmr = next(e["sequence"] for e in records if e["event_type"] == "nmr_retrieved")
    assert context[1]["sequence"] < nmr < context[2]["sequence"]
    needle = [
        e
        for e in records
        if e["event_type"] == "needle_transition"
        and e["result_classification"] == "completed"
    ]
    assert needle[0]["target"] == "UP"
    assert context[0]["sequence"] < needle[1]["sequence"] < context[1]["sequence"]
    assert context[2]["sequence"] < needle[2]["sequence"] < context[3]["sequence"]
    assert s.channel_states[1].retained_volume_ml == 0
    assert s.channel_states[2].retained_volume_ml == pytest.approx(0.2)


def test_scans_cadences_and_trends_honored(complete_run):
    paths, records, _ = complete_run
    assert all(e["scans"] == 8 for e in records if e["event_type"] == "nmr_retrieved")
    starts = [e for e in records if e["event_type"] == "monitoring_stage_started"]
    assert [
        (e["workflow_phase"], e["interval_minutes"], e["hard_runtime_ceiling_hours"])
        for e in starts
    ] == [("stage_1", 120, 48), ("stage_2", 30, 6)]
    for name, hours, trend in [
        ("stage_1", 2, "decreasing"),
        ("stage_2", 0.5, "increasing"),
    ]:
        selected = [
            e
            for e in records
            if e["event_type"] == "nmr_acquisition_time" and e["workflow_phase"] == name
        ]
        times = [datetime.fromisoformat(e["acquired_at"]) for e in selected]
        assert all(
            (b - a).total_seconds() == hours * 3600 for a, b in zip(times, times[1:])
        )
        assert all(e["timestamp_source"] == "LONG DATE header" for e in selected)
        summary = json.loads(
            (paths.run_dir / "stages" / name / "summary.json").read_text()
        )
        assert summary["completion"]["complete"]
        assert summary["trend"] == trend


def test_exactly_one_configured_dose_between_completed_stages(complete_run):
    paths, records, s = complete_run
    doses = [e for e in transfers(records) if e["channel"] == 2]
    assert len(doses) == 1
    assert doses[0]["requested_parameters"]["volume"] == 1.8
    receipt = json.loads(
        (paths.run_dir / "transition/channel2_addition.json").read_text()
    )
    assert receipt["status"] == "CONFIRMED" and receipt["rate_ml_min"] == 1
    confirmation = next(e for e in records if e["event_type"] == "dose_confirmed")
    stage1 = [
        e
        for e in records
        if e["event_type"] == "stage_completion_evidence"
        and e["workflow_phase"] == "stage_1"
    ][-1]
    stage2 = next(
        e
        for e in records
        if e["event_type"] == "measurement_started" and e["workflow_phase"] == "stage_2"
    )
    assert stage1["evidence"]["complete"]
    assert (
        stage1["sequence"]
        < doses[0]["sequence"]
        < confirmation["sequence"]
        < stage2["sequence"]
    )
    with pytest.raises(DoseReplayBlocked):
        DoseGuard(
            s.dose_guard.path.parent, "SIMULATION_ONLY", recipe(), "restart"
        ).reserve()


def test_reports_dataset_titles_manifest_and_independent_csvs(complete_run):
    paths, records, _ = complete_run
    import csv

    final = json.loads((paths.run_dir / "final/experiment_summary.json").read_text())
    assert final["status"] == "completed" and final["simulation_only"]
    totals = 0
    for name in ("stage_1", "stage_2"):
        directory = paths.run_dir / "stages" / name
        with (directory / "time_series.csv").open(newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) >= 8 and all(r["stage"] == name for r in rows)
        assert all(r["timestamp_source"] == "LONG DATE header" for r in rows)
        assert all((paths.run_dir / r["raw_path"]).is_file() for r in rows)
        totals += len(rows)
        for ext in ("png", "svg", "pdf"):
            assert (directory / f"time_series.{ext}").is_file()
    with (paths.run_dir / "final/full_time_series.csv").open(newline="") as f:
        assert len(list(csv.DictReader(f))) == totals
    manifest = json.loads((paths.run_dir / "final/plot_manifest.json").read_text())
    assert len(manifest["figures"]) == 9
    for figure in manifest["figures"]:
        assert figure["dataset_display_name"] == paths.run_dir.name
        assert figure["visible_title"].startswith(paths.run_dir.name + " ")
        assert Path(figure["path"]).is_file()
    svg = (paths.run_dir / "final/full_time_series.svg").read_text()
    assert paths.run_dir.name in svg and "Channel-2 addition" in svg
    replay = replay_journal(paths.journal_jsonl)
    assert (
        replay.valid and replay.state.pump_channels["2"]["cumulative_infused_ml"] == 1.8
    )
    assert all(
        "channel" in e
        for e in records
        if e.get("operation_type") in {"withdraw", "infuse", "stop"}
    )


@pytest.mark.parametrize(
    "name", ["nominal", "fast_sim", "stage1_development", "stage2_development"]
)
def test_templates_validate(name):
    raw = base.load_si6_config(TEMPLATES / f"si6_two_stage_{name}.yaml")
    assert len(base.build_stages(raw["workflow"])) == 2


@pytest.mark.parametrize(
    "mutation",
    [
        "invalid_channel",
        "missing_channel2",
        "down_initial",
        "wrong_needle_order",
        "bad_trend",
        "no_dose_rate",
        "before_dose",
        "fixed_completion",
        "unknown_threshold",
        "infinite_threshold",
    ],
)
def test_invalid_configs_rejected_before_motion(tmp_path, mutation):
    raw = recipe()
    if mutation == "invalid_channel":
        raw["workflow"]["cycle"][0]["channel"] = 3
    if mutation == "missing_channel2":
        del raw["pump"]["channels"]["2"]
    if mutation == "down_initial":
        raw["workflow"]["initial_needle_position"] = "DOWN"
    if mutation == "wrong_needle_order":
        raw["workflow"]["cycle"][1]["position"] = "UP"
    if mutation == "bad_trend":
        raw["workflow"]["initial_stage"]["completion"]["trend"] = "increasing"
    if mutation == "no_dose_rate":
        del raw["workflow"]["initial_stage"]["after_monitoring"][1]["rate_ml_min"]
    if mutation == "before_dose":
        raw["workflow"]["first_addition_stage"]["before_monitoring"] = raw["workflow"][
            "initial_stage"
        ]["after_monitoring"]
    if mutation == "fixed_completion":
        raw["workflow"]["first_addition_stage"]["plateau_stopping_enabled"] = False
    if mutation == "unknown_threshold":
        raw["workflow"]["initial_stage"]["completion"]["magic"] = True
    if mutation == "infinite_threshold":
        raw["analysis"]["measurement_qc"]["noise_multiplier"] = float("inf")
    with pytest.raises(ValueError):
        with rig(tmp_path, raw):
            pytest.fail("Invalid configuration opened services")


@pytest.mark.parametrize("point", [0, 1, 2, 3, 4])
def test_interruptions_during_sampling_do_not_dose_or_repeat(
    tmp_path, monkeypatch, point
):
    with pytest.raises(KeyboardInterrupt):
        with rig(tmp_path) as s:
            original = s.pump_move
            calls = 0

            def interrupt(*args, **kwargs):
                nonlocal calls
                if calls == point:
                    raise KeyboardInterrupt()
                calls += 1
                return original(*args, **kwargs)

            monkeypatch.setattr(s, "pump_move", interrupt)
            si6.run_experiment(s)
    records = events(s.paths.journal_jsonl)
    assert not any(e.get("channel") == 2 for e in transfers(records))
    assert s.dose_guard.data["status"] == "RESERVED"
    with pytest.raises(DoseReplayBlocked):
        DoseGuard(
            s.dose_guard.path.parent, "SIMULATION_ONLY", recipe(), "restart"
        ).reserve()


@pytest.mark.parametrize(
    "point", ["before_dose", "during_dose", "after_dose", "stage2"]
)
def test_transition_crash_windows_block_replay(tmp_path, monkeypatch, point):
    # Reports are tested in complete_run; keep fault injection focused on state.
    monkeypatch.setattr(si6, "write_stage_reports", lambda *a, **k: [])
    with pytest.raises(RuntimeError):
        with rig(tmp_path) as s:
            original_move, original_mark, original_sample = (
                s.pump_move,
                s.dose_guard.mark,
                si6.sample_cycle,
            )

            def move(*args, **kwargs):
                if kwargs.get("channel") == 2 and point == "during_dose":
                    # Fail after dispatch, through the actual metered-move path.
                    monkeypatch.setattr(
                        s.pump,
                        "start",
                        lambda **k: (_ for _ in ()).throw(
                            RuntimeError("dispatch fault")
                        ),
                    )
                return original_move(*args, **kwargs)

            def mark(status, **kwargs):
                if status == "DISPATCH_INTENT" and point == "before_dose":
                    raise RuntimeError("before intent")
                if status == "CONFIRMED" and point == "after_dose":
                    raise RuntimeError("receipt fault")
                return original_mark(status, **kwargs)

            def sample(*args, **kwargs):
                if kwargs["stage"] == "stage_2" and point == "stage2":
                    raise RuntimeError("stage2 interrupted")
                return original_sample(*args, **kwargs)

            monkeypatch.setattr(s, "pump_move", move)
            monkeypatch.setattr(s.dose_guard, "mark", mark)
            monkeypatch.setattr(si6, "sample_cycle", sample)
            si6.run_experiment(s)
    doses = [e for e in transfers(events(s.paths.journal_jsonl)) if e["channel"] == 2]
    assert len(doses) == (1 if point in {"after_dose", "stage2"} else 0)
    assert (
        s.dose_guard.data["status"]
        == {
            "before_dose": "RESERVED",
            "during_dose": "DISPATCH_INTENT",
            "after_dose": "DISPATCH_INTENT",
            "stage2": "CONFIRMED",
        }[point]
    )
    with pytest.raises(DoseReplayBlocked):
        DoseGuard(
            s.dose_guard.path.parent, "SIMULATION_ONLY", recipe(), "restart"
        ).reserve()


def test_minimum_evidence_cannot_be_overridden_by_operator_advance(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(si6, "write_stage_reports", lambda *a, **k: [])
    with rig(tmp_path) as s:
        s.simulation.fixture["stages"]["stage_1"] = [100] * 24
        outcome = si6.run_experiment(s, stage_decision=lambda *_: "advance")
        assert outcome.status is base.TerminalStatus.ANALYSIS_INCONCLUSIVE
        assert s.dose_guard.data["status"] == "RESERVED"
        assert not any(
            e["channel"] == 2 for e in transfers(events(s.paths.journal_jsonl))
        )


def test_stage2_does_not_complete_one_flat_or_still_growing_interval():
    raw = recipe()
    rules = raw["workflow"]["first_addition_stage"]["completion"]
    origin = datetime.fromisoformat("2026-01-01T00:00:00+00:00")
    from datetime import timedelta

    for values in ([0, 25, 55, 76, 76, 76, 89, 91], [0, 25, 55, 76, 89, 91, 92, 94]):
        rows = [
            dict(
                peak_area=a,
                peak_clear=True,
                acquired_at=(origin + timedelta(minutes=30 * i)).isoformat(),
            )
            for i, a in enumerate(values)
        ]
        assert not completion_evidence(rows, rules)["complete"]


def test_optional_git_is_not_an_offline_dependency(monkeypatch):
    def missing_git(*args, **kwargs):
        raise FileNotFoundError("No Git installed")

    monkeypatch.setattr(phase_audit.subprocess, "run", missing_git)
    value = phase_audit.provenance()
    assert value["git_commit"] == "unavailable"


def test_repository_resource_paths_independent_of_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert config.resolve_repo_path("configs/nmr/analysis.yaml").is_file()
    assert (
        si6.run_root(recipe(), si6.RunIdentity("si6", False))
        == config.REPO_ROOT / "results/runs/si6_two_stage_fast_sim"
    )


def test_processing_acquisition_identity_does_not_collide(tmp_path):
    paths = base.create_run_paths(tmp_path)

    class Completed:
        returncode = 0

    commands = []

    def runner(command, **kwargs):
        commands.append(command)
        return Completed()

    first = base.run_process_fid_postprocessing(
        tmp_path / "same_stage_name_0001.dx", paths, "dataset", runner=runner
    )
    second = base.run_process_fid_postprocessing(
        tmp_path / "same_stage_name_0002.dx", paths, "dataset", runner=runner
    )
    assert first != second
    first.mkdir(parents=True)
    with pytest.raises(base.AnalysisInconclusiveError, match="overwrite"):
        base.run_process_fid_postprocessing(
            tmp_path / "same_stage_name_0001.dx", paths, "dataset", runner=runner
        )
