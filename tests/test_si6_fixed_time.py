"""Fixed-time schedule, physical integration and adversarial NMR tests: no hardware."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

from chemyx_lab import config
from chemyx_lab.runtime_journal import OperationJournal
from chemyx_lab.workflows import si6_fixed_time as fixed
from chemyx_lab.workflows import si6_fixed_time_nmr as nmr_io
from chemyx_lab.workflows import chemyx_manual as manual, chemyx_position as position
from chemyx_lab.workflows.dose_guard import DoseGuard, DoseReplayBlocked
from chemyx_lab.workflows.dose_recovery import reconcile


class Recorder:
    def __init__(self):
        self.rows = []

    def record(self, event, **fields):
        self.rows.append(dict(event=event, **fields))


class ScheduleRig:
    def __init__(self, seconds=60):
        self.clock = fixed.SimulatedClock()
        self.recorder = Recorder()
        self.seconds = seconds
        self.starts = []

    def sample(self, acquire):
        self.starts.append(self.clock.now())
        self.clock.sleep(self.seconds)
        try:
            return acquire()
        except Exception as exc:
            return {"status": "NMR_FAILED", "error": str(exc)}


@pytest.mark.parametrize("failure", ["saved", "missing", "failure"])
def test_full_54_hour_windows_and_intervals_even_when_every_nmr_fails(failure):
    raw, *_ = fixed.load_settings()
    rig = ScheduleRig()
    rows, transitions = [], []
    def acquisition(*_):
        if failure == "failure":
            raise OSError("connection/refused")
        return {"status": "NMR_MISSING" if failure == "missing" else "NMR_SAVED"}
    first = fixed.run_stage(raw["stages"][0], rig, acquisition, rows.append)
    assert first["elapsed_seconds"] == 48 * 3600
    assert rig.starts == list(range(0, 48 * 3600, 120 * 60))
    transitions.append(rig.clock.now())
    rig.clock.sleep(111.6)  # Dose outside both scheduled stage windows.
    second_start = rig.clock.now()
    second = fixed.run_stage(raw["stages"][1], rig, acquisition, rows.append)
    assert second["elapsed_seconds"] == 6 * 3600
    assert rig.starts[24:] == [second_start + i * 30 * 60 for i in range(12)]
    assert len(rows) == 36 and transitions == [48 * 3600]
    assert all(r["physical_cleanup"] == "COMPLETE" for r in rows)


def test_deadline_allows_active_cycle_to_finish_and_skips_missed_slots():
    rig = ScheduleRig(seconds=3700)
    rows = []
    result = fixed.run_stage({"name": "stage_1", "hours": 2, "interval_minutes": 30}, rig,
                             lambda *_: {"status": "NMR_MISSING"}, rows.append)
    assert rig.starts == [0, 5400]
    assert result["elapsed_seconds"] == 9100  # Active physical operation finishes.
    assert all(t < 7200 for t in rig.starts)


class NmrClient:
    def __init__(self, *, fault=None, raw=True, late=0):
        self.starts = 0
        self.fault, self.raw, self.late = fault, raw, late
        self.started = False
        self.polls = 0

    def iflow_experiment_status(self):
        if self.fault == "connection":
            raise ConnectionError("Ethernet unavailable")
        if self.fault == "unknown":
            return None
        if self.fault == "already_busy":
            return {"Running": True}
        if not self.started:
            return {"ResultCode": 0, "OriginalReceipt": {"TimeStamp": "old"}}
        self.polls += 1
        if self.fault == "timeout":
            return {"Running": True}
        if self.fault == "retrieval" and self.polls > 1:
            raise FileNotFoundError("server export share unavailable")
        result = {"ResultCode": 0, "OriginalReceipt": {"TimeStamp": "new"}}
        if self.fault == "stale":
            result["OriginalReceipt"]["TimeStamp"] = "old"
        if self.raw and self.polls > self.late:
            result["JDX_FileContents_TD"] = "##TITLE=raw\n##JCAMP-DX=5.0\n##END="
            result["JDX_FileContents_FD"] = "##TITLE=processed\n##END="
        return result

    def iflow_1d_settings(self):
        return {}

    def iflow_experiment_settings(self):
        return {}

    def set_iflow_1d_settings(self, settings):
        self.one_d = settings

    def run_iflow_experiment(self, settings):
        self.starts += 1
        self.started = True
        self.settings = settings
        if self.fault == "lost_start_reply":
            raise TimeoutError("START reply lost")


@pytest.mark.parametrize("fault,expected,starts", [
    (None, "NMR_SAVED", 1), ("connection", "NMR_FAILED", 0),
    ("unknown", "NMR_FAILED", 0), ("already_busy", "NMR_FAILED", 0),
    ("timeout", "NMR_FAILED", 1), ("lost_start_reply", "NMR_FAILED", 1),
    ("retrieval", "NMR_FAILED", 1), ("stale", "NMR_FAILED", 1)])
def test_nmr_faults_are_bounded_and_never_reissue_start(tmp_path, fault, expected, starts):
    *_, nmr = fixed.load_settings()
    nmr = replace(nmr, max_wait_seconds=5, poll_seconds=1)
    clock = fixed.SimulatedClock()
    client = NmrClient(fault=fault, raw=fault != "retrieval")
    result = nmr_io.acquire(nmr, tmp_path / "missing_folder/raw.dx", 3,
                            client=client, now=clock.now, sleep=clock.sleep)
    assert result["status"] == expected and client.starts == starts
    assert clock.now() <= 8
    if expected == "NMR_SAVED":
        assert "TITLE=raw" in Path(result["path"]).read_text()
        assert "processed" not in Path(result["path"]).read_text()
        assert client.settings["NumberOfScans"] == 8


@pytest.mark.parametrize("late,raw,expected", [(2, True, "NMR_SAVED"), (0, False, "NMR_MISSING")])
def test_missing_and_delayed_exports(tmp_path, late, raw, expected):
    *_, nmr = fixed.load_settings()
    clock = fixed.SimulatedClock()
    client = NmrClient(late=late, raw=raw)
    result = nmr_io.acquire(replace(nmr, poll_seconds=1), tmp_path / "new/raw.dx", 3,
        client=client, now=clock.now, sleep=clock.sleep)
    assert result["status"] == expected and client.starts == 1 and clock.now() <= 3
    assert (tmp_path / "new/raw.dx").exists() == raw


def test_nmr_worker_timeout_and_output_errors_do_not_escape(tmp_path, monkeypatch):
    *_, nmr = fixed.load_settings()
    calls = []
    def hung(*args, **kwargs):
        calls.append(args)
        assert kwargs["timeout"] < 1000
        raise subprocess.TimeoutExpired("NMR", kwargs["timeout"])
    monkeypatch.setattr(nmr_io.subprocess, "run", hung)
    assert nmr_io.acquire_bounded(nmr, tmp_path / "raw.dx", 2)["status"] == "NMR_FAILED"
    assert len(calls) == 1  # A pre-dispatch serialization error must not pass.
    blocked = tmp_path / "file"
    blocked.write_text("folder blocked")
    assert nmr_io.acquire_bounded(nmr, blocked / "raw.dx", 2)["status"] == "NMR_FAILED"
    assert len(calls) == 1


@pytest.fixture
def short_settings():
    raw, arduino, settings, nmr = fixed.load_settings()
    raw = deepcopy(raw)
    for stage in raw["stages"]:
        stage["hours"] = .01  # One complete physical cycle may cross the deadline.
    return raw, arduino, settings, nmr


@pytest.mark.parametrize("fault", ["missing", "exception"])
def test_real_mock_drivers_cleanup_and_one_confirmed_dose_despite_all_nmr_failures(tmp_path, short_settings, fault, monkeypatch):
    def forbidden(*_, **__):
        pytest.fail("Chemical analysis was called")
    monkeypatch.setattr(fixed.base, "run_nmr_acquisition", forbidden)
    monkeypatch.setattr(fixed.base, "run_process_fid_postprocessing", forbidden)
    def acquisition(*_):
        if fault == "exception":
            raise RuntimeError("NMR retrieval failed")
        return {"status": "NMR_MISSING", "error": "raw file absent"}
    report = fixed.run(*short_settings, mock=True, output=tmp_path / "new", acquisition=acquisition)
    assert report["status"] == "COMPLETE" and report["physical_cleanup"] and report["dose_confirmed"]
    rows = [json.loads(line) for line in (tmp_path / "new/operation_journal.jsonl").read_text().splitlines()]
    doses = [r for r in rows if r.get("channel") == 2 and r.get("operation_type") == "infuse" and r.get("lifecycle_state") == "completed"]
    assert len(doses) == 1 and doses[0]["requested_parameters"]["volume"] == 1.86
    assert len(report["stages"]) == 2
    assert (tmp_path / "new/observations.jsonl").is_file()
    assert not list((tmp_path / "new/nmr_raw").glob("*.dx"))
    assert all(r["status"] != "NMR_SAVED" for r in report["observations"])


def test_interrupted_dose_cannot_repeat_or_start_stage2(tmp_path, short_settings, monkeypatch):
    original = fixed.Rig.move
    attempts = []
    def interrupted(self, event):
        if event["channel"] == 2:
            attempts.append(event)
            raise KeyboardInterrupt()
        return original(self, event)
    monkeypatch.setattr(fixed.Rig, "move", interrupted)
    report = fixed.run(*short_settings, mock=True, output=tmp_path / "interrupted")
    assert report["status"] == "INTERRUPTED" and not report["physical_cleanup"]
    assert len(attempts) == 1 and len(report["stages"]) == 1
    record = json.loads(Path(report["dose_record"]).read_text())
    assert record["status"] == "DISPATCH_INTENT" and not report["dose_confirmed"]
    with pytest.raises(DoseReplayBlocked):
        DoseGuard(Path(report["dose_record"]).parent, report["experiment_id"], {}, "again").reserve()


def test_stale_software_owner_recovers_but_active_lock_and_pending_move_do_not(tmp_path):
    root = tmp_path / "state"
    root.mkdir()
    (root / "owner.json").write_text('{"pid": 99999999, "owner": "failed previous run"}')
    with position.PositionLedger(root, owner="new run") as ledger:
        identity = position.ChannelIdentity(1, "mock", 20, 20, "empty", 0)
        ledger.set_home(identity, receipt_id="test", established_by="test")
        with pytest.raises(position.LedgerBusyError):
            position.PositionLedger(root, owner="cannot clear active process").acquire()
        ledger.begin_motion(1, position.motion_fields("mock", 20, 20), "withdraw", 1,
            operation_id="interrupted", source="failed experiment", run_id="old")
    with position.PositionLedger(root, owner="recovery") as ledger:
        assert not ledger.state(1)["position_known"]
        with pytest.raises(position.HomeRefused):
            position.plan_home(ledger.state(1), identity)


@pytest.mark.parametrize("status", ["RESERVED", "DISPATCH_INTENT", "CONFIRMED"])
def test_manual_legacy_dose_reconciliation_preserves_status(tmp_path, status):
    guard = DoseGuard(tmp_path / "doses", "OLD", {}, "old-run")
    guard.reserve()
    journal = OperationJournal(tmp_path / "old/operation_journal.jsonl", "old-run")
    journal.append("experiment_reserved", **guard.data)
    if status != "RESERVED":
        guard.mark("DISPATCH_INTENT")
        journal.append("dose_dispatch_intent", dose_id=guard.data["dose_id"], experiment_id="OLD",
                       configuration_sha256=guard.data["configuration_sha256"], channel=2)
    if status == "CONFIRMED":
        guard.mark("CONFIRMED")
    assert not manual.dose_records(guard.path.parent)[0]["reconciled"]
    if status != "RESERVED":
        with pytest.raises(ValueError, match="unused is refused"):
            reconcile(guard.path.parent, "OLD", journal.path, "unused")
    reconcile(guard.path.parent, "OLD", journal.path, "unused" if status == "RESERVED" else "retired")
    assert manual.dose_records(guard.path.parent)[0]["reconciled"]
    assert json.loads(guard.path.read_text())["status"] == status
    with pytest.raises(DoseReplayBlocked):
        DoseGuard(guard.path.parent, "OLD", {}, "new").reserve()


def test_original_production_recipe_and_manual_defaults_are_independent():
    import yaml
    original = yaml.safe_load((config.REPO_ROOT / "configs/experiments/si6_real_two_stage.yaml").read_text())
    raw, _, settings, nmr = fixed.load_settings()
    assert nmr.scans == original["nmr"]["scans"] == 8
    for ch in (1, 2):
        assert raw["pump"]["channels"][str(ch)] == original["pump"]["channels"][str(ch)]
        assert settings.channels[ch].transfer_volume_ml == 1 and settings.channels[ch].rate_ml_min == 10
    for event, old in zip(raw["workflow"]["cycle"], original["workflow"]["cycle"]):
        if event["action"] in {"withdraw", "infuse", "pause", "nmr"}:
            assert event == old
    assert raw["workflow"]["dose"]["volume_ml"] == original["workflow"]["initial_stage"]["after_monitoring"][1]["volume_ml"]
