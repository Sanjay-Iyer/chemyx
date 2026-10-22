"""Focused Script 24 (was 17) tests: existing mock Chemyx serial + fixture NMR, no hardware."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.instruments.nmr import NmrRpcError
from chemyx_lab.workflows import simple_rig
from chemyx_lab.workflows.stage2_legacy_nmr import MockLegacyClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("twoinstrument_test", ROOT / "scripts/24_stage2only.py")
workflow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(workflow)


DEFAULT = workflow.load_config()["chemyx"]  # Tests follow the YAML, so editing it needs no test change.
WITHDRAW, INFUSE, INITIAL = DEFAULT["withdraw_volume_ml"], DEFAULT["infuse_volume_ml"], DEFAULT["initial_volume_ml"]
NET = INFUSE - WITHDRAW


def transfer_seconds(action):
    return DEFAULT[f"{action}_volume_ml"] / DEFAULT[f"{action}_rate_ml_min"] * 60 + DEFAULT["extra_seconds"]


def settings(count=1):
    cfg = workflow.load_config()
    cfg["experiment"]["observation_count"] = count
    return cfg


def files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def volumes(transport):
    # Assert what the actual Pump driver sent to the existing fake serial port.
    return [float(command.split()[-1]) for command in transport.tx_log if "set volume " in command]


@pytest.fixture
def rigs():
    made, events = [], []

    def factory(*args, **kwargs):
        rig = workflow.TwoInstrumentRig(*args, **kwargs)
        pump, clock = rig.pump.pump, kwargs["clock"]
        transfer, scan, connect, disconnect, stop = (rig.pump.transfer, rig.nmr.scan,
                                                    pump.connect, pump.disconnect, pump.stop)

        def connect_recorded():
            result = connect()
            rig.transport = pump.ser
            return result

        def transfer_recorded(action, volume, rate):
            events.append((action, float(volume), float(rate), clock.now()))
            # Infusion must see the completed acquisition/save or its logged failure.
            if action == "infuse" and rig.nmr.fake.counter:
                rig.saved_before_return = sorted(p.name for p in rig.output_dir.glob("*.dx"))
            result = transfer(action, volume, rate)
            events.append((action + "_complete", clock.now()))
            return result

        def scan_recorded(path, scans):
            rig.output_dir = Path(path).parent
            events.append(("nmr", scans, clock.now()))
            result = scan(path, scans)
            events.append(("nmr_result", result["status"], clock.now()))
            return result

        def stop_recorded(**kwargs):
            assert pump.is_connected, "STOP attempted after port closed"
            events.append(("stop", clock.now()))
            return stop(**kwargs)

        def disconnect_recorded():
            events.append(("close", clock.now()))
            return disconnect()

        pump.connect, pump.disconnect, pump.stop = connect_recorded, disconnect_recorded, stop_recorded
        rig.pump.transfer, rig.nmr.scan = transfer_recorded, scan_recorded
        made.append(rig)
        return rig

    factory.made, factory.events = made, events
    return factory


def test_a_one_observation_exact_order_and_driver_settings(tmp_path, rigs):
    status, tally = workflow.run(settings(), mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "COMPLETE" and tally == dict(slots=1, saved=1, nmr_failed=0, missed=0)
    rig = rigs.made[0]
    operations = [event[0] for event in rigs.events if event[0] not in {"stop", "close"}]
    assert operations == ["withdraw", "withdraw_complete", "nmr", "nmr_result", "infuse", "infuse_complete"]
    assert volumes(rig.transport) == [-WITHDRAW, INFUSE]
    assert rig.saved_before_return == ["observation_001.dx"]
    assert files(tmp_path / "run") == ["log.txt", "observation_001.dx"]
    assert "##END" in (tmp_path / "run/observation_001.dx").read_text(encoding="utf-8")
    assert rig.pump.contents_ml == pytest.approx(INITIAL - NET)
    assert not rig.pump.pump.is_connected
    commands = rig.transport.tx_log
    assert any("set diameter 20" in command for command in commands)
    assert all(command.startswith("1 ") for command in commands)  # Only CH1.
    assert [float(command.split()[-1]) for command in commands if "set rate " in command] == [5.0, 5.0]
    assert rigs.events[-1][0] == "close"
    assert all("Cancel" not in path for _, path, _ in rig.nmr.fake.calls)  # Success RPC sequence unchanged.


@pytest.mark.parametrize("infuse", [5.1, 5.2, 5.5, 6.0])
def test_b_yaml_volumes_reach_pump_driver_independently(tmp_path, rigs, infuse):
    cfg = settings()
    cfg["chemyx"]["infuse_volume_ml"] = infuse
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    loaded = workflow.load_config(config_path)
    assert loaded["chemyx"]["withdraw_volume_ml"] == 5.0
    assert workflow.run(loaded, mock=True, output_dir=tmp_path / "run", rig_factory=rigs)[0] == "COMPLETE"
    assert volumes(rigs.made[0].transport) == [-5.0, infuse]


def test_b_withdraw_and_rates_are_also_read_from_yaml(tmp_path, rigs):
    cfg = settings()
    cfg["chemyx"].update(withdraw_volume_ml=4.0, infuse_volume_ml=4.2,
                         withdraw_rate_ml_min=2.5, infuse_rate_ml_min=4.0)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    assert workflow.run(workflow.load_config(config_path), mock=True, output_dir=tmp_path / "run",
                        rig_factory=rigs)[0] == "COMPLETE"
    assert volumes(rigs.made[0].transport) == [-4.0, 4.2]
    commands = rigs.made[0].transport.tx_log
    assert [float(command.split()[-1]) for command in commands if "set rate " in command] == [2.5, 4.0]


def test_c_full_six_hour_schedule(tmp_path, rigs):
    cfg = workflow.load_config()
    clock = workflow.SimulatedClock()
    assert workflow.observation_times(cfg) == [i * 900.0 for i in range(25)]
    status, tally = workflow.run(cfg, mock=True, output_dir=tmp_path / "run", clock=clock, rig_factory=rigs)
    assert status == "COMPLETE" and tally == dict(slots=25, saved=25, nmr_failed=0, missed=0)
    rig = rigs.made[0]
    assert volumes(rig.transport) == [-WITHDRAW, INFUSE] * 25
    assert [event[3] for event in rigs.events if event[0] == "withdraw"] == [i * 900.0 for i in range(25)]
    starts = [payload for _, path, payload in rig.nmr.fake.calls if path.endswith("RunExperiment")]
    assert len(starts) == 25 and {payload["NumberOfScans"] for payload in starts} == {8}
    assert files(tmp_path / "run") == ["log.txt"] + [f"observation_{i:03d}.dx" for i in range(1, 26)]
    assert rig.pump.contents_ml == pytest.approx(INITIAL - 25 * NET)
    n = cfg["nmr"]
    assert (n["acquisition_timeout_seconds"], n["poll_seconds"], n["http_timeout_seconds"]) == (300, 2, 10)
    text = (tmp_path / "run/log.txt").read_text(encoding="utf-8")
    assert f"{NET:+.3f} mL" in text and f"{25 * NET:+.3f} mL" in text


def test_d_fresh_process_arduino_unavailable_and_no_state_access(tmp_path):
    # Install the import/access guard BEFORE loading Script 24 or its helpers.
    code = r'''
import builtins, importlib.abc, pathlib, runpy, sys
class NoArduino(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "arduino" or fullname.startswith("arduino."):
            raise AssertionError("Arduino import attempted: " + fullname)
sys.meta_path.insert(0, NoArduino())
def audit(event, args):
    if event == "open":
        path = str(args[0]).replace("\\", "/").lower()
        if any(token in path for token in ("/runs/arduino/", "/runtime/", "needle_state", "simple_needle_position",
                                           "simple_needle_steps", "home_pending", "operation_journal", "dose_record")):
            raise AssertionError("State access attempted: " + path)
sys.addaudithook(audit)
sys.path.insert(0, str(pathlib.Path.cwd() / "scripts"))
builtins.input = lambda prompt: "y"
ns = runpy.run_path("scripts/24_stage2only.py")
assert ns["main"](["--mock", "--output-dir", sys.argv[1]]) == 0
assert not any(name == "arduino" or name.startswith("arduino.") for name in sys.modules)
'''
    proc = subprocess.run([sys.executable, "-c", code, str(tmp_path / "run")], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(list((tmp_path / "run").glob("*.dx"))) == 25
    assert files(tmp_path / "run") == ["log.txt"] + [f"observation_{i:03d}.dx" for i in range(1, 26)]


class FirstScanMissing(MockLegacyClient):
    def _request(self, method, path, payload=None, params=None):
        if path.endswith("CancelExperiment"):
            self.calls.append((method, path, payload))
            self.active = False
            return {"ResultCode": 0}
        reply = super()._request(method, path, payload, params)
        if path.endswith("ExperimentStatus") and self.counter == 1:
            return {"ResultCode": 0, "JDX_FileContents_TD": ""}
        return reply


def test_e_nmr_failure_returns_sample_and_continues_only_after_idle(tmp_path, rigs, monkeypatch):
    monkeypatch.setattr(simple_rig, "MockLegacyClient", FirstScanMissing)
    status, tally = workflow.run(settings(2), mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "COMPLETE" and tally == dict(slots=2, saved=1, nmr_failed=1, missed=0)
    rig = rigs.made[0]
    assert volumes(rig.transport) == [-WITHDRAW, INFUSE] * 2
    assert rig.nmr.fake.counter == 2 and not rig.nmr_pending
    assert files(tmp_path / "run") == ["log.txt", "observation_002.dx"]
    text = (tmp_path / "run/log.txt").read_text(encoding="utf-8")
    assert "FAILED" in text and "no raw FID" in text and "idle confirmed after failure" in text
    assert text.index("idle confirmed after failure") < text.index(f"Infuse {INFUSE:g} mL - COMPLETE") < text.index("Observation 2/2")


@pytest.mark.parametrize("unknown", [False, True])
def test_e_uncertain_nmr_stops_after_return_without_another_start(tmp_path, rigs, monkeypatch, unknown):
    class CannotConfirmIdle(FirstScanMissing):
        def _request(self, method, path, payload=None, params=None):
            reply = super()._request(method, path, payload, params)
            if path.endswith("ExperimentStatus") and self.counter == 1:
                return {} if unknown else {"ResultCode": 1}
            return reply

    monkeypatch.setattr(simple_rig, "MockLegacyClient", CannotConfirmIdle)
    # Simulate timeout without spending 300 seconds in the real driver's poll loop.
    def timed_out_scan(self, path, scans):
        self.fake.counter = 1
        return dict(status="NMR_FAILED", error="simulated acquisition timeout")

    monkeypatch.setattr(simple_rig.NmrScanner, "scan", timed_out_scan)
    if not unknown:
        def still_busy(self, **kwargs):
            raise NmrRpcError("Timed out waiting for NMR run to finish")
        monkeypatch.setattr(simple_rig._MockTracingClient, "wait_for_idle", still_busy)
    status, _ = workflow.run(settings(2), mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "STOPPED"
    assert volumes(rigs.made[0].transport) == [-WITHDRAW, INFUSE]
    assert len([event for event in rigs.events if event[0] == "nmr"]) == 1
    assert files(tmp_path / "run") == ["log.txt"]
    text = (tmp_path / "run/log.txt").read_text(encoding="utf-8")
    assert "NMR: idle NOT confirmed" in text and "sample returned, run stopped" in text


def test_e_nmr_exception_also_returns_completed_withdrawal(tmp_path, rigs, monkeypatch):
    def broken(self, path, scans):
        raise OSError("simulated NMR connection lost")
    monkeypatch.setattr(simple_rig.NmrScanner, "scan", broken)
    status, tally = workflow.run(settings(), mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "COMPLETE" and tally["nmr_failed"] == 1
    assert volumes(rigs.made[0].transport) == [-WITHDRAW, INFUSE]
    assert "simulated NMR connection lost" in (tmp_path / "run/log.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("failed_start", [1, 2])
def test_f_pump_failure_no_blind_infusion_and_next_run_can_start(tmp_path, rigs, monkeypatch, failed_start):
    original = Pump.start
    calls = 0

    def fail(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls == failed_start:
            raise TimeoutError("simulated Chemyx start timeout")
        return original(self, **kwargs)

    monkeypatch.setattr(Pump, "start", fail)
    status, _ = workflow.run(settings(2), mock=True, output_dir=tmp_path / "failed", rig_factory=rigs)
    assert status == "STOPPED"
    rig = rigs.made[0]
    assert volumes(rig.transport) == ([-WITHDRAW] if failed_start == 1 else [-WITHDRAW, INFUSE])
    assert len([e for e in rigs.events if e[0] == "nmr"]) == failed_start - 1
    assert files(tmp_path / "failed") == (["log.txt"] if failed_start == 1 else ["log.txt", "observation_001.dx"])
    text = (tmp_path / "failed/log.txt").read_text(encoding="utf-8")
    assert "Chemyx" in text and "simulated Chemyx start timeout" in text and "physical inspection" in text
    assert rigs.events[-1][0] == "close" and rigs.events[-2][0] == "stop"
    assert not rig.pump.pump.is_connected
    monkeypatch.setattr(Pump, "start", original)
    assert workflow.run(settings(), mock=True, output_dir=tmp_path / "fresh", rig_factory=rigs)[0] == "COMPLETE"
    assert files(tmp_path / "fresh") == ["log.txt", "observation_001.dx"]


def test_f_return_failure_after_nmr_failure_stops_current_run(tmp_path, rigs, monkeypatch):
    monkeypatch.setattr(simple_rig, "MockLegacyClient", FirstScanMissing)
    original = Pump.set_volume

    def fail_infusion(self, value, **kwargs):
        if value > 0:
            return "error: pump fault"
        return original(self, value, **kwargs)

    monkeypatch.setattr(Pump, "set_volume", fail_infusion)
    assert workflow.run(settings(2), mock=True, output_dir=tmp_path / "run", rig_factory=rigs)[0] == "STOPPED"
    assert rigs.made[0].nmr.fake.counter == 1
    assert files(tmp_path / "run") == ["log.txt"]
    text = (tmp_path / "run/log.txt").read_text(encoding="utf-8")
    assert "pump fault" in text and "physical inspection" in text


def test_g_prior_logs_and_error_files_are_never_read(tmp_path, rigs, monkeypatch):
    # These are historical records, deliberately unreadable to the workflow.
    old = tmp_path / "old"
    old.mkdir()
    for name in ("log.txt", "fault.json", "needle_state.json", "operation_journal.jsonl"):
        (old / name).write_text("old failure must not matter", encoding="utf-8")
    original = Path.open

    def guarded(self, *args, **kwargs):
        assert old not in self.parents, f"Historical file accessed: {self}"
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded)
    cfg = settings()
    cfg["output"]["root_dir"] = str(tmp_path)
    assert workflow.run(cfg, mock=True, rig_factory=rigs)[0] == "COMPLETE"
    assert workflow.run(cfg, mock=True, rig_factory=rigs)[0] == "COMPLETE"  # Same second is collision-safe.
    runs = sorted(p for p in tmp_path.iterdir() if p.name != "old")
    assert len(runs) == 2
    assert all(files(p) == ["log.txt", "observation_001.dx"] for p in runs)


def test_overrun_uses_script21_missed_slot_handling(tmp_path, rigs):
    cfg = settings(4)
    cfg["experiment"]["interval_minutes"] = 2
    # Each cycle (withdraw + 80 s mock NMR + infuse) takes over 200 simulated s, so slots at 120 and 360 are missed.
    status, tally = workflow.run(cfg, mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "COMPLETE" and tally == dict(slots=4, saved=2, nmr_failed=0, missed=2)
    assert [e[3] for e in rigs.events if e[0] == "withdraw"] == [0.0, 240.0]
    assert files(tmp_path / "run") == ["log.txt", "observation_001.dx", "observation_003.dx"]


def test_pump_volume_limits_are_preserved(tmp_path, rigs):
    cfg = settings(3)
    cfg["chemyx"].update(initial_volume_ml=1.0, infuse_volume_ml=6.0)
    status, _ = workflow.run(cfg, mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "STOPPED"
    # First cycle consumes the initial 1 mL; second cannot return 6 from 5 mL.
    assert volumes(rigs.made[0].transport) == [-5.0, 6.0, -5.0]
    text = (tmp_path / "run/log.txt").read_text(encoding="utf-8")
    assert "allowed 0 to 20 mL" in text and "physical inspection" in text
    assert "+3.000 mL" in text


def test_one_initial_confirmation_and_no_connections_if_declined(tmp_path, rigs):
    prompts = []

    def decline(prompt):
        prompts.append(prompt)
        return "n"

    cfg = settings()
    assert workflow.run(cfg, mock=True, output_dir=tmp_path / "no", rig_factory=rigs,
                        confirm=True, input_fn=decline)[0] == "DECLINED"
    assert not rigs.made and len(prompts) == 1
    prompts.clear()

    def accept(prompt):
        prompts.append(prompt)
        return "y"

    assert workflow.run(cfg, mock=True, output_dir=tmp_path / "yes", rig_factory=rigs,
                        confirm=True, input_fn=accept)[0] == "COMPLETE"
    assert len(prompts) == 1
    text = (tmp_path / "yes/log.txt").read_text(encoding="utf-8")
    for phrase in ("needle secured", "sample-return path", "syringe liquid/headroom", "flask capacity"):
        assert phrase in text


def test_existing_output_is_not_overwritten(tmp_path):
    existing = tmp_path / "run"
    existing.mkdir()
    (existing / "observation_001.dx").write_text("preserve me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        workflow.run(settings(), mock=True, output_dir=existing)
    assert (existing / "observation_001.dx").read_text(encoding="utf-8") == "preserve me"


@pytest.mark.parametrize("phase", ["withdraw", "nmr", "infuse"])
def test_ctrl_c_returns_only_a_completed_known_withdrawal(tmp_path, rigs, monkeypatch, phase):
    class InterruptedClock(workflow.SimulatedClock):
        interrupted = False

        def sleep(self, seconds):
            target = transfer_seconds(phase) if phase != "nmr" else -1
            if not self.interrupted and seconds == pytest.approx(target):
                self.interrupted = True
                super().sleep(seconds / 2)
                raise KeyboardInterrupt()
            super().sleep(seconds)

    if phase == "nmr":
        def interrupted_nmr(self, path, scans):
            raise KeyboardInterrupt()
        monkeypatch.setattr(simple_rig.NmrScanner, "scan", interrupted_nmr)
    status, _ = workflow.run(settings(2), mock=True, output_dir=tmp_path / "run",
                             clock=InterruptedClock(), rig_factory=rigs)
    assert status == "INTERRUPTED"
    rig = rigs.made[0]
    assert volumes(rig.transport) == ([-WITHDRAW] if phase == "withdraw" else [-WITHDRAW, INFUSE])
    assert not rig.pump.pump.is_connected and rigs.events[-1][0] == "close"
    text = (tmp_path / "run/log.txt").read_text(encoding="utf-8")
    if phase == "nmr":
        assert f"Infuse {INFUSE:g} mL - COMPLETE" in text and not rig.sample_pending
    else:
        assert "physical inspection" in text
        assert rig.pump_uncertain
