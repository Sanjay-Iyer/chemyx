"""Custom software HOME for the two Chemyx channels (mock instruments only).

HOME is a saved software reference, never a native Chemyx command. These tests
contact no serial port, NMR or Arduino: the driver runs against the mock serial
emulator and the ledger lives in a temporary directory.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.instruments import chemyx as chemyx_driver
from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.runtime_state import replay_journal
from chemyx_lab.testing.mock_serial import MockChemyxSerial
from chemyx_lab.workflows import chemyx_manual as manual
from chemyx_lab.workflows import chemyx_position as position
from chemyx_lab.workflows import si6_automated_nmr as base

ROOT = config.REPO_ROOT
FAST_SIM = ROOT / "config_templates/experiments/si6_two_stage_fast_sim.yaml"
ARDUINO = ROOT / "arduino/configs/arduino.example.yaml"
ML = position.NL_PER_ML


class FaultySerial(MockChemyxSerial):
    """Mock Chemyx port that can fail in one way on one channel."""

    fault: str | None = None
    fault_channel = "1"

    def _handle(self, line):
        words = line.split()
        channel = words[0] if words and words[0] in {"1", "2"} else "0"
        command = " ".join(words[1:] if channel != "0" else words)
        armed = self.fault if channel == self.fault_channel else None
        if armed == "echo" and command.startswith("set volume"):
            return self._respond("volume = 999")
        if armed == "timeout" and command.startswith("start"):
            raise chemyx_driver.serial.SerialException("device timeout")
        if command == "stop" and self.channel_states.get(int(channel), {}).get("running"):
            if armed == "lost_ack":
                self.channel_states[int(channel)]["running"] = False
                return None
            if armed == "disconnect":
                raise chemyx_driver.serial.SerialException("USB serial device disconnected")
        return super()._handle(line)


def write_config(rig, *, ch1=5.0, ch2=2.0, diameter1=20.0, syringe_config=None):
    syringes = ({1: {}, 2: {}} if syringe_config else
                {1: {"diameter_mm": diameter1, "capacity_ml": 20.0}, 2: {"diameter_mm": 12.06, "capacity_ml": 5.0}})
    data = {
        "machine_config": str(rig.machine),
        "dose_ledger_dir": str(rig.doses),
        "channels": {
            1: {"name": "sampling", "syringe": syringes[1],
                "home": {"enabled": True, "reference_description": "Plunger fully depressed",
                         "syringe_volume_at_home_ml": 0.0},
                "manual_transfer": {"volume_ml": ch1, "rate_ml_min": 5.0}},
            2: {"name": "reagent_dosing", "syringe": syringes[2],
                "home": {"enabled": True, "reference_description": "Loaded with 2.0 mL reagent",
                         "syringe_volume_at_home_ml": 2.0},
                "manual_transfer": {"volume_ml": ch2, "rate_ml_min": 1.0}},
        },
    }
    if syringe_config:
        data["syringe_config"] = str(syringe_config)
    rig.config.write_text(yaml.safe_dump(data), encoding="utf-8")


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """Isolated ledger, manual YAML and a mock-serial pump in place of COM ports."""
    monkeypatch.setenv(position.STATE_DIR_ENV, str(tmp_path / "state"))
    monkeypatch.setattr(manual, "sleep_with_progress", lambda *_: None)
    FaultySerial.fault, FaultySerial.fault_channel = None, "1"
    ports: list[FaultySerial] = []

    def make_pump(settings, *, mock):
        def factory(**kwargs):
            ports.append(FaultySerial(**kwargs))
            return ports[-1]
        return Pump(port=settings.port, channel=1, units=0, response_delay=0, serial_factory=factory)

    monkeypatch.setattr(manual, "make_pump", make_pump)
    machine = tmp_path / "machine.yaml"
    machine.write_text("chemyx:\n  serial_port: COMTEST\n", encoding="utf-8")
    ns = SimpleNamespace(tmp=tmp_path, ports=ports, machine=machine, config=tmp_path / "manual.yaml",
                         doses=tmp_path / "doses", root=tmp_path / "state" / "chemyx_state")
    write_config(ns)
    yield ns
    FaultySerial.fault = None


def cli(rig, *args, answer="y"):
    return manual.main([*args, "--config", str(rig.config)], input_fn=lambda _prompt: answer)


def saved(root: Path, ch: int) -> dict:
    return json.loads((root / f"channel_{ch}_state.json").read_text(encoding="utf-8"))


def sent(rig) -> list[str]:
    return [line for port in rig.ports for line in port.tx_log]


def test_set_home_saves_reference_without_motion(rig):
    assert cli(rig, "set-home", "--channel", "both", "--live", answer="n") == 1
    assert not (rig.root / "channel_1_state.json").exists()
    assert cli(rig, "set-home", "--channel", "both", "--live") == 0
    assert not [line for line in sent(rig) if "set" in line or "start" in line]
    for ch in (1, 2):
        state = saved(rig.root, ch)
        assert state["position_state"] == "KNOWN" and state["current_displacement_nl"] == 0
        assert state["home"]["operator_confirmed"] and not state["home"]["physical_position_measured"]
    assert position.inspect_ledger(rig.root)[2]["effective_state"] == "KNOWN"


@pytest.mark.parametrize("moves, expected", [
    ([("withdraw", 5.0)], ("infuse", 5.0)),
    ([("withdraw", 5.0), ("infuse", 2.0), ("withdraw", 1.0)], ("infuse", 4.0)),
    ([], None),
])
def test_home_returns_the_tracked_displacement(rig, capsys, moves, expected):
    assert cli(rig, "set-home", "--channel", "1", "--live") == 0
    for direction, volume in moves:
        write_config(rig, ch1=volume)
        assert cli(rig, direction, "--channel", "1", "--live") == 0
    net = sum(volume if direction == "withdraw" else -volume for direction, volume in moves)
    assert saved(rig.root, 1)["current_displacement_nl"] == round(net * ML)
    assert cli(rig, "status", "--channel", "1") == 0
    status = capsys.readouterr().out
    opened = len(rig.ports)
    assert cli(rig, "home", "--channel", "1", "--live") == 0
    out = capsys.readouterr().out
    if expected is None:
        assert "already at HOME" in out and len(rig.ports) == opened
    else:
        direction, volume = expected
        assert f"Return to HOME: {direction.upper()} {volume:g} mL" in status
        assert f"{direction.upper()} {volume:g} mL" in out
        # Driver convention: +volume infuses, -volume withdraws. No HOME command exists.
        signed = volume if direction == "infuse" else -volume
        assert rig.ports[-1].tx_log.count(f"1 set volume {signed}\r") == 1
        assert not any("home" in line.lower() for line in sent(rig))
    final = saved(rig.root, 1)
    assert final["position_state"] == "KNOWN" and final["current_displacement_nl"] == 0


def test_channels_are_independent_and_both_never_overreports(rig, capsys):
    displacement = lambda ch: saved(rig.root, ch)["current_displacement_nl"]  # noqa: E731
    assert cli(rig, "set-home", "--channel", "both", "--live") == 0
    assert cli(rig, "withdraw", "--channel", "1", "--live") == 0
    assert cli(rig, "withdraw", "--channel", "2", "--live") == 0
    assert (displacement(1), displacement(2)) == (5 * ML, 2 * ML)
    assert cli(rig, "home", "--channel", "1", "--live") == 0
    assert (displacement(1), displacement(2)) == (0, 2 * ML)
    # Returning Channel 2 pushes reagent out, so it needs an explicit per-command authorization.
    capsys.readouterr()
    assert cli(rig, "home", "--channel", "2", "--live") == 1
    assert "--allow-channel2-infusion" in capsys.readouterr().out and displacement(2) == 2 * ML
    assert cli(rig, "home", "--channel", "2", "--live", "--allow-channel2-infusion") == 0
    assert (displacement(1), displacement(2)) == (0, 0)

    # Both channels: each uses its own syringe and volume; a Channel 2 failure
    # leaves Channel 1's completed return in place and is never reported as HOME.
    assert cli(rig, "withdraw", "--channel", "both", "--live") == 0
    FaultySerial.fault, FaultySerial.fault_channel = "lost_ack", "2"
    capsys.readouterr()
    assert cli(rig, "home", "--channel", "both", "--live", "--allow-channel2-infusion") == 1
    out = capsys.readouterr().out
    log = rig.ports[-1].tx_log
    assert "1 set diameter 20.0\r" in log and "2 set diameter 12.06\r" in log
    assert "1 set volume 5.0\r" in log and "2 set volume 2.0\r" in log
    one, two = saved(rig.root, 1), saved(rig.root, 2)
    assert one["position_state"] == "KNOWN" and one["current_displacement_nl"] == 0
    assert two["position_state"] == "UNKNOWN" and "positively confirmed" in two["uncertainty_reason"]
    assert "Channel 1: completed" in out and "Channel 2: not completed" in out


def test_experiment_then_separate_home_process(tmp_path):
    """Set HOME -> actual three-instrument run -> separate standalone HOME process."""
    env = dict(os.environ, **{position.STATE_DIR_ENV: str(tmp_path / "state"), "PYTHONDONTWRITEBYTECODE": "1"})
    root = tmp_path / "state" / "chemyx_state_mock"
    recipe = yaml.safe_load(FAST_SIM.read_text(encoding="utf-8"))
    recipe["output"]["run_root_dir"] = str(tmp_path / "runs")
    recipe_path = tmp_path / "workflow.yaml"
    recipe_path.write_text(yaml.safe_dump(recipe), encoding="utf-8")
    rig = SimpleNamespace(machine=tmp_path / "no_machine.yaml", doses=tmp_path / "runs" / ".mock_dose_ledger",
                          config=tmp_path / "manual.yaml")
    write_config(rig, ch1=2.0, syringe_config=recipe_path)  # geometry inherited from the run's recipe

    def run(script, *args, control=True):
        command = [sys.executable, str(ROOT / "scripts" / script), *args]
        if control:
            command += ["--config", str(rig.config)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=900)
        return result.returncode, result.stdout + result.stderr

    # 1. The operator establishes both references (no motion), then Channel 1
    #    is withdrawn 2 mL so the run starts away from HOME.
    assert run("chemyx_control.py", "set-home", "--channel", "both", "--mock")[0] == 0
    assert run("chemyx_control.py", "withdraw", "--channel", "1", "--mock")[0] == 0

    # 2. The unchanged two-stage experiment, in its own process.
    code, out = run("02_si6_experiment.py", "--mock", "--workflow-config", str(recipe_path),
                    "--machine-config", str(rig.machine), "--arduino-config", str(ARDUINO), control=False)
    assert code == 0 and "Si6 EXPERIMENT: completed" in out, out[-4000:]
    [journal] = (tmp_path / "runs").rglob("experiment_journal.jsonl")
    moves = [r for r in replay_journal(journal).records
             if r.get("operation_type") in {"withdraw", "infuse"} and r.get("lifecycle_state") == "completed"]
    totals = {(ch, d): sum(round(r["requested_parameters"]["volume"] * ML) for r in moves
                           if r["channel"] == ch and r["operation_type"] == d)
              for ch in (1, 2) for d in ("withdraw", "infuse")}
    assert totals[(1, "withdraw")] > 0 and totals[(2, "infuse")] == 1.8 * ML and totals[(2, "withdraw")] == 0

    # 3. Every experiment transfer was counted exactly once in the shared ledger.
    one, two = saved(root, 1), saved(root, 2)
    assert one["position_state"] == two["position_state"] == "KNOWN"
    assert one["current_displacement_nl"] == 2 * ML and two["current_displacement_nl"] == -1.8 * ML
    assert one["tracked_since_home"] == {
        "withdrawn_nl": 2 * ML + totals[(1, "withdraw")], "infused_nl": totals[(1, "infuse")],
        "movements": 1 + sum(r["channel"] == 1 for r in moves)}
    assert two["tracked_since_home"]["movements"] == 1
    assert two["last_confirmed_movement"]["run_id"] == journal.parents[1].name

    # 4. A separate process calculates both returns.
    code, out = run("chemyx_control.py", "status", "--channel", "both", "--mock")
    assert code == 0
    assert "Return to HOME: INFUSE 2 mL" in out and "Return to HOME: WITHDRAW 1.8 mL" in out

    # 5. Channel 1 alone returns to HOME; Channel 2 is untouched.
    code, out = run("chemyx_control.py", "home", "--channel", "1", "--mock")
    assert code == 0 and "INFUSE 2 mL" in out, out
    assert saved(root, 1)["current_displacement_nl"] == 0
    assert saved(root, 2)["current_displacement_nl"] == -1.8 * ML

    # 6. Channel 2 respects reagent-dose records: an uncertain dose blocks it.
    uncertain = tmp_path / "uncertain_doses"
    uncertain.mkdir()
    (uncertain / "OTHER.json").write_text(json.dumps({"experiment_id": "OTHER", "status": "DISPATCH_INTENT"}))
    revision = saved(root, 2)["revision"]
    code, out = run("chemyx_control.py", "home", "--channel", "2", "--mock", "--dose-ledger-dir", str(uncertain))
    assert code == 1 and "uncertain" in out and saved(root, 2)["revision"] == revision

    # 7. With the run's own CONFIRMED dose record, the HOME wrapper withdraws 1.8 mL.
    [dose] = rig.doses.glob("*.json")
    dose_bytes = dose.read_bytes()
    assert json.loads(dose_bytes)["status"] == "CONFIRMED"
    code, out = run("chemyx_home.py", "--channel", "2", "--mock")
    assert code == 0 and "WITHDRAW 1.8 mL" in out, out
    assert saved(root, 2)["current_displacement_nl"] == 0 and dose.read_bytes() == dose_bytes
    # Both HOME returns used ordinary timed transfers ended by an acknowledged STOP.
    replays = [replay_journal(path) for path in (root / "manual_runs").glob("*_home_mock_*/operation_journal.jsonl")]
    assert len(replays) == 2 and all(replay.valid for replay in replays)
    records = [record for replay in replays for record in replay.records]
    done = sorted((r["channel"], r["operation_type"], r["requested_parameters"]["volume"], r["operation_id"]) for r in records
                  if r.get("operation_type") in {"withdraw", "infuse"} and r.get("lifecycle_state") == "completed")
    assert [item[:3] for item in done] == [(1, "infuse", 2.0), (2, "withdraw", 1.8)]
    for *_, operation_id in done:
        assert any(r.get("operation_type") == "pump_stop" and r.get("parent_operation_id") == operation_id
                   and r.get("result_classification") == "succeeded" for r in records)


CRASH_CHILD = """
import os, sys
sys.path.insert(0, sys.argv[1])
from chemyx_lab.workflows import chemyx_position as p
ledger = p.PositionLedger(p.state_root(False), owner="process that crashes").acquire()
tracker = ledger.tracker(channel=1, pump="serial:COMTEST", diameter_mm=20.0, capacity_ml=20.0,
                         source="crash test", run_id="crash")
tracker.begin("withdraw", 3.0, operation_id="op-crash")
os._exit(9)  # dies mid-move: no completion, no cleanup
"""


def test_crash_during_motion_leaves_unknown_until_rereferenced(rig, capsys):
    assert cli(rig, "set-home", "--channel", "1", "--live") == 0
    child = subprocess.run([sys.executable, "-c", CRASH_CHILD, str(ROOT)], capture_output=True, text=True, timeout=120)
    assert child.returncode == 9, child.stderr
    capsys.readouterr()
    assert cli(rig, "status", "--channel", "1") == 0
    out = capsys.readouterr().out
    assert "Position: UNKNOWN" in out and "ended during tracked withdraw of 3.0 mL" in out
    opened = len(rig.ports)
    assert cli(rig, "home", "--channel", "1", "--live") == 1
    assert "HOME REFUSED" in capsys.readouterr().out and len(rig.ports) == opened
    state = saved(rig.root, 1)
    assert state["position_state"] == "UNKNOWN" and state["last_trusted_displacement_ml"] == 0.0
    # Recovery is an explicit physical re-reference, never an inferred distance.
    assert cli(rig, "set-home", "--channel", "1", "--live") == 0
    assert saved(rig.root, 1)["position_state"] == "KNOWN"


@pytest.mark.parametrize("fault, expected", [
    ("echo", "KNOWN"),          # rejected before START: provably no motion
    ("timeout", "UNKNOWN"),     # START dispatched, outcome unknown
    ("lost_ack", "UNKNOWN"),    # completion STOP not acknowledged
    ("disconnect", "UNKNOWN"),  # serial link lost during the move
])
def test_interrupted_transfer_is_never_recorded_as_moved(rig, fault, expected):
    assert cli(rig, "set-home", "--channel", "1", "--live") == 0
    assert cli(rig, "withdraw", "--channel", "1", "--live") == 0
    FaultySerial.fault = fault
    assert cli(rig, "withdraw", "--channel", "1", "--live") == 1
    state = saved(rig.root, 1)
    assert state["position_state"] == expected and not state["pending_motion"]
    assert state["current_displacement_nl"] == (5 * ML if expected == "KNOWN" else None)
    if expected == "UNKNOWN":
        assert state["last_trusted_displacement_ml"] == 5.0
        FaultySerial.fault = None
        assert cli(rig, "home", "--channel", "1", "--live") == 1


@pytest.mark.parametrize("damage", ["corrupt_state", "edited_state", "restored_state", "corrupt_journal"])
def test_damaged_records_are_never_trusted(rig, capsys, damage):
    assert cli(rig, "set-home", "--channel", "1", "--live") == 0
    path = rig.root / "channel_1_state.json"
    early = path.read_bytes()
    assert cli(rig, "withdraw", "--channel", "1", "--live") == 0
    if damage == "corrupt_state":
        path.write_text("{ not json", encoding="utf-8")
    elif damage == "edited_state":  # still schema-valid, but claims HOME
        data = json.loads(path.read_text(encoding="utf-8"))
        data.update(current_displacement_nl=0, current_displacement_ml=0.0)
        path.write_text(json.dumps(data), encoding="utf-8")
    elif damage == "restored_state":  # an old copy taken at HOME
        path.write_bytes(early)
    else:
        with (rig.root / "operations.jsonl").open("ab") as handle:
            handle.write(b"{garbage\n")
    capsys.readouterr()
    assert cli(rig, "status", "--channel", "1") == 0
    assert "Position: UNKNOWN" in capsys.readouterr().out
    opened = len(rig.ports)
    assert cli(rig, "home", "--channel", "1", "--live") == 1
    assert len(rig.ports) == opened and saved(rig.root, 1)["position_state"] == "UNKNOWN"
    assert list(rig.root.glob("*.corrupt-*.bak"))  # damaged bytes kept for review


def test_missing_or_invalidated_reference_blocks_home(rig, capsys):
    assert cli(rig, "home", "--channel", "both", "--live") == 1
    assert "no HOME reference exists" in capsys.readouterr().out and not rig.ports
    assert cli(rig, "set-home", "--channel", "both", "--live") == 0
    assert cli(rig, "withdraw", "--channel", "1", "--live") == 0
    # Editing the syringe geometry never silently changes a saved reference's meaning.
    write_config(rig, diameter1=21.0)
    capsys.readouterr()
    assert cli(rig, "home", "--channel", "1", "--live") == 1
    assert "no longer matches the saved HOME reference" in capsys.readouterr().out
    assert saved(rig.root, 1)["position_state"] == "KNOWN"  # refused, not reset
    write_config(rig)
    # The Chemyx cannot report whether it was moving when STOP arrived.
    assert cli(rig, "stop", "--channel", "1", "--live") == 0
    assert saved(rig.root, 1)["position_state"] == "UNKNOWN"
    assert saved(rig.root, 2)["position_state"] == "KNOWN"
    assert cli(rig, "home", "--channel", "1", "--live") == 1


def test_untracked_motion_and_changed_experiment_syringe_invalidate(rig):
    assert cli(rig, "set-home", "--channel", "both", "--live") == 0
    # A live move outside HOME tracking (legacy runner, Arduino integrated
    # test) marks the saved position UNKNOWN before it starts.
    pump = Pump(port="COMTEST")  # never connected; transport replaced below
    pump.set_volume = lambda volume, channel=None: f"volume = {volume}"
    pump.start = lambda channel=None, delay=None: "pump start"
    pump.stop = lambda channel=None: "pump stop"
    cfg = config.PumpConfig("COMTEST", 115200, 1, 0, 20.0, 5.0, 1.0, 1.0, 0.0)
    base.run_safe_metered_move(pump, cfg, "withdraw", 1.0, base.PumpSafetyState(channel=1), sleep_fn=lambda *_: None)
    state = saved(rig.root, 1)
    assert state["position_state"] == "UNKNOWN" and "outside HOME tracking" in state["uncertainty_reason"]
    # An experiment move with a different syringe diameter cannot be compared with HOME.
    with position.PositionLedger(rig.root, owner="test") as ledger:
        tracker = ledger.tracker(channel=2, pump="serial:COMTEST", diameter_mm=14.5, capacity_ml=5.0,
                                 source="si6 test", run_id="r")
        tracker.complete(tracker.begin("infuse", 1.8, operation_id="op"))
    assert saved(rig.root, 2)["position_state"] == "UNKNOWN"
    # A legacy unaddressed (channel 0) transfer cannot be attributed to either syringe.
    assert cli(rig, "set-home", "--channel", "both", "--live") == 0
    with position.PositionLedger(rig.root, owner="test") as ledger:
        tracker = ledger.tracker(channel=0, pump="serial:COMTEST", diameter_mm=20.0, capacity_ml=20.0,
                                 source="legacy channel 0", run_id="r")
        assert tracker.complete(tracker.begin("withdraw", 1.0, operation_id="op0"))
    assert {saved(rig.root, ch)["position_state"] for ch in (1, 2)} == {"UNKNOWN"}


def test_channel2_dose_records_block_and_are_never_cleared(rig, capsys):
    assert cli(rig, "set-home", "--channel", "both", "--live") == 0
    assert cli(rig, "withdraw", "--channel", "2", "--live") == 0
    rig.doses.mkdir()
    record = rig.doses / "EXP.json"
    record.write_text(json.dumps({"experiment_id": "EXP", "status": "RESERVED"}), encoding="utf-8")
    capsys.readouterr()
    # A reserved, undosed reaction must not receive reagent, even when authorized.
    assert cli(rig, "home", "--channel", "2", "--live", "--allow-channel2-infusion") == 1
    assert "RESERVED" in capsys.readouterr().out
    record.write_text(json.dumps({"experiment_id": "EXP", "status": "DISPATCH_INTENT"}), encoding="utf-8")
    assert cli(rig, "withdraw", "--channel", "2", "--live") == 1
    assert saved(rig.root, 2)["current_displacement_nl"] == 2 * ML
    before = record.read_bytes()
    assert cli(rig, "set-home", "--channel", "2", "--live") == 0
    assert record.read_bytes() == before
    assert cli(rig, "withdraw", "--channel", "1", "--live") == 0  # Channel 1 is unaffected
