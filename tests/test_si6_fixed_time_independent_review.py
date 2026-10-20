"""Second-review fault injection, independently exercising the actual code."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.controller import NeedleController
from arduino.python.manual_jog import tracked_relative_jog
from arduino.python.needle_state import NeedleStateStore
from chemyx_lab.workflows import si6_fixed_time as fixed
from chemyx_lab.workflows import si6_fixed_time_nmr as nmr
from chemyx_lab.workflows import dose_guard, dose_recovery
from chemyx_lab.workflows import chemyx_manual as manual
from chemyx_lab.runtime_journal import OperationJournal
from chemyx_lab import runtime_journal
from arduino.python.errors import ProcessLockError
from arduino.python.needle_state import NeedleState
from datetime import datetime, timezone


RAW = "##TITLE=independent raw fixture\n##JCAMP-DX=5.0\n##DATA TYPE=NMR FID\n##END="


def idle(receipt="new", **extra):
    return {"ResultCode": 0, "OriginalReceipt": {"TimeStamp": receipt}, **extra}


class Client:
    def __init__(self, statuses, *, on_start=None, receipt=None):
        self.statuses, self.on_start, self.receipt = iter(statuses), on_start, receipt
        self.started = False
        self.starts = 0

    def iflow_experiment_status(self):
        return next(self.statuses) if self.started else idle("old")

    def iflow_1d_settings(self):
        return {}

    def iflow_experiment_settings(self):
        return {}

    def set_iflow_1d_settings(self, _):
        pass

    def run_iflow_experiment(self, _):
        self.started = True
        self.starts += 1
        if self.on_start:
            self.on_start()
        return self.receipt


def acquire(client, path, wait=2):
    settings = replace(fixed.load_settings()[-1], max_wait_seconds=3, poll_seconds=1)
    clock = fixed.SimulatedClock()
    result = nmr.acquire(settings, path, wait, client=client, now=clock.now, sleep=clock.sleep)
    assert clock.now() <= 5
    assert client.starts <= 1
    return result


@pytest.mark.parametrize("status", [
    {"Running": False, "InProgress": True},
    {"Running": False, "ResultCode": 1},
    {"Running": False, "PercentComplete": 20},
    {"Running": False, "ResultCode": 2},
])
def test_contradictory_or_error_status_never_proves_idle(tmp_path, status):
    client = Client([])
    client.iflow_experiment_status = lambda: status
    assert acquire(client, tmp_path / "raw.dx")["status"] == "NMR_FAILED"
    assert client.starts == 0


def test_existing_observation_is_preserved_and_not_reacquired(tmp_path):
    path = tmp_path / "raw.dx"
    path.write_text(RAW)
    client = Client([])
    assert acquire(client, path)["status"] == "NMR_FAILED"
    assert client.starts == 0 and path.read_text() == RAW


def test_direct_server_export_and_inline_data_do_not_conflict(tmp_path):
    path = tmp_path / "raw.dx"
    client = Client([idle(JDX_FileContents_TD=RAW)], on_start=lambda: path.write_text(RAW))
    result = acquire(client, path)
    assert result["status"] == "NMR_SAVED" and Path(result["path"]).read_text() == RAW


def test_partial_server_export_is_preserved_and_inline_raw_saved_beside_it(tmp_path):
    path = tmp_path / "raw.dx"
    client = Client([idle(JDX_FileContents_TD=RAW)], on_start=lambda: path.write_text("partial export"))
    result = acquire(client, path)
    assert result["status"] == "NMR_SAVED"
    assert Path(result["path"]) == path.with_suffix(".rpc.dx")
    assert path.read_text() == "partial export"


def test_local_frequency_domain_export_is_never_reported_as_raw_fid(tmp_path):
    path = tmp_path / "raw.dx"
    frequency_domain = RAW.replace("NMR FID", "NMR SPECTRUM")
    client = Client([idle()], on_start=lambda: path.write_text(frequency_domain))
    result = acquire(client, path, wait=0)
    assert result["status"] == "NMR_MISSING" and path.read_text() == frequency_domain
    assert "not identifiable as raw NMR FID" in result["local_export_issue"]


@pytest.mark.parametrize("changed", [idle("another", JDX_FileContents_TD=RAW), {"Running": True}])
def test_late_retrieval_cannot_attribute_another_acquisition(tmp_path, changed):
    client = Client([idle(), changed])
    result = acquire(client, tmp_path / "raw.dx")
    assert result["status"] == "NMR_FAILED" and "changed during raw retrieval" in result["error"]
    assert not (tmp_path / "raw.dx").exists()


def test_running_transition_does_not_validate_old_raw_receipt(tmp_path):
    client = Client([{"Running": True}, idle("old", JDX_FileContents_TD=RAW)])
    result = acquire(client, tmp_path / "raw.dx")
    assert result["status"] == "NMR_FAILED" and "previous acquisition" in result["error"]


def test_returned_start_receipt_must_match_completion(tmp_path):
    client = Client([idle("other", JDX_FileContents_TD=RAW)], receipt={"TimeStamp": "requested"})
    assert acquire(client, tmp_path / "raw.dx")["status"] == "NMR_FAILED"


@pytest.mark.parametrize("payload", [[], {"status": "NMR_SAVED", "path": "missing.dx"}])
def test_worker_malformed_or_false_saved_result_is_a_failed_observation(tmp_path, monkeypatch, payload):
    calls = []
    def worker(command, **_):
        calls.append(command)
        Path(command[-1]).write_text(json.dumps(payload))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(nmr.subprocess, "run", worker)
    result = nmr.acquire_bounded(fixed.load_settings()[-1], tmp_path / "raw.dx", 2)
    assert result["status"] == "NMR_FAILED" and len(calls) == 1


@pytest.mark.parametrize("direct_export", [False, True])
def test_real_worker_subprocess_round_trip_uses_only_fake_client(tmp_path, monkeypatch, direct_export):
    """Real request/child/main/result boundary; RPC transport alone is fake."""
    path = tmp_path / "raw.dx"
    helper = tmp_path / "mock_worker.py"
    dispatched = tmp_path / "dispatch.json"
    source = '''
import sys, json
from pathlib import Path
sys.path.insert(0, __ROOT__)
from chemyx_lab.workflows import si6_fixed_time_nmr as nmr
RAW = __RAW__
DIRECT_EXPORT = __DIRECT__
DISPATCHED = Path(__DISPATCH__)
class Client:
    def __init__(self, config):
        assert config.host == 'mock.invalid'
        self.started = False
    def iflow_experiment_status(self):
        return {'ResultCode': 0, 'OriginalReceipt': {'TimeStamp': 'new' if self.started else 'old'},
                'JDX_FileContents_TD': RAW if self.started and not DIRECT_EXPORT else ''}
    def iflow_1d_settings(self):
        return {}
    def iflow_experiment_settings(self):
        return {}
    def set_iflow_1d_settings(self, settings):
        pass
    def run_iflow_experiment(self, settings):
        assert not self.started
        self.started = True
        DISPATCHED.write_text(json.dumps(settings))
        if DIRECT_EXPORT:
            Path(settings['ExportFilename']).write_text(RAW)
        return {'TimeStamp': 'new'}
nmr.NmrRpcClient = Client
original_acquire = nmr.acquire
def checked_acquire(settings, *args, **kwargs):
    assert isinstance(settings.save_dir, Path)
    assert settings.scans == 8
    return original_acquire(settings, *args, **kwargs)
nmr.acquire = checked_acquire
nmr.main()
'''
    for key, value in {"__ROOT__": str(fixed.config.REPO_ROOT), "__RAW__": RAW,
                       "__DIRECT__": direct_export, "__DISPATCH__": str(dispatched)}.items():
        source = source.replace(key, repr(value))
    helper.write_text(source)
    original_run = subprocess.run
    calls = []
    def isolated_worker(command, **kwargs):
        assert command[1:3] == ["-m", nmr.__name__]
        calls.append(command)
        return original_run([command[0], str(helper), *command[-2:]], **kwargs)
    monkeypatch.setattr(nmr.subprocess, "run", isolated_worker)
    settings = replace(fixed.load_settings()[-1], host="mock.invalid", timeout=2,
                       max_wait_seconds=3, poll_seconds=.05, save_dir=tmp_path / "unused_save_dir")
    result = nmr.acquire_bounded(settings, path, 1)
    assert result["status"] == "NMR_SAVED", result
    assert result["requested"] and path.read_text() == RAW
    assert len(calls) == 1 and json.loads(dispatched.read_text())["NumberOfScans"] == 8
    request = json.loads(path.with_suffix(".request.json").read_text())
    assert request["settings"]["save_dir"] == str(settings.save_dir)
    assert json.loads(path.with_suffix(".result.json").read_text())["status"] == "NMR_SAVED"


def test_actual_hung_child_is_killed_and_failure_returns_within_bound(tmp_path, monkeypatch):
    original_run = subprocess.run
    calls = []
    def hung_worker(command, **kwargs):
        calls.append(command)
        kwargs["timeout"] = .3
        return original_run([command[0], "-c", "import time; time.sleep(60)"], **kwargs)
    monkeypatch.setattr(nmr.subprocess, "run", hung_worker)
    started = time.monotonic()
    result = nmr.acquire_bounded(fixed.load_settings()[-1], tmp_path / "raw.dx", 1)
    assert result["status"] == "NMR_FAILED" and "TimeoutExpired" in result["error"]
    assert len(calls) == 1 and time.monotonic() - started < 5


@pytest.mark.parametrize("journal_kind", ["absent", "empty"])
def test_reserved_before_first_journal_event_has_nonmoving_recovery(tmp_path, journal_kind):
    guard = dose_guard.DoseGuard(tmp_path / "doses", "OLD", {}, "old-run")
    guard.reserve()
    journal = tmp_path / "operation_journal.jsonl"
    if journal_kind == "empty":
        journal.touch()
    dose_recovery.reconcile(guard.path.parent, "OLD", journal, "unused")
    data = json.loads(guard.path.read_text())
    assert data["status"] == "RESERVED" and data["reconciliation"]["resolution"] == "unused"
    assert manual.dose_records(guard.path.parent)[0]["reconciled"]


def test_corrupt_journal_cannot_assert_unused_but_retirement_is_available(tmp_path):
    guard = dose_guard.DoseGuard(tmp_path / "doses", "OLD", {}, "old-run")
    guard.reserve()
    journal = tmp_path / "journal.jsonl"
    journal.write_text('{"interrupted"')
    with pytest.raises(ValueError, match="unused is refused"):
        dose_recovery.reconcile(guard.path.parent, "OLD", journal, "unused")
    dose_recovery.reconcile(guard.path.parent, "OLD", journal, "retired")
    assert journal.read_text() == '{"interrupted"'
    assert manual.dose_records(guard.path.parent)[0]["reconciled"]


@pytest.mark.parametrize("original", [b'{"broken":', b'[]', b'{"status":"CONFIRMED"}'])
def test_unreadable_dose_can_only_be_retired_and_exact_original_is_preserved(tmp_path, original):
    path = tmp_path / "OLD.json"
    path.write_bytes(original)
    records = manual.dose_records(tmp_path)
    assert records[0]["status"] == "UNREADABLE" and not records[0]["reconciled"]
    assert "uncertain" in manual.channel2_refusal("infuse", records, True)
    with pytest.raises(ValueError, match="unused is refused"):
        dose_recovery.reconcile(tmp_path, "OLD", None, "unused")
    assert path.read_bytes() == original
    dose_recovery.reconcile(tmp_path, "OLD", None, "retired")
    data = json.loads(path.read_text())
    assert data["status"] == "UNREADABLE"
    assert Path(data["reconciliation"]["original_record_backup"]).read_bytes() == original
    assert manual.dose_records(tmp_path)[0]["reconciled"]


def test_dose_persistence_failure_never_reports_an_unwritten_confirmation(tmp_path, monkeypatch):
    guard = dose_guard.DoseGuard(tmp_path, "OLD", {}, "old-run")
    guard.reserve()
    guard.mark("DISPATCH_INTENT")
    def fail(*_):
        raise OSError("dose volume evidence disk unavailable")
    monkeypatch.setattr(dose_guard, "write_json_atomic", fail)
    with pytest.raises(OSError):
        guard.mark("CONFIRMED")
    assert guard.data["status"] == json.loads(guard.path.read_text())["status"] == "DISPATCH_INTENT"


def short_run(tmp_path, **kwargs):
    raw, arduino, settings, nmr_settings = fixed.load_settings()
    raw = deepcopy(raw)
    for stage in raw["stages"]:
        stage["hours"] = .0001
    return fixed.run(raw, arduino, settings, nmr_settings, mock=True, output=tmp_path / "run", **kwargs)


@pytest.mark.parametrize("name", ["configuration.json", "instrument_commands.log"])
def test_optional_output_failure_cannot_cancel_physical_schedule(tmp_path, monkeypatch, name):
    original = Path.open
    def deny(path, *args, **kwargs):
        if path.name == name:
            raise PermissionError("injected optional export unavailable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", deny)
    report = short_run(tmp_path, acquisition=lambda *_: {"status": "NMR_MISSING"})
    assert report["status"] == "COMPLETE" and report["physical_cleanup"] and report["dose_confirmed"]


def test_missing_uncreatable_nmr_folder_only_fails_observations(tmp_path, monkeypatch):
    original = Path.mkdir
    def deny(path, *args, **kwargs):
        if path.name == "nmr_raw":
            raise PermissionError("NMR export folder unavailable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "mkdir", deny)
    report = short_run(tmp_path)
    assert report["status"] == "COMPLETE" and report["physical_cleanup"]
    assert all(row["status"] == "NMR_FAILED" for row in report["observations"])


def test_optional_stage_events_and_observation_export_do_not_shorten_clock():
    clock = fixed.SimulatedClock()
    starts = []
    def fail(*_, **__):
        raise OSError("optional stage/export unavailable")
    def sample(_):
        starts.append(clock.now())
        clock.sleep(1)
        return {"status": "NMR_FAILED"}
    rig = SimpleNamespace(clock=clock, recorder=SimpleNamespace(record=fail), sample=sample)
    result = fixed.run_stage({"name": "stage_1", "hours": 10 / 3600, "interval_minutes": 5 / 60},
                             rig, lambda *_: None, fail)
    assert result["elapsed_seconds"] == 10 and starts == [0, 5]


def test_stop_attempts_are_independent_and_do_not_mask_interruption(monkeypatch):
    attempted = []
    def pump_stop(_, state):
        attempted.append(state.channel)
        if state.channel == 1:
            raise OSError("first STOP transport failed")
    def needle_stop():
        attempted.append("needle")
        raise OSError("needle STOP state persistence failed")
    monkeypatch.setattr(fixed.base, "attempt_emergency_stop", pump_stop)
    rig = fixed.Rig.__new__(fixed.Rig)
    rig.pump, rig.needle = object(), SimpleNamespace(stop_best_effort=needle_stop)
    rig.stop()
    assert attempted == [1, 2, "needle"]


def test_interrupted_nmr_user_abort_stays_interrupt_and_preserves_records(tmp_path):
    def interrupt(*_):
        raise KeyboardInterrupt("operator stopped run")
    report = short_run(tmp_path, acquisition=interrupt)
    assert report["status"] == "INTERRUPTED" and not report["physical_cleanup"]
    assert len(report["stages"]) == 0 and not report["dose_confirmed"]
    assert (tmp_path / "run/operation_journal.jsonl").is_file()
    assert json.loads(Path(report["dose_record"]).read_text())["status"] == "RESERVED"


def test_unhomed_manual_done_with_wrong_final_counter_remains_pending(tmp_path, monkeypatch):
    _, cfg, *_ = fixed.load_settings()
    fake = FakeArduinoTransport(runtime_configurable=True)
    store = NeedleStateStore(tmp_path / "needle_state.json")
    with NeedleController(fake, expected_version="1.2.1", allow_motion=True) as controller:
        controller.configure_runtime(cfg)
        real_status = controller.status
        calls = []
        def wrong_after_move():
            calls.append(1)
            result = real_status()
            if len(calls) > 1:
                result["commanded_position_steps"] = "999999"
            return result
        monkeypatch.setattr(controller, "status", wrong_after_move)
        with pytest.raises(Exception, match="DONE/idle verification failed"):
            tracked_relative_jog(controller, cfg, "up", state_path=store.path)
    assert not store.load().position_valid and store.load().reason == "manual_relative_pending"


def test_new_entry_point_loads_without_numerical_processing_dependencies():
    script = '''
import builtins
original = builtins.__import__
def no_numerics(name, *args, **kwargs):
    if name.split('.')[0] in {'numpy', 'scipy', 'matplotlib', 'nmrglue'}:
        raise ImportError('Numerical NMR processing deliberately unavailable: ' + name)
    return original(name, *args, **kwargs)
builtins.__import__ = no_numerics
from chemyx_lab.workflows.si6_fixed_time import load_settings
assert load_settings()[-1].scans == 8
'''
    result = subprocess.run([sys.executable, "-c", script], cwd=fixed.config.REPO_ROOT,
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_numeric_yaml_strings_are_normalized_before_runtime(tmp_path):
    raw = deepcopy(fixed.config.read_mapping_config(fixed.DEFAULT_CONFIG, "test"))
    for stage in raw["stages"]:
        stage["hours"] = str(stage["hours"])
        stage["interval_minutes"] = str(stage["interval_minutes"])
    raw["workflow"]["pump_extra_seconds"] = "2"
    for event in raw["workflow"]["cycle"] + [raw["workflow"]["dose"]]:
        for key in ("volume_ml", "rate_ml_min", "seconds"):
            if key in event:
                event[key] = str(event[key])
    raw["retrieval_wait_seconds"] = "20"
    for recipe in raw["pump"]["channels"].values():
        for key in recipe.keys() - {"units"}:
            recipe[key] = str(recipe[key])
    path = tmp_path / "numeric_config.json"
    path.write_text(json.dumps(raw))
    loaded = fixed.load_settings(path)[0]
    assert loaded["stages"][0]["hours"] == 48.0
    assert loaded["workflow"]["dose"]["volume_ml"] == 1.86
    assert all(isinstance(event[key], float) for event in loaded["workflow"]["cycle"]
               for key in ("volume_ml", "rate_ml_min", "seconds") if key in event)


def test_mock_reconciliation_default_cannot_mutate_live_canonical_directory(tmp_path, monkeypatch):
    mock_state = tmp_path / "mock" / "chemyx_state"
    monkeypatch.setattr(manual.position, "state_root", lambda mock: mock_state if mock else tmp_path / "live")
    called = []
    def handler(args, *_):
        called.append(args.dose_ledger_dir)
        return 0
    monkeypatch.setattr(manual, "cmd_reconcile_dose", handler)
    assert manual.main(["reconcile-dose", "--channel", "2", "--mock", "--experiment-id", "OLD", "--resolution", "retired"]) == 0
    assert called == [mock_state.parent / "si6_doses_mock"]


def test_nmr_result_is_preserved_when_later_physical_cleanup_fails(tmp_path, monkeypatch):
    original = fixed.Rig.needle_to
    def cleanup_fault(self, target):
        if target == 1 and self.clock.now() > 0:
            raise RuntimeError("physical needle cleanup fault")
        return original(self, target)
    monkeypatch.setattr(fixed.Rig, "needle_to", cleanup_fault)
    report = short_run(tmp_path, acquisition=lambda *_: {"status": "NMR_FAILED", "error": "NMR request timed out"})
    assert report["status"] == "FAILED" and not report["physical_cleanup"]
    records = [json.loads(line) for line in (tmp_path / "run/operation_journal.jsonl").read_text().splitlines()]
    evidence = [record for record in records if record["event_type"] == "nmr_observation"]
    assert len(evidence) == 1 and evidence[0]["error"] == "NMR request timed out"


@pytest.mark.parametrize("lock_error", [ProcessLockError("NMR owner active"), PermissionError("NMR lock folder unavailable")])
def test_nmr_lock_contention_uses_only_mock_transports_and_does_not_abort(tmp_path, monkeypatch, lock_error):
    """Exercise startup's live-mode branch with both transports replaced by fakes."""
    raw, cfg, settings, nmr_settings = fixed.load_settings()
    raw = deepcopy(raw)
    for stage in raw["stages"]:
        stage["hours"] = .0001
    cfg["needle"]["state_path"] = str(tmp_path / "needle.json")
    NeedleStateStore(cfg["needle"]["state_path"]).save(NeedleState(logical_position=0, position_valid=True,
        reason="operator_confirmed_home", last_updated=datetime.now(timezone.utc).isoformat(),
        steps_per_unit=cfg["needle"]["steps_per_unit"], up_step_sign=cfg["needle"]["up_step_sign"]))
    settings = replace(settings, dose_ledger_dir=tmp_path / "doses")
    root = tmp_path / "pump_state"
    with manual.position.PositionLedger(root, owner="mock fixture") as ledger:
        for channel in (1, 2):
            ledger.set_home(settings.channels[channel].identity(settings.pump_identity(False)),
                            receipt_id="mock", established_by="mock fixture only")
    monkeypatch.setattr(manual.position, "state_root", lambda _: root)
    controller_session = fixed.controller_session
    pump_session = manual.pump_session
    monkeypatch.setattr(fixed, "controller_session", lambda cfg, mode, **kw: controller_session(cfg, "mock", **kw))
    monkeypatch.setattr(manual, "pump_session", lambda settings, mock: pump_session(settings, True))
    class Locked:
        def __init__(self, _):
            pass
        def __enter__(self):
            raise lock_error
        def __exit__(self, *_):
            pass
    monkeypatch.setattr(fixed, "PortProcessLock", Locked)
    def forbidden(*_):
        pytest.fail("NMR ownership was not acquired, so no NMR command is allowed")
    report = fixed.run(raw, cfg, settings, nmr_settings, mock=False, output=tmp_path / "run",
        clock=fixed.SimulatedClock(), acquisition=forbidden)
    assert report["status"] == "COMPLETE" and report["dose_confirmed"] and report["physical_cleanup"]
    assert all(row["status"] == "NMR_FAILED" and row["requested"] is False for row in report["observations"])


def test_intentionally_new_runs_with_same_output_basename_have_distinct_identities(tmp_path):
    first = short_run(tmp_path / "first", acquisition=lambda *_: {"status": "NMR_MISSING"})
    second = short_run(tmp_path / "second", acquisition=lambda *_: {"status": "NMR_MISSING"})
    assert first["status"] == second["status"] == "COMPLETE"
    assert Path(first["results"]).name == Path(second["results"]).name
    assert first["experiment_id"] != second["experiment_id"]


@pytest.mark.parametrize("field,value", [("hours", 1e308), ("interval_minutes", 1e308)])
def test_finite_values_that_overflow_clock_seconds_are_rejected_before_motion(tmp_path, field, value):
    raw = deepcopy(fixed.config.read_mapping_config(fixed.DEFAULT_CONFIG, "test"))
    raw["stages"][0][field] = value
    path = tmp_path / "overflow.json"
    path.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="finite"):
        fixed.load_settings(path)


def test_rebuildable_snapshot_failure_does_not_override_durable_physical_evidence(tmp_path, monkeypatch):
    def unavailable(*_):
        raise PermissionError("run_state projection unavailable")
    monkeypatch.setattr(runtime_journal, "write_state_atomic", unavailable)
    report = short_run(tmp_path, acquisition=lambda *_: {"status": "NMR_MISSING"})
    assert report["status"] == "COMPLETE" and report["physical_cleanup"] and report["dose_confirmed"]
    assert not (tmp_path / "run/run_state.json").exists()
    assert fixed.replay_journal(tmp_path / "run/operation_journal.jsonl").valid


def test_required_motion_journal_write_failure_still_stops_before_movement(tmp_path, monkeypatch):
    original = OperationJournal._durable_write
    def unavailable(self, payload):
        if b'"physical_state_effect":true' in payload:
            raise PermissionError("required physical evidence unavailable")
        return original(self, payload)
    monkeypatch.setattr(OperationJournal, "_durable_write", unavailable)
    report = short_run(tmp_path, acquisition=lambda *_: {"status": "NMR_MISSING"})
    assert report["status"] == "FAILED" and not report["physical_cleanup"] and not report["dose_confirmed"]
    assert not report["stages"]


@pytest.mark.parametrize("target", [None, "unavailable-analysis-only-setting"])
def test_analysis_target_and_environment_are_irrelevant_to_acquisition_startup(tmp_path, monkeypatch, target):
    raw = deepcopy(fixed.config.read_mapping_config(fixed.DEFAULT_CONFIG, "test"))
    if target is not None:
        raw["nmr"]["target_ppm"] = target
    path = tmp_path / "acquisition.json"
    path.write_text(json.dumps(raw))
    monkeypatch.setenv("NMR_TARGET_PPM", "invalid unused analysis target")
    assert fixed.load_settings(path)[-1].scans == 8


@pytest.mark.parametrize("sticky_scans", ["2", "obsolete-invalid-checkout-setting"])
def test_fixed_preset_scan_count_wins_over_checkout_environment(monkeypatch, sticky_scans):
    monkeypatch.setenv("NMR_DEFAULT_SCANS", sticky_scans)
    assert fixed.load_settings()[-1].scans == 8
