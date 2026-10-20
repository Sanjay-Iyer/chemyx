"""Stage 2 ONLY scheduling and live-path tests; every transport is a fake."""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from arduino.python.errors import ProcessLockError
from arduino.python.needle_state import NeedleState, NeedleStateStore
from arduino.scripts._common import controller_session
from chemyx_lab import config
from chemyx_lab.workflows import si6_fixed_time as fixed, si6_stage2only as stage2
from chemyx_lab.workflows import chemyx_manual as manual, chemyx_position as position


class Recorder:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append(dict(event_type=event, **fields))


class ScheduleRig:
    def __init__(self, seconds=60):
        self.clock = fixed.SimulatedClock()
        self.recorder = Recorder()
        self.seconds, self.starts = seconds, []

    def sample(self, acquire):
        self.starts.append(self.clock.now())
        self.clock.sleep(self.seconds)
        try:
            return acquire()
        except Exception as exc:
            return {"status": "NMR_FAILED", "error": str(exc)}


@pytest.mark.parametrize("outcome", ["saved", "missing", "failed"])
def test_six_hours_initial_and_endpoint_with_all_nmr_outcomes(outcome):
    raw, *_ = stage2.load_settings()
    rig, rows, calls = ScheduleRig(), [], []
    def acquire(stage, number):
        calls.append((stage, number))
        if outcome == "failed":
            raise ConnectionError("NMR disconnected")
        return {"status": "NMR_SAVED" if outcome == "saved" else "NMR_MISSING"}
    result = stage2.run_stage2only(raw["stage"], rig, acquire, rows.append)
    assert rig.starts == list(range(0, 21601, 1800))
    assert calls == [("stage_2", i) for i in range(1, 14)]
    assert [r["scheduled_seconds"] for r in rows] == rig.starts
    assert [r["endpoint"] for r in rows] == [False] * 12 + [True]
    assert result["duration_seconds"] == 21600 and result["elapsed_seconds"] == 21660
    assert result["skipped_slots"] == 0


def test_slow_cycles_skip_missed_slots_without_losing_final_measurement():
    rig, rows = ScheduleRig(seconds=3700), []
    result = stage2.run_stage2only({"name": "stage_2", "hours": 2, "interval_minutes": 30}, rig,
                                 lambda *_: {"status": "NMR_FAILED"}, rows.append)
    assert rig.starts == [0, 5400, 9100]
    assert [r["scheduled_seconds"] for r in rows] == [0, 5400, 7200]
    assert result["skipped_slots"] == 2 and result["elapsed_seconds"] == 12800
    assert rows[-1]["endpoint"]  # one endpoint, after previous cleanup finishes


def test_clock_is_rechecked_when_sleep_returns_early_and_optional_export_fails():
    rig = ScheduleRig()
    sleep = rig.clock.sleep
    rig.clock.sleep = lambda seconds: sleep(min(seconds, 123))
    result = stage2.run_stage2only({"name": "stage_2", "hours": 1, "interval_minutes": 30}, rig,
        lambda *_: {"status": "NMR_MISSING"}, lambda _: (_ for _ in ()).throw(OSError("export unavailable")))
    assert rig.starts == [0, 1800, 3600] and result["elapsed_seconds"] == 3660


def test_new_config_reuses_sampling_recipe_without_stage1_or_dose():
    raw, _, _, nmr = stage2.load_settings()
    old, *_ = fixed.load_settings()
    assert raw["stage"] == {"name": "stage_2", "hours": 6, "interval_minutes": 30, "include_endpoint": True}
    assert [{k: v for k, v in event.items() if k != "needle_position"} for event in raw["workflow"]["cycle"]] == old["workflow"]["cycle"]
    assert set(raw["pump"]["channels"]) == {"1"} and "dose" not in raw["workflow"]
    assert raw["output"]["run_root_dir"] == "results/stage2only" and nmr.scans == 8


@pytest.mark.parametrize("mutation", ["dose", "channel2", "stage1", "no_endpoint", "unbalanced", "bad_interval"])
def test_bad_configuration_cannot_enable_another_stage_or_dose(tmp_path, mutation):
    raw = yaml.safe_load(stage2.DEFAULT_CONFIG.read_text())
    if mutation == "dose":
        raw["workflow"]["dose"] = {"channel": 2, "volume_ml": 1.86}
    elif mutation == "channel2":
        raw["workflow"]["cycle"][0]["channel"] = 2
    elif mutation == "stage1":
        raw["stage"]["name"] = "stage_1"
    elif mutation == "no_endpoint":
        raw["stage"]["include_endpoint"] = False
    elif mutation == "unbalanced":
        raw["workflow"]["cycle"][-1]["volume_ml"] = 4
    else:
        raw["stage"]["interval_minutes"] = 0
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError):
        stage2.load_settings(path)


class NoLock:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def live(tmp_path, monkeypatch):
    """Exercise mock=False without opening a hardware transport."""
    raw, cfg, settings, nmr = stage2.load_settings()
    cfg = deepcopy(cfg)
    cfg["needle"]["state_path"] = str(tmp_path / "needle_state.json")
    cfg["results"]["run_root_dir"] = str(tmp_path / "arduino_runs")
    root = tmp_path / "pump_state"
    pump_id = settings.pump_identity(False)
    with position.PositionLedger(root, owner="operator manual setup") as ledger:
        for ch in (1, 2):
            ledger.set_home(settings.channels[ch].identity(pump_id), receipt_id=f"OFFICIAL_HOME_{ch}", established_by="operator")
        token = ledger.begin_motion(2, position.motion_fields(pump_id, 20, 20), "infuse", 1.86,
                                   operation_id="already_dosed", source="prior Phase 1 transition", run_id="old")
        ledger.complete_motion(token)
    NeedleStateStore(cfg["needle"]["state_path"]).save(NeedleState(
        logical_position=2, position_valid=True, reason="motion_completed",
        last_updated=datetime.now(timezone.utc).isoformat(), steps_per_unit=200, up_step_sign=1))
    original_pump = manual.pump_session
    monkeypatch.setattr(stage2, "controller_session", lambda c, _mode, **kw: controller_session(c, "mock", **kw))
    monkeypatch.setattr(manual, "pump_session", lambda s, _mock: original_pump(s, True))
    monkeypatch.setattr(stage2, "PortProcessLock", NoLock)
    monkeypatch.setattr(stage2, "start_review_worker", lambda output: {
        "status": "STARTED", "pid": 123, "index": str(Path(output) / "analysis_live/index.html"), "controls_run": False})
    monkeypatch.setattr(position, "state_root", lambda _: root)
    def forbidden(*_, **__):
        pytest.fail("stage2only attempted an interactive prompt, dose or live HOME establishment")
    monkeypatch.setattr("builtins.input", forbidden)
    monkeypatch.setattr(fixed, "DoseGuard", forbidden)
    monkeypatch.setattr(fixed.Rig, "dose", forbidden)
    monkeypatch.setattr(position.PositionLedger, "set_home", forbidden)
    ns = SimpleNamespace(raw=raw, cfg=cfg, settings=settings, nmr=nmr, root=root, tmp=tmp_path, count=0)
    def run(acquisition=None):
        ns.count += 1
        return stage2.run(raw, cfg, settings, nmr, mock=False, output=tmp_path / f"run_{ns.count}",
                          clock=fixed.SimulatedClock(), acquisition=acquisition or (lambda *_: {"status": "NMR_MISSING"}))
    ns.run = run
    return ns


def rows(report):
    return [json.loads(line) for line in (Path(report["results"]) / "operation_journal.jsonl").read_text().splitlines()]


def test_full_live_path_has_13_measurements_and_preserves_both_homes_and_existing_dose(live):
    before = {ch: (live.root / f"channel_{ch}_state.json").read_bytes() for ch in (1, 2)}
    # Do not write to the user's runtime directory: point at isolated history.
    live.settings = replace(live.settings, dose_ledger_dir=live.tmp / "old_doses")
    previous_doses = live.settings.dose_ledger_dir
    previous_doses.mkdir()
    old = previous_doses / "RESERVED_OR_CORRUPT.json"
    old.write_bytes(b'{"interrupted old record')
    result = stage2.run(live.raw, live.cfg, live.settings, live.nmr, mock=False, output=live.tmp / "full_run",
        clock=fixed.SimulatedClock(), acquisition=lambda *_: (_ for _ in ()).throw(ConnectionError("NMR disconnected")))
    assert result["status"] == "COMPLETE" and result["physical_cleanup"]
    assert not result["stage_1_performed"] and not result["dose_performed"] and not result["interactive_prompts"]
    assert len(result["observations"]) == 13
    assert [r["scheduled_seconds"] for r in result["observations"]] == list(range(0, 21601, 1800))
    assert all(r["status"] == "NMR_FAILED" for r in result["observations"])
    assert result["stages"][0]["elapsed_seconds"] > 21600
    assert (live.root / "channel_2_state.json").read_bytes() == before[2]
    assert old.read_bytes() == b'{"interrupted old record'
    after1 = json.loads((live.root / "channel_1_state.json").read_text())
    assert after1["home"] == json.loads(before[1])["home"] and after1["current_displacement_nl"] == 0
    operations = rows(result)
    motions = [r for r in operations if r.get("operation_type") in {"withdraw", "infuse"}]
    assert motions and all(r["channel"] == 1 for r in motions)
    assert not [r for r in operations if r["event_type"].startswith("dose_")]
    assert [r["stage"] for r in operations if r["event_type"] == "stage_started"] == ["stage_2"]
    assert not list(Path(result["results"]).rglob("*dose*"))
    with position.PositionLedger(live.root, owner="inspect") as ledger:
        assert not ledger.notices and ledger.state(1)["reference_valid"]
        assert ledger.state(2)["current_displacement_nl"] == -1_860_000
    needle = NeedleStateStore(live.cfg["needle"]["state_path"]).load()
    assert needle.position_valid and needle.logical_position == 1 and needle.home_position == 0


def test_missing_channel2_home_is_not_a_stage2_admission_requirement(live):
    # Simulate a rig where only the sampling channel has ever been referenced.
    with position.PositionLedger(live.root, owner="isolate unused channel") as ledger:
        ledger._commit(2, position.blank_state(2), "unused_channel_no_home")
    live.raw["stage"].update(hours=.0001, interval_minutes=.003)
    assert live.run()["status"] == "COMPLETE"


def test_unknown_needle_is_never_replaced_and_no_liquid_moves(live):
    store = NeedleStateStore(live.cfg["needle"]["state_path"])
    store.save(replace(store.load(), position_valid=False, reason="motion_failed_or_interrupted"))
    original = store.path.read_bytes()
    result = live.run()
    assert result["status"] == "FAILED" and store.path.read_bytes() == original
    assert not [r for r in rows(result) if r.get("operation_type") in {"withdraw", "infuse"}]


def test_active_controller_still_prevents_two_runs_from_driving_same_pump(live):
    with position.PositionLedger(live.root, owner="other active run"):
        result = live.run()
    assert result["status"] == "FAILED" and "owned by another process" in result["error"]


def test_nmr_lock_failure_does_not_end_stage2(live, monkeypatch):
    class Owned(NoLock):
        def __enter__(self):
            raise ProcessLockError("NMR is owned elsewhere")
    monkeypatch.setattr(stage2, "PortProcessLock", Owned)
    live.raw["stage"].update(hours=.0001, interval_minutes=.003)
    def forbidden(*_):
        pytest.fail("No NMR START when another program owns it")
    result = live.run(acquisition=forbidden)
    assert result["status"] == "COMPLETE"
    assert all(r["requested"] is False for r in result["observations"])


def test_channel2_transfer_and_dose_are_unavailable_even_if_called_directly():
    rig = stage2.Stage2OnlyRig(None, None, None, None, None, None, None, None, None, None, None)
    with pytest.raises(RuntimeError, match="Channel 2"):
        rig.move({"action": "infuse", "channel": 2})
    with pytest.raises(RuntimeError, match="never delivers"):
        rig.dose()


def test_mock_entry_point_completes_and_analysis_inventory_reads_stage2only(tmp_path, monkeypatch):
    output = tmp_path / "stage2only_mock"
    proc = subprocess.run([sys.executable, str(config.REPO_ROOT / "scripts/06_si6_stage2only_run.py"),
                           "--mock", "--output-dir", str(output)], cwd=config.REPO_ROOT,
                          text=True, capture_output=True, timeout=180)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    report = json.loads((output / "report.json").read_text())
    assert report["workflow"] == "stage2only" and report["status"] == "COMPLETE"
    assert len(list((output / "nmr_raw").glob("*.dx"))) == 13
    assert not list(output.rglob("*dose*"))
    monkeypatch.syspath_prepend(str(config.REPO_ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("stage2only_inventory", config.REPO_ROOT / "scripts/05_analyze_fixed_time_run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    inventory = module.inventory(output)
    assert len(inventory) == 13 and all(r["stage"] == "stage_2" and r["analysed"] for r in inventory)
    assert [r["scheduled_hours"] for r in inventory] == [i / 2 for i in range(13)]


def test_live_cli_dispatches_only_to_stage2_runner_without_prompt_or_old_run(monkeypatch, tmp_path):
    captured = []
    monkeypatch.setattr(fixed, "run", lambda *_a, **_kw: pytest.fail("Old two-stage runner invoked"))
    monkeypatch.setattr(stage2, "run", lambda *args, **kw: captured.append((args, kw)) or {"status": "COMPLETE"})
    assert stage2.main(["--live", "--machine-config", "configs/machines/si6_real_COM6.yaml", "--output-dir", str(tmp_path / "new_run")]) == 0
    assert captured[0][1]["mock"] is False and captured[0][0][2].port == "COM6"
    assert captured[0][1]["experiment_id"].startswith("SI6_STAGE2ONLY_")
