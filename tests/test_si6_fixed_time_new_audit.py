"""New independent audit: realistic NMReady protocol sequences, restart states and
full accelerated schedules. Fake RPC clients and mock transports only; no hardware.

Status replies follow the vendor's documented ``iFlow/ExperimentStatus`` shape in
docs/08_reference/vendor/nmr_rpc_api/html/rpc-api.md (top-level ResultCode, NumberOfScansRun,
OriginalReceipt, JDX_FileContents_TD/FD). The FID text is the real NMReady file
from the 2026-08-10 WORK run.
"""
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import sys

import pytest

from arduino.python.needle_state import NeedleState, NeedleStateStore
from chemyx_lab import config
from chemyx_lab.instruments.nmr import example_iflow_1d_settings, example_iflow_experiment_settings
from chemyx_lab.workflows import chemyx_manual as manual
from chemyx_lab.workflows import si6_fixed_time as fixed
from chemyx_lab.workflows import si6_fixed_time_nmr as nmr_io
from chemyx_lab.workflows.dose_guard import DoseGuard

FIXTURE = config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"
# The fixture was saved by a Windows text-mode write of the RPC string, which
# doubled every CR. The instrument's own exports (results/raw/nmr/06-08-26) use
# CRLF, so CRLF text is what the iFlow reply carries.
REAL_FID = FIXTURE.read_bytes().decode("utf-8").replace("\r\r\n", "\r\n")
OLD_FID = REAL_FID.replace("##LONG DATE=2026/08/10 17:27:21-0400", "##LONG DATE=2026/08/10 15:00:00-0400")
SPECTRUM = REAL_FID.replace("##DATA TYPE=NMR FID", "##DATA TYPE=NMR SPECTRUM")
OLD, NEW = "2026-10-17 08:00:00", "2026-10-17 10:00:05"


def saved(path):
    """Saved content with a doubled-CR write artifact normalized away."""
    return Path(path).read_bytes().decode("utf-8").replace("\r\r\n", "\r\n")


def receipt(stamp, **extra):
    return {"ExperimentNumber": 1, "ResultCode": 1, "TimeStamp": stamp,
            "Settings": {"NumberOfScans": 8, "ReceiverGain": 12}, **extra}


def status(code, scans_run, stamp, *, td="", fd="", filename=""):
    """Documented ExperimentStatus shape (vendor example: ResultCode 2, 0 scans run)."""
    return {"JDX_FileContents_FD": fd, "JDX_FileContents_TD": td, "JDX_Filename": filename,
            "NumberOfScansRun": scans_run, "OriginalReceipt": receipt(stamp),
            "PeakList": [4.986], "PeakThresholdValue": 4.7, "Peaks": {}, "ResultCode": code}


PREVIOUS_COMPLETE = status(0, 8, OLD, td=OLD_FID)


class ScriptedIflow:
    """Replays a scripted status sequence after START; items may be exceptions."""

    def __init__(self, after_start, *, before=PREVIOUS_COMPLETE, start_reply=None):
        self.before = before if isinstance(before, list) else [before]
        self.after = list(after_start)
        self.start_reply = receipt(NEW) if start_reply is None else start_reply
        self.starts = 0
        self.polls = 0
        self.one_d = self.run_settings = None

    def _next(self, items):
        item = items.pop(0) if len(items) > 1 else items[0]
        if isinstance(item, BaseException):
            raise item
        return deepcopy(item)

    def iflow_experiment_status(self):
        if not self.starts:
            return self._next(self.before)
        self.polls += 1
        return self._next(self.after)

    def iflow_1d_settings(self):
        return example_iflow_1d_settings()

    def iflow_experiment_settings(self):
        return example_iflow_experiment_settings()

    def set_iflow_1d_settings(self, settings):
        self.one_d = settings

    def run_iflow_experiment(self, settings):
        self.starts += 1
        self.run_settings = settings
        return deepcopy(self.start_reply)


def production_nmr(**changes):
    return replace(fixed.load_settings()[-1], **changes)


def acquire(client, path, *, retrieval_wait=20, **changes):
    settings = production_nmr(**changes)
    clock = fixed.SimulatedClock()
    result = nmr_io.acquire(settings, path, retrieval_wait, client=client, now=clock.now, sleep=clock.sleep)
    # Every path stays inside the documented bound and never repeats START.
    assert clock.now() <= settings.max_wait_seconds + retrieval_wait + 2 * settings.poll_seconds + 1
    assert client.starts <= 1
    return result


def assert_production_request(client, path):
    assert client.run_settings["NumberOfScans"] == 8
    assert client.run_settings["ReceiverGain"] == 12.0
    assert client.run_settings["SpectralCentreInPpm"] == 5.0
    assert client.run_settings["SpectralWidthInPpm"] == 20.0
    assert client.run_settings["ExportFilename"] == str(path)
    assert client.one_d["AutoGain"] is False and client.one_d["ExportFilename"] == str(path)


# -- realistic successful acquisition sequences ---------------------------------
def test_documented_in_progress_status_is_waited_through_and_raw_saved(tmp_path):
    """The vendor's ExperimentStatus example (ResultCode 2, 0 scans, empty data)."""
    path = tmp_path / "nmr_raw/stage_1_0001.dx"
    client = ScriptedIflow([status(2, 0, NEW), status(1, 4, NEW), status(0, 8, NEW, td=REAL_FID)])
    result = acquire(client, path)
    assert result["status"] == "NMR_SAVED", result
    assert saved(path) == REAL_FID and client.starts == 1
    assert_production_request(client, path)


def test_status_that_still_shows_previous_run_after_start_is_waited_out(tmp_path):
    path = tmp_path / "raw.dx"
    client = ScriptedIflow([PREVIOUS_COMPLETE, status(1, 2, NEW), status(0, 8, NEW, td=REAL_FID)])
    result = acquire(client, path)
    assert result["status"] == "NMR_SAVED", result
    assert saved(path) == REAL_FID


def test_transient_status_errors_and_empty_replies_do_not_discard_acquisition(tmp_path):
    path = tmp_path / "raw.dx"
    client = ScriptedIflow([status(1, 1, NEW), TimeoutError("read timed out"), None,
                            status(1, 6, NEW), status(0, 8, NEW, td=REAL_FID)])
    result = acquire(client, path)
    assert result["status"] == "NMR_SAVED", result
    assert saved(path) == REAL_FID


def test_start_reply_with_fewer_receipt_fields_still_matches_completion(tmp_path):
    path = tmp_path / "raw.dx"
    client = ScriptedIflow([status(1, 3, NEW), status(0, 8, NEW, td=REAL_FID)],
                           start_reply={"ExperimentNumber": 1, "ResultCode": 1})
    result = acquire(client, path)
    assert result["status"] == "NMR_SAVED", result


def test_raw_fid_under_the_fd_field_is_saved_but_a_spectrum_never_is(tmp_path):
    fid = ScriptedIflow([status(1, 3, NEW), status(0, 8, NEW, fd=REAL_FID)])
    result = acquire(fid, tmp_path / "fid.dx")
    assert result["status"] == "NMR_SAVED", result
    assert saved(tmp_path / "fid.dx") == REAL_FID
    spectrum = ScriptedIflow([status(1, 3, NEW), status(0, 8, NEW, fd=SPECTRUM)])
    result = acquire(spectrum, tmp_path / "fd.dx", retrieval_wait=4)
    assert result["status"] == "NMR_MISSING" and not (tmp_path / "fd.dx").exists()


def test_inline_raw_is_saved_byte_for_byte_with_instrument_line_endings(tmp_path):
    path = tmp_path / "raw.dx"
    client = ScriptedIflow([status(1, 3, NEW), status(0, 8, NEW, td=REAL_FID)])
    assert acquire(client, path)["status"] == "NMR_SAVED"
    assert path.read_bytes() == REAL_FID.encode("utf-8")


def test_late_inline_raw_after_idle_is_retrieved_without_second_start(tmp_path):
    path = tmp_path / "raw.dx"
    client = ScriptedIflow([status(1, 3, NEW), status(0, 8, NEW), status(0, 8, NEW),
                            status(0, 8, NEW, td=REAL_FID)])
    result = acquire(client, path)
    assert result["status"] == "NMR_SAVED" and client.starts == 1


# -- protections that must remain ---------------------------------------------
def test_stale_previous_spectrum_is_never_saved_as_this_observation(tmp_path):
    client = ScriptedIflow([PREVIOUS_COMPLETE])
    result = acquire(client, tmp_path / "raw.dx")
    assert result["status"] == "NMR_FAILED" and client.starts == 1
    assert not (tmp_path / "raw.dx").exists() and not (tmp_path / "raw.rpc.dx").exists()


def test_completion_of_a_different_start_receipt_is_never_saved(tmp_path):
    client = ScriptedIflow([status(1, 2, "someone else"), status(0, 8, "someone else", td=REAL_FID)],
                           start_reply=receipt(NEW))
    result = acquire(client, tmp_path / "raw.dx")
    assert result["status"] == "NMR_FAILED" and not (tmp_path / "raw.dx").exists()


@pytest.mark.parametrize("busy", [status(1, 3, OLD), {"Running": True}, {"InProgress": True, "ResultCode": 0},
                                  {"PercentComplete": 40, "ResultCode": 0}])
def test_positively_busy_instrument_never_receives_start(tmp_path, busy):
    client = ScriptedIflow([status(0, 8, NEW, td=REAL_FID)], before=busy)
    result = acquire(client, tmp_path / "raw.dx")
    assert result["status"] == "NMR_FAILED" and client.starts == 0 and result["requested"] is False


@pytest.mark.parametrize("unknown", [None, {}, status(2, 0, OLD), "Service unavailable"])
def test_unrecognized_pre_start_state_still_skips_start(tmp_path, unknown):
    client = ScriptedIflow([status(0, 8, NEW, td=REAL_FID)], before=unknown)
    result = acquire(client, tmp_path / "raw.dx")
    assert result["status"] == "NMR_FAILED" and client.starts == 0
    assert "START skipped" in result["error"]


def test_transient_pre_start_reply_is_rechecked_before_skipping(tmp_path):
    path = tmp_path / "raw.dx"
    client = ScriptedIflow([status(1, 3, NEW), status(0, 8, NEW, td=REAL_FID)],
                           before=[TimeoutError("first status read timed out"), PREVIOUS_COMPLETE])
    result = acquire(client, path)
    assert result["status"] == "NMR_SAVED", result


def test_new_acquisition_during_raw_retrieval_is_not_attributed(tmp_path):
    client = ScriptedIflow([status(1, 3, NEW), status(0, 8, NEW), status(1, 0, "next acquisition")])
    result = acquire(client, tmp_path / "raw.dx")
    assert result["status"] == "NMR_FAILED" and "changed during raw retrieval" in result["error"]
    assert not (tmp_path / "raw.dx").exists()


def test_acquisition_timeout_reports_last_status_and_stays_bounded(tmp_path):
    client = ScriptedIflow([status(1, 3, NEW)])
    result = acquire(client, tmp_path / "raw.dx", max_wait_seconds=30, poll_seconds=2)
    assert result["status"] == "NMR_FAILED" and "timeout" in result["error"].lower()
    assert result["final"]["ResultCode"] == 1 and "JDX_FileContents_TD" not in result["final"]


# -- the real child-process boundary with a realistic protocol sequence -------
CHILD = '''
import sys
from pathlib import Path
sys.path.insert(0, ROOT)
sys.path.insert(0, TESTS)
from chemyx_lab.workflows import si6_fixed_time_nmr as nmr
import test_si6_fixed_time_new_audit as audit
class Client(audit.ScriptedIflow):
    def __init__(self, rpc_config):
        assert rpc_config.host == "nmr.invalid" and rpc_config.port == 5000
        super().__init__([audit.status(2, 0, audit.NEW), audit.status(1, 4, audit.NEW),
                          audit.status(0, 8, audit.NEW, td=audit.REAL_FID)])
    def run_iflow_experiment(self, settings):
        assert settings["NumberOfScans"] == 8
        Path(DISPATCH).write_text(str(settings["NumberOfScans"]))
        return super().run_iflow_experiment(settings)
nmr.NmrRpcClient = Client
nmr.main()
'''


def test_real_worker_child_saves_documented_sequence_byte_for_byte(tmp_path, monkeypatch):
    helper = tmp_path / "child.py"
    dispatched = tmp_path / "dispatched.txt"
    source = CHILD
    for key, value in {"ROOT": str(config.REPO_ROOT), "TESTS": str(Path(__file__).parent),
                       "DISPATCH": str(dispatched)}.items():
        source = source.replace(key, repr(value))
    helper.write_text(source, encoding="utf-8")
    original_run = subprocess.run
    calls = []

    def child(command, **kwargs):
        assert command[1:3] == ["-m", nmr_io.__name__] and kwargs["timeout"] == 430
        calls.append(command)
        return original_run([command[0], str(helper), *command[-2:]], **kwargs)

    monkeypatch.setattr(nmr_io.subprocess, "run", child)
    path = tmp_path / "nmr_raw/stage_2_0003.dx"
    result = nmr_io.acquire_bounded(production_nmr(host="nmr.invalid"), path, 20)
    assert result["status"] == "NMR_SAVED", result
    assert saved(path) == REAL_FID
    assert dispatched.read_text() == "8" and len(calls) == 1
    request = json.loads(path.with_suffix(".request.json").read_text(encoding="utf-8"))
    assert request["settings"]["scans"] == 8 and isinstance(request["settings"]["save_dir"], str)
    assert json.loads(path.with_suffix(".result.json").read_text(encoding="utf-8"))["status"] == "NMR_SAVED"


# -- restart and recovery through the real --live startup branch --------------
from contextlib import contextmanager
from types import SimpleNamespace

import yaml

from arduino.python.errors import ProcessLockError
from arduino.scripts._common import controller_session as real_controller_session
from chemyx_lab.runtime_journal import OperationJournal
from chemyx_lab.workflows import chemyx_position as position


class NullLock:
    def __init__(self, *_):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


@pytest.fixture
def live(tmp_path, monkeypatch):
    """fixed.run(mock=False): live paths and state; only transports are fakes."""
    raw, cfg, settings, nmr_settings = fixed.load_settings()
    raw = deepcopy(raw)
    for stage in raw["stages"]:
        stage["hours"] = .0001
    cfg = deepcopy(cfg)
    cfg["needle"]["state_path"] = str(tmp_path / "needle_state.json")
    cfg["results"]["run_root_dir"] = str(tmp_path / "arduino_runs")
    settings = replace(settings, dose_ledger_dir=tmp_path / "doses")
    root = tmp_path / "chemyx_state"
    results = tmp_path / "results"
    monkeypatch.setattr(manual.position, "state_root", lambda _mock: root)
    pump_session = manual.pump_session
    monkeypatch.setattr(fixed, "controller_session", lambda c, mode, **kw: real_controller_session(c, "mock", **kw))
    monkeypatch.setattr(manual, "pump_session", lambda s, mock: pump_session(s, True))
    monkeypatch.setattr(fixed, "PortProcessLock", NullLock)
    ns = SimpleNamespace(raw=raw, cfg=cfg, settings=settings, nmr=nmr_settings, root=root, tmp=tmp_path,
                         results=results, runs=0, prompts=[], pump=settings.pump_identity(False))

    def save_needle(logical, valid=True, reason="operator_confirmed_home"):
        NeedleStateStore(cfg["needle"]["state_path"]).save(NeedleState(
            logical_position=logical, position_valid=valid, reason=reason,
            last_updated=datetime.now(timezone.utc).isoformat(),
            steps_per_unit=cfg["needle"]["steps_per_unit"], up_step_sign=cfg["needle"]["up_step_sign"]))

    def set_home(*channels):
        with position.PositionLedger(root, owner="operator set-home") as ledger:
            for ch in channels:
                ledger.set_home(settings.channels[ch].identity(ns.pump), receipt_id="operator", established_by="test")

    def forbidden_input(prompt):
        ns.prompts.append(prompt)
        pytest.fail(f"Automatic experiment requested confirmation: {prompt}")
    monkeypatch.setattr("builtins.input", forbidden_input)

    def run(*, acquisition=None, experiment_id=None):
        ns.runs += 1
        output = tmp_path / f"run_{ns.runs}"
        report = fixed.run(raw, cfg, settings, nmr_settings, mock=False, output=output,
                           clock=fixed.SimulatedClock(), experiment_id=experiment_id,
                           acquisition=acquisition or (lambda *_: {"status": "NMR_MISSING", "path": None}))
        journal = output / "operation_journal.jsonl"
        rows = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()] if journal.exists() else []
        report["motion_records"] = [r for r in rows if r.get("operation_type") in {"withdraw", "infuse"}
                                    or r["event_type"] == "needle_requested"]
        return report

    ns.save_needle, ns.set_home, ns.run = save_needle, set_home, run
    set_home(1, 2)
    save_needle(0)
    return ns


def old_production_dose(live, status):
    """A previous production experiment's canonical record and organized journal."""
    run_id = "20261015_SI6_REAL_TWO_STAGE_001"
    guard = DoseGuard(live.settings.dose_ledger_dir, "SI6_REAL_TWO_STAGE_001", {}, run_id)
    guard.reserve()
    run_dir = live.results / "experiments" / run_id
    (run_dir / "metadata").mkdir(parents=True)
    (run_dir / "metadata/experiment_info.json").write_text("{}", encoding="utf-8")
    journal = OperationJournal(run_dir / "logs/experiment_journal.jsonl", run_id)
    journal.append("experiment_reserved", **guard.data)
    if status != "RESERVED":
        guard.mark("DISPATCH_INTENT")
        journal.append("dose_dispatch_intent", dose_id=guard.data["dose_id"], experiment_id=guard.data["experiment_id"],
                       configuration_sha256=guard.data["configuration_sha256"], channel=2)
    if status == "CONFIRMED":
        guard.mark("CONFIRMED")
    return guard, journal.path, run_id


@pytest.mark.parametrize("status", ["RESERVED", "DISPATCH_INTENT", "CONFIRMED"])
def test_previous_dose_never_blocks_new_fixed_time_run_or_changes_old_evidence(live, status):
    guard, journal, run_id = old_production_dose(live, status)
    old_record, old_journal = guard.path.read_bytes(), journal.read_bytes()
    report = live.run()
    assert report["status"] == "COMPLETE" and report["dose_confirmed"]
    assert guard.path.read_bytes() == old_record and journal.read_bytes() == old_journal
    assert Path(report["dose_record"]).is_relative_to(Path(report["results"]))
    assert not live.prompts and not report["interactive_prompts"]


def test_unreadable_previous_dose_is_history_not_a_new_run_gate(live):
    live.settings.dose_ledger_dir.mkdir()
    (live.settings.dose_ledger_dir / "BROKEN.json").write_text('{"truncated', encoding="utf-8")
    assert live.run()["status"] == "COMPLETE"
    assert (live.settings.dose_ledger_dir / "BROKEN.json").read_text() == '{"truncated'


def test_stale_owner_record_and_old_failed_report_do_not_block_a_fresh_run(live):
    (live.root / "owner.json").write_text('{"owner": "crashed fixed-time run", "pid": 99999999}', encoding="utf-8")
    old = live.tmp / "results/fixed_time/live/SI6_FIXED_OLD"
    old.mkdir(parents=True)
    (old / "report.json").write_text('{"status": "FAILED", "error": "old NMR failure"}', encoding="utf-8")
    assert live.run()["status"] == "COMPLETE"


def move_record(live, channel, direction, volume, *, complete=True):
    with position.PositionLedger(live.root, owner="previous run") as ledger:
        token = ledger.begin_motion(channel, position.motion_fields(live.pump, 20.0, 20.0), direction, volume,
                                    operation_id=f"old-{channel}-{direction}", source="previous run", run_id="old")
        if complete:
            ledger.complete_motion(token)


def test_new_run_references_manually_restored_channel1_despite_old_displacement(live):
    move_record(live, 1, "withdraw", 5.0)
    assert live.run()["status"] == "COMPLETE"
    with position.PositionLedger(live.root, owner="inspect") as ledger:
        assert ledger.state(1)["current_displacement_nl"] == 0


def test_new_run_references_manually_reloaded_channel2_despite_previous_dose(live):
    move_record(live, 2, "infuse", 1.86)
    assert live.run()["status"] == "COMPLETE"
    with position.PositionLedger(live.root, owner="inspect") as ledger:
        assert ledger.state(2)["current_displacement_nl"] == -1_860_000  # one new dose, not two


def test_interrupted_previous_pump_move_needs_no_separate_set_home_on_new_run(live):
    move_record(live, 1, "infuse", 13.0, complete=False)  # process died mid-move
    assert live.run()["status"] == "COMPLETE"


def test_uncertain_needle_reference_is_preserved_instead_of_becoming_a_new_home(live):
    live.save_needle(1, valid=False, reason="motion_failed_or_interrupted")
    path = Path(live.cfg["needle"]["state_path"])
    old_state = path.read_bytes()
    report = live.run()
    assert report["status"] == "FAILED" and "Needle HOME is unconfirmed" in report["error"]
    assert not report["motion_records"] and path.read_bytes() == old_state


@pytest.mark.parametrize("previous", ["missing", "corrupt", "pending"])
def test_new_run_never_replaces_missing_corrupt_or_pending_arduino_home(live, previous):
    path = Path(live.cfg["needle"]["state_path"])
    pending = NeedleStateStore(path).home_pending_path
    if previous == "missing":
        path.unlink()
    elif previous == "corrupt":
        path.write_bytes(b'{"cut off')
    else:
        pending.write_bytes(b'old interrupted HOME')
    before = path.read_bytes() if path.exists() else None
    report = live.run()
    assert report["status"] == "FAILED" and not report["motion_records"]
    assert (path.read_bytes() if path.exists() else None) == before
    if previous == "pending":
        assert pending.read_bytes() == b'old interrupted HOME'


@pytest.mark.parametrize("position", [-2, 0, 1, 5])
def test_startup_physically_returns_to_saved_home_before_up_without_setting_new_zero(live, monkeypatch, position):
    live.save_needle(position)
    path = Path(live.cfg["needle"]["state_path"])
    original_save = NeedleStateStore.save
    def preserve_home(store, state):
        if store.path == path:
            assert state.reason != "operator_confirmed_home", "Experiment redefined the official Arduino HOME"
            assert state.home_position == 0 and state.steps_per_unit == 200 and state.up_step_sign == 1
        return original_save(store, state)
    monkeypatch.setattr(NeedleStateStore, "save", preserve_home)
    original_session = fixed.controller_session
    jogs = []
    @contextmanager
    def capture(*args, **kwargs):
        with original_session(*args, **kwargs) as controller:
            original_jog = controller.jog
            def jog(steps, speed):
                jogs.append(steps)
                return original_jog(steps, speed)
            monkeypatch.setattr(controller, "jog", jog)
            yield controller
    monkeypatch.setattr(fixed, "controller_session", capture)
    report = live.run()
    assert report["status"] == "COMPLETE"
    assert report["needle_home_policy"] == "return_to_saved_arduino_home"
    if position:
        assert jogs[:2] == [-position * 200, 200]
    else:
        assert jogs[0] == 200
    targets = [r["target"] for r in report["motion_records"] if r["event_type"] == "needle_requested"]
    assert targets[:2] == [0, 1]
    rows = [json.loads(line) for line in (Path(report["results"]) / "operation_journal.jsonl").read_text().splitlines()]
    requested = next(r for r in rows if r["event_type"] == "startup_needle_home_requested")
    completed = next(r for r in rows if r["event_type"] == "startup_needle_home_completed")
    assert requested["from_position"] == position and requested["target"] == 0
    assert requested["reference_basis"] == "saved_standalone_arduino_home"
    assert completed["movement_required"] == (position != 0) and not completed["reference_redefined"]


@pytest.mark.parametrize("previous", ["missing", "corrupt"])
def test_pump_snapshot_recovers_existing_home_from_journal_without_redefining_it(live, previous):
    before = {ch: json.loads((live.root / f"channel_{ch}_state.json").read_text())["home"] for ch in (1, 2)}
    if previous == "missing":
        for ch in (1, 2):
            (live.root / f"channel_{ch}_state.json").unlink()
    elif previous == "corrupt":
        (live.root / "channel_1_state.json").write_text('{"cut off')
    assert live.run()["status"] == "COMPLETE"
    assert all(json.loads((live.root / f"channel_{ch}_state.json").read_text())["home"] == before[ch]
               for ch in (1, 2))


def test_missing_official_pump_home_is_never_invented(live, monkeypatch):
    empty = live.tmp / "no_official_pump_home"
    monkeypatch.setattr(position, "state_root", lambda _: empty)
    report = live.run()
    assert report["status"] == "FAILED" and "no established Chemyx HOME" in report["error"]
    assert not report["motion_records"] and not live.prompts
    assert not list(empty.glob("channel_*_state.json"))


def test_incompatible_official_pump_home_is_never_replaced(live):
    with position.PositionLedger(live.root, owner="previous port") as ledger:
        ledger.set_home(position.ChannelIdentity(1, "serial:COM99", 20, 20, "old", 0),
                        receipt_id="old", established_by="old")
    path = live.root / "channel_1_state.json"
    original = path.read_bytes()
    report = live.run()
    assert report["status"] == "FAILED" and "will not redefine" in report["error"]
    assert not report["motion_records"] and path.read_bytes() == original


def test_completed_runs_can_start_again_without_reconciliation_even_with_reused_label(live):
    reports = [live.run(experiment_id="SAME_OPERATOR_LABEL") for _ in range(2)]
    assert all(r["status"] == "COMPLETE" and r["dose_confirmed"] for r in reports)
    assert not live.prompts
    assert reports[0]["dose_record"] != reports[1]["dose_record"]
    assert all(json.loads(Path(r["dose_record"]).read_text())["status"] == "CONFIRMED" for r in reports)
    for report in reports:
        doses = [r for r in report["motion_records"] if r.get("channel") == 2
                 and r.get("operation_type") == "infuse" and r.get("lifecycle_state") == "completed"]
        assert len(doses) == 1


def test_stopped_phase1_restarts_without_software_recovery_and_preserves_old_record(live):
    def interrupt(*_):
        raise KeyboardInterrupt("operator stopped Phase 1")
    first = live.run(acquisition=interrupt)
    assert first["status"] == "INTERRUPTED"
    path = Path(first["dose_record"])
    old = path.read_bytes()
    assert json.loads(old)["status"] == "RESERVED"
    assert live.run()["status"] == "COMPLETE"
    assert path.read_bytes() == old and not live.prompts


def test_stopped_dose_does_not_retry_inside_run_but_new_manual_setup_can_start(live, monkeypatch):
    original = fixed.Rig.move
    attempts = []
    def interrupt(self, event):
        if event["channel"] == 2:
            attempts.append(event)
            raise KeyboardInterrupt("operator stopped dose")
        return original(self, event)
    monkeypatch.setattr(fixed.Rig, "move", interrupt)
    first = live.run()
    assert first["status"] == "INTERRUPTED" and len(attempts) == 1 and len(first["stages"]) == 1
    path = Path(first["dose_record"])
    old = path.read_bytes()
    assert json.loads(old)["status"] == "DISPATCH_INTENT"
    monkeypatch.setattr(fixed.Rig, "move", original)
    assert live.run()["status"] == "COMPLETE"
    assert path.read_bytes() == old


@pytest.mark.parametrize("nmr_outcome", ["saved", "failed"])
def test_full_live_path_preserves_both_pump_homes_and_never_prompts(live, monkeypatch, nmr_outcome):
    raw, *_ = fixed.load_settings()
    live.raw["stages"] = deepcopy(raw["stages"])
    live.save_needle(2)
    move_record(live, 1, "withdraw", 5, complete=False)
    move_record(live, 2, "infuse", 1.86)
    before = {ch: json.loads((live.root / f"channel_{ch}_state.json").read_text()) for ch in (1, 2)}
    def forbidden_home(*_, **__):
        pytest.fail("Live experiment attempted to redefine Chemyx HOME")
    monkeypatch.setattr(position.PositionLedger, "set_home", forbidden_home)
    def acquisition(*_):
        if nmr_outcome == "failed":
            raise ConnectionError("NMR disconnected")
        return {"status": "NMR_SAVED", "path": "fake acquisition"}
    report = live.run(acquisition=acquisition)
    assert report["status"] == "COMPLETE" and report["physical_cleanup"] and report["dose_confirmed"]
    assert [s["elapsed_seconds"] for s in report["stages"]] == [48 * 3600, 6 * 3600]
    assert len(report["observations"]) == 36 and not live.prompts
    for ch in (1, 2):
        after = json.loads((live.root / f"channel_{ch}_state.json").read_text())
        for key in ("home", "home_reference_ml", "configuration_identity", "configuration_sha256", "syringe"):
            assert after[key] == before[ch][key]
    with position.PositionLedger(live.root, owner="verify after completed run") as ledger:
        assert not ledger.notices
        assert all(ledger.state(ch)["reference_valid"] for ch in (1, 2))
    rows = [json.loads(line) for line in (live.root / "operations.jsonl").read_text().splitlines()]
    returns = [r for r in rows if r["event_type"] == "chemyx_position_manual_return_home"]
    assert len(returns) == 2 and all(not r["reference_redefined"] and not r["physical_motion_commanded"] for r in returns)
    doses = [r for r in report["motion_records"] if r.get("channel") == 2
             and r.get("operation_type") == "infuse" and r.get("lifecycle_state") == "completed"]
    assert len(doses) == 1


def test_active_pump_owner_still_prevents_concurrent_motion(live):
    with position.PositionLedger(live.root, owner="another active controller"):
        report = live.run()
    assert report["status"] == "FAILED" and "owned by another process" in report["error"]
    assert not report["motion_records"]


@pytest.mark.parametrize("problem", ["moving", "fault"])
def test_current_needle_fault_or_motion_is_not_hidden_by_fresh_start(live, monkeypatch, problem):
    original = fixed.controller_session
    @contextmanager
    def bad_controller(*args, **kwargs):
        with original(*args, **kwargs) as controller:
            original_status = controller.status
            def status():
                value = original_status()
                value["moving" if problem == "moving" else "fault"] = "true" if problem == "moving" else "MOTOR_TIMEOUT"
                return value
            monkeypatch.setattr(controller, "status", status)
            yield controller
    monkeypatch.setattr(fixed, "controller_session", bad_controller)
    report = live.run()
    assert report["status"] == "FAILED" and "moving or faulted at startup" in report["error"]
    assert not report["motion_records"]


def test_closing_a_transport_after_completion_is_a_warning_not_a_failed_run(live, monkeypatch):
    mock_session = manual.pump_session

    @contextmanager
    def close_fails(settings, mock):
        with mock_session(settings, mock) as pump:
            yield pump
        raise OSError("USB serial close failed")

    monkeypatch.setattr(manual, "pump_session", close_fails)
    report = live.run()
    assert report["status"] == "COMPLETE" and report["physical_cleanup"] and report["dose_confirmed"]
    assert "USB serial close failed" in report["teardown_warning"] and "error" not in report


def test_geometry_mismatch_names_both_files_and_values(tmp_path):
    manual_cfg = yaml.safe_load((config.REPO_ROOT / "configs/chemyx/manual_control.yaml").read_text(encoding="utf-8"))
    manual_cfg["machine_config"] = str(config.REPO_ROOT / manual_cfg["machine_config"])
    manual_cfg["syringe_config"] = str(config.REPO_ROOT / manual_cfg["syringe_config"])
    manual_cfg["channels"][2]["home"]["syringe_volume_at_home_ml"] = 2.0  # an older WORK convention
    manual_path = tmp_path / "manual_control.yaml"
    manual_path.write_text(yaml.safe_dump(manual_cfg), encoding="utf-8")
    raw = yaml.safe_load(fixed.DEFAULT_CONFIG.read_text(encoding="utf-8"))
    raw["manual_config"] = str(manual_path)
    preset = tmp_path / "preset.yaml"
    preset.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError) as error:
        fixed.load_settings(preset)
    text = str(error.value)
    assert "Channel 2" in text and "2.0 mL at HOME" in text and "5 mL loaded" in text


# -- NMR-only real-instrument check -------------------------------------------
def test_nmr_check_uses_only_the_fixed_worker(tmp_path, monkeypatch):
    raw, _, _, nmr_settings = fixed.load_settings()
    calls = []

    def worker(settings, path, wait):
        calls.append((settings.scans, Path(path).name, wait))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(REAL_FID.encode("utf-8"))
        return {"status": "NMR_SAVED", "path": str(path), "requested": True, "before": {"ResultCode": 0}}

    def forbidden(*_, **__):
        pytest.fail("An NMR check must not open a pump, needle, position ledger or dose record")

    monkeypatch.setattr(fixed, "acquire_bounded", worker)
    monkeypatch.setattr(fixed, "PortProcessLock", NullLock)
    monkeypatch.setattr(fixed, "controller_session", forbidden)
    monkeypatch.setattr(fixed, "DoseGuard", forbidden)
    monkeypatch.setattr(manual, "pump_session", forbidden)
    monkeypatch.setattr(position, "PositionLedger", forbidden)
    assert fixed.nmr_check(raw, nmr_settings, output=tmp_path / "check", input_fn=lambda _: "y") == 0
    assert calls == [(8, "nmr_check_0001.dx", 20)]
    assert json.loads((tmp_path / "check/nmr_check.json").read_text(encoding="utf-8"))["status"] == "NMR_SAVED"


def test_nmr_check_never_overlaps_an_experiment_and_declining_sends_nothing(tmp_path, monkeypatch):
    raw, _, _, nmr_settings = fixed.load_settings()

    class Owned(NullLock):
        def __enter__(self):
            raise ProcessLockError("fixed-time experiment owns the NMR")

    monkeypatch.setattr(fixed, "PortProcessLock", Owned)
    monkeypatch.setattr(fixed, "acquire_bounded", lambda *_: pytest.fail("no NMR command while owned"))
    assert fixed.nmr_check(raw, nmr_settings, output=tmp_path / "owned", input_fn=lambda _: "y") == 1
    result = json.loads((tmp_path / "owned/nmr_check.json").read_text(encoding="utf-8"))
    assert result["status"] == "NMR_FAILED" and result["requested"] is False
    assert fixed.nmr_check(raw, nmr_settings, output=tmp_path / "declined", input_fn=lambda _: "n") == 1
    assert not (tmp_path / "declined").exists()
    seen = []
    monkeypatch.setattr(fixed, "nmr_check", lambda raw, nmr, output=None: seen.append((nmr.scans, output)) or 0)
    assert fixed.main(["--nmr-check", "--output-dir", str(tmp_path / "cli")]) == 0
    assert seen == [(8, tmp_path / "cli")]


# -- intermittent NMR outcomes with the real mock drivers ----------------------
def test_intermittent_nmr_outcomes_keep_schedule_cleanup_and_single_dose(tmp_path):
    raw, arduino, settings, nmr_settings = fixed.load_settings()
    raw = deepcopy(raw)
    raw["stages"][0].update(hours=1.0, interval_minutes=20)
    raw["stages"][1].update(hours=0.5, interval_minutes=10)
    outcomes = iter(["saved", "missing", "raise", "garbage", "failed", "saved"])
    output = tmp_path / "run"

    def acquisition(stage, observation):
        kind = next(outcomes)
        if kind == "saved":
            path = output / "nmr_raw" / f"{stage}_{observation:04d}.dx"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(REAL_FID.encode("utf-8"))
            return {"status": "NMR_SAVED", "path": str(path)}
        if kind == "raise":
            raise ConnectionError("NMR Ethernet dropped")
        if kind == "garbage":
            return "unexpected worker reply"
        return {"status": "NMR_MISSING" if kind == "missing" else "NMR_FAILED", "path": None, "error": kind}

    report = fixed.run(raw, arduino, settings, nmr_settings, mock=True, output=output, acquisition=acquisition)
    assert report["status"] == "COMPLETE" and report["physical_cleanup"] and report["dose_confirmed"]
    assert [s["elapsed_seconds"] for s in report["stages"]] == pytest.approx([3600.0, 1800.0])
    rows = report["observations"]
    assert [(r["stage"], r["scheduled_seconds"]) for r in rows] == [
        ("stage_1", 0.0), ("stage_1", 1200.0), ("stage_1", 2400.0),
        ("stage_2", 0.0), ("stage_2", 600.0), ("stage_2", 1200.0)]
    assert [r["status"] for r in rows] == ["NMR_SAVED", "NMR_MISSING", "NMR_FAILED", "NMR_FAILED", "NMR_FAILED", "NMR_SAVED"]
    assert all(r["physical_cleanup"] == "COMPLETE" for r in rows)
    journal = [json.loads(line) for line in (output / "operation_journal.jsonl").read_text(encoding="utf-8").splitlines()]
    doses = [r for r in journal if r.get("channel") == 2 and r.get("operation_type") == "infuse"]
    assert [r["lifecycle_state"] for r in doses] == ["planned", "dispatch_started", "completed"]
    assert doses[0]["requested_parameters"] == {"volume": 1.86, "rate": 1.0}
    assert not [r for r in journal if r.get("lifecycle_state") == "uncertain"]
    dose = json.loads((output / "channel2_dose.json").read_text(encoding="utf-8"))
    assert dose["status"] == "CONFIRMED" and abs(dose["retained_volume_ml"] - 3.14) < 1e-9
    ledger = {ch: json.loads((output / f"mock_state/chemyx_state/channel_{ch}_state.json").read_text(encoding="utf-8"))
              for ch in (1, 2)}
    assert ledger[1]["position_state"] == "KNOWN" and ledger[1]["current_displacement_nl"] == 0
    assert ledger[2]["position_state"] == "KNOWN" and ledger[2]["current_displacement_nl"] == -1_860_000
    needle = json.loads((output / "mock_state/needle_state.json").read_text(encoding="utf-8"))
    assert needle["logical_position"] == 1 and needle["position_valid"]


# -- manual CLIs: persistence across processes ----------------------------------
def needle_cli(cfg_path, *args, check=True):
    proc = subprocess.run([sys.executable, str(config.REPO_ROOT / "arduino/scripts/needle_control.py"), *args,
                           "--config", str(cfg_path), "--mock"], cwd=config.REPO_ROOT, text=True,
                          capture_output=True, timeout=60)
    if check:
        assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc


@pytest.fixture
def needle_cfg(tmp_path):
    cfg = yaml.safe_load((config.REPO_ROOT / "arduino/configs/arduino_real_COM3.yaml").read_text(encoding="utf-8"))
    cfg["results"]["run_root_dir"] = str(tmp_path)
    path = tmp_path / "arduino.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path, NeedleStateStore(tmp_path / "mock_needle_state.json")


def test_standalone_set_home_and_manual_moves_feed_the_same_experiment_home(live):
    cfg = yaml.safe_load((config.REPO_ROOT / "arduino/configs/arduino_real_COM3.yaml").read_text())
    manual_root = live.tmp / "standalone_arduino"
    cfg["results"]["run_root_dir"] = str(manual_root)
    path = live.tmp / "standalone_arduino.yaml"
    path.write_text(yaml.safe_dump(cfg))
    needle_cli(path, "set-home")
    needle_cli(path, "up")
    needle_cli(path, "up")
    store = NeedleStateStore(manual_root / "mock_needle_state.json")
    assert store.load().logical_position == 2
    history = {p: p.read_bytes() for p in manual_root.rglob("operation_journal.jsonl")}
    assert history  # the Arduino-only HOME action has durable evidence
    live.cfg["needle"]["state_path"] = str(store.path)
    report = live.run()
    assert report["status"] == "COMPLETE"
    rows = [json.loads(line) for line in (Path(report["results"]) / "operation_journal.jsonl").read_text().splitlines()]
    home = next(r for r in rows if r["event_type"] == "startup_needle_home_completed")
    assert home["from_position"] == 2 and home["target"] == 0 and home["movement_required"]
    assert not home["reference_redefined"]
    assert all(p.read_bytes() == data for p, data in history.items())


def test_manual_relative_count_persists_across_processes_then_set_home_feeds_automation(needle_cfg):
    cfg_path, store = needle_cfg
    counts = []
    for action in ("up", "up", "down", "down", "down"):
        out = needle_cli(cfg_path, action).stdout
        counts.append(store.load().logical_position)
        assert not store.load().position_valid and "calibrated HOME=False" in out
    assert counts == [1, 2, 1, 0, -1]
    assert "no motor movement" in needle_cli(cfg_path, "set-home").stdout
    state = store.load()
    assert (state.logical_position, state.position_valid, state.reason) == (0, True, "operator_confirmed_home")
    needle_cli(cfg_path, "up")  # a calibrated manual move shares the absolute record
    assert store.load().logical_position == 1 and store.load().position_valid
    needle_cli(cfg_path, "home")  # absolute HOME/DOWN = 0 from the same record
    assert store.load().logical_position == 0 and store.load().reason == "motion_completed"


def test_uncertain_needle_record_recovers_with_documented_stop_then_set_home(needle_cfg):
    cfg_path, store = needle_cfg
    needle_cli(cfg_path, "up")
    store.save(replace(store.load(), position_valid=False, reason="motion_failed_or_interrupted"))
    refused = needle_cli(cfg_path, "up", check=False)
    assert refused.returncode != 0 and "set-home" in refused.stdout + refused.stderr
    needle_cli(cfg_path, "stop")
    needle_cli(cfg_path, "set-home")
    needle_cli(cfg_path, "up")
    assert store.load().logical_position == 1 and store.load().position_valid
