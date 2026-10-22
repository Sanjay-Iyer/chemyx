"""Live review wiring for every simple run; all instrument activity is mocked."""
from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path
import sys

import pytest
import yaml

from chemyx_lab.workflows import simple_review


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
SCRIPTS = ["20_stage1stage2.py", "21_stage2only.py", "22_3instrumentquickcheck.py",
           "23_stage1stage2.py", "24_stage2only.py", "25_2instrumentquickcheck.py"]


def load(filename):
    spec = importlib.util.spec_from_file_location("review_test_" + Path(filename).stem, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("filename", SCRIPTS)
def test_every_cli_enables_the_shared_live_review_launcher(filename, monkeypatch):
    workflow = load(filename)
    calls = []

    def physical_run(cfg, **kwargs):
        calls.append(kwargs)
        return (True if filename.startswith("22") else "COMPLETE"), {"nmr": "PASS"}

    monkeypatch.setattr(workflow, "run", physical_run)
    assert workflow.main(["--mock"]) == 0
    assert calls[0]["review_launcher"] is simple_review.start_review


@pytest.mark.parametrize("filename", SCRIPTS)
@pytest.mark.parametrize("broken", [False, True])
def test_review_starts_before_instruments_and_failure_never_changes_the_run(tmp_path, filename, broken):
    workflow = load(filename)
    cfg = workflow.load_config()
    if "arduino" in cfg:
        cfg["arduino"]["position_file"] = str(tmp_path / "simple_needle_position.txt")
    if "stages" in cfg:
        for stage in cfg["stages"].values():
            stage.update(duration_minutes=30, interval_minutes=30)
    if "experiment" in cfg:
        if "observation_count" in cfg["experiment"]:
            cfg["experiment"]["observation_count"] = 2
        else:
            cfg["experiment"].update(duration_minutes=30, interval_minutes=15)
    factory = inspect.signature(workflow.run).parameters["rig_factory"].default
    events, reviews = [], []

    def rig_factory(*args, **kwargs):
        events.append("instrument rig")
        return factory(*args, **kwargs)

    def launcher(run, raw, settings, log):
        assert not events  # Neither a serial connection nor an instrument object exists yet.
        reviews.append((run, raw))
        if broken:
            raise OSError("review executable unavailable")

    plain = workflow.run(cfg, mock=True, output_dir=tmp_path / "plain")
    reviewed = workflow.run(cfg, mock=True, output_dir=tmp_path / "reviewed",
                            rig_factory=rig_factory, review_launcher=launcher)
    assert reviewed == plain
    assert len(reviews) == (2 if filename.startswith(("20", "23")) else 1)
    assert all(run.is_dir() for run, _ in reviews)
    raw_before = {p.relative_to(tmp_path / "plain"): p.read_bytes() for p in (tmp_path / "plain").rglob("*.dx")}
    raw_after = {p.relative_to(tmp_path / "reviewed"): p.read_bytes() for p in (tmp_path / "reviewed").rglob("*.dx")}
    assert raw_before and raw_after == raw_before
    assert not list((tmp_path / "reviewed").rglob("*.json"))
    if broken:
        assert "review executable unavailable" in (tmp_path / "reviewed/log.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("name,stage", [("20_TEST", "stage_1"), ("23_TEST", "stage_2"),
                                       ("21_TEST", None), ("22_TEST", None),
                                       ("24_TEST", None), ("25_TEST", None)])
def test_child_watches_each_actual_raw_directory_without_waiting_or_opening_instruments(
        tmp_path, monkeypatch, name, stage):
    run = tmp_path / name
    if stage:
        run /= stage
    run.mkdir(parents=True)
    raw = run if name.startswith(("24", "25")) else run / "nmr_raw"
    calls = []

    class UnreadableChild:
        def __getattribute__(self, attr):
            raise AssertionError("The instrument process must never inspect or wait for review")

    def spawn(command, **kwargs):
        calls.append((command, kwargs))
        return UnreadableChild()

    monkeypatch.setattr(simple_review.subprocess, "Popen", spawn)
    simple_review.start_review(run, raw, {}, logging.getLogger("review_test"))
    [(command, options)] = calls
    assert command[command.index("--raw-dir") + 1] == str(raw)
    assert command[command.index("--run-dir") + 1] == str(run)
    assert command[command.index("--parent-pid") + 1].isdigit()
    assert "--watch" in command and options["close_fds"] is True
    assert options["stdin"] == simple_review.subprocess.DEVNULL
    settings = yaml.safe_load((run / "review_config.yaml").read_text(encoding="utf-8"))["analysis"]
    assert settings["dataset_display_name"] == name + (f" {stage}" if stage else "")
    assert settings["every_n_acquisitions"] == 1
    assert settings["save_spectrum_plots"] and settings["save_area_vs_time"]
    assert settings["target_peak"]["prominence_snr"] == 8.0
    assert settings["target_peak"]["rel_height"] == .95
    assert not list(run.rglob("*.json"))


def test_disabled_review_does_not_spawn_or_create_outputs(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Review is disabled")
    monkeypatch.setattr(simple_review.subprocess, "Popen", forbidden)
    simple_review.begin_review(tmp_path, tmp_path, {"review": {"enabled": False}},
                               logging.getLogger("review_test"), forbidden)
    simple_review.start_review(tmp_path, tmp_path, {"review": {"enabled": False}}, logging.getLogger("review_test"))
    assert not list(tmp_path.iterdir())
