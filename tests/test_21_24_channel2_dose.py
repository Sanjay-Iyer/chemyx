"""Optional one-time Channel 2 dose after the time-0 sample in 21 and 24 (mock only)."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.workflows import simple_rig
from chemyx_lab.workflows.simple_rig import SimulatedClock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


s21 = load("stage2only_21_dose_test", "21_stage2only.py")
s24 = load("stage2only_24_dose_test", "24_stage2only.py")
DOSE = s21.load_config()["channel_2"]
DOSE_SECONDS = DOSE["volume_ml"] / DOSE["rate_ml_min"] * 60 + s21.load_config()["chemyx"]["extra_seconds"]


def cfg21(tmp_path, **dose):
    cfg = s21.load_config()
    cfg["arduino"]["position_file"] = str(tmp_path / "simple_needle_position.txt")  # Mock -> *_mock.txt here.
    cfg["experiment"]["duration_minutes"] = 30  # Samples at 0, 15 and 30 min.
    cfg["channel_2"].update(dose)
    return cfg


def cfg24(**dose):
    cfg = s24.load_config()
    cfg["experiment"]["observation_count"] = 3  # Samples at 0, 15 and 30 min.
    cfg["channel_2"].update(dose)
    return cfg


@pytest.fixture
def moves(monkeypatch):
    """(channel, action, volume, start, end) of every completed pump transfer."""
    events = []
    original = simple_rig.SyringePump.transfer

    def recorded(self, action, volume, rate):
        start = self.clock.now()
        original(self, action, volume, rate)
        events.append((self.channel, action, float(volume), start, self.clock.now()))
    monkeypatch.setattr(simple_rig.SyringePump, "transfer", recorded)
    return events


@pytest.fixture
def sent(monkeypatch):
    commands = []
    original = Pump.send_command

    def recorded(self, command, channel=None):
        commands.append(self._with_channel(command, channel).strip())
        return original(self, command, channel)
    monkeypatch.setattr(Pump, "send_command", recorded)
    return commands


class CtrlCAt(SimulatedClock):
    def __init__(self, seconds):
        super().__init__()
        self.target, self.fired = seconds, False

    def sleep(self, seconds):
        if not self.fired and seconds == pytest.approx(self.target):
            self.fired = True
            super().sleep(seconds / 2)
            raise KeyboardInterrupt
        super().sleep(seconds)


def test_dose_is_off_by_default_and_nothing_changes(tmp_path, moves, sent):
    assert s21.load_config()["channel_2"]["enabled"] is False and s24.load_config()["channel_2"]["enabled"] is False
    status, tally = s21.run(cfg21(tmp_path), mock=True, output_dir=tmp_path / "r21")
    assert status == "COMPLETE" and tally == dict(slots=3, saved=3, nmr_failed=0, missed=0)
    status, tally = s24.run(cfg24(), mock=True, output_dir=tmp_path / "r24")
    assert status == "COMPLETE" and tally == dict(slots=3, saved=3, nmr_failed=0, missed=0)
    assert not [c for c in sent if c.startswith("2 ")] and {m[0] for m in moves} == {1}


def test_21_doses_once_three_minutes_after_the_time_zero_sample(tmp_path, moves):
    status, tally = s21.run(cfg21(tmp_path, enabled=True), mock=True, output_dir=tmp_path / "run")
    assert status == "COMPLETE" and tally == dict(slots=3, saved=3, nmr_failed=0, missed=0, dose="COMPLETE")
    [dose] = [m for m in moves if m[0] == 2]
    slot_1_end = max(m[4] for m in moves if m[0] == 1 and m[4] <= dose[3])
    assert dose[1:3] == ("infuse", DOSE["volume_ml"]) and dose[3] == pytest.approx(slot_1_end + 180)
    starts = [m[3] for m in moves if m[0] == 1 and m[1] == "withdraw" and m[2] == 8.0]
    assert starts == pytest.approx([0.0, 900.0, 1800.0])  # The schedule did not move.
    log = (tmp_path / "run/log.txt").read_text(encoding="utf-8")
    dose_part = log[log.index("CHANNEL 2 DOSE at"):log.index("Slot 2/3 (scheduled")]
    assert dose_part.index("needle DOWN") < dose_part.index("Chemyx CH2: infuse 1.86 mL") < dose_part.index("needle UP")
    assert "CH2 dose: COMPLETE" in log


def test_21_dose_that_runs_past_the_next_sample_skips_that_sample(tmp_path, moves):
    status, tally = s21.run(cfg21(tmp_path, enabled=True, delay_after_time_zero_seconds=600), mock=True,
                            output_dir=tmp_path / "run")
    assert status == "COMPLETE" and tally["dose"] == "COMPLETE" and tally["missed"] == 1 and tally["saved"] == 2
    assert "Slot 2/3 MISSED" in (tmp_path / "run/log.txt").read_text(encoding="utf-8")


def test_21_ctrl_c_during_the_dose_stops_ch2_and_returns_the_needle_home(tmp_path, sent):
    status, tally = s21.run(cfg21(tmp_path, enabled=True), mock=True, output_dir=tmp_path / "run",
                            clock=CtrlCAt(DOSE_SECONDS))
    assert status == "INTERRUPTED" and tally["dose"] == "UNCERTAIN" and tally["saved"] == 1
    assert [c for c in sent if c.startswith("2 ")][-1] == "2 stop"
    log = (tmp_path / "run/log.txt").read_text(encoding="utf-8")
    assert "never repeated automatically" in log and "Needle at HOME" in log
    assert (tmp_path / "simple_needle_steps_mock.txt").read_text(encoding="utf-8").strip() == "0"


def test_24_doses_once_three_minutes_after_the_time_zero_sample(tmp_path, moves):
    status, tally = s24.run(cfg24(enabled=True), mock=True, output_dir=tmp_path / "run")
    assert status == "COMPLETE" and tally == dict(slots=3, saved=3, nmr_failed=0, missed=0, dose="COMPLETE")
    [dose] = [m for m in moves if m[0] == 2]
    slot_1_end = max(m[4] for m in moves if m[0] == 1 and m[4] <= dose[3])
    assert dose[3] == pytest.approx(slot_1_end + 180)
    assert [m[3] for m in moves if m[0] == 1 and m[1] == "withdraw"] == pytest.approx([0.0, 900.0, 1800.0])
    assert "CH2 line secured in flask" in (tmp_path / "run/log.txt").read_text(encoding="utf-8")


def test_24_ctrl_c_during_the_dose_stops_ch2(tmp_path, sent):
    status, tally = s24.run(cfg24(enabled=True), mock=True, output_dir=tmp_path / "run", clock=CtrlCAt(DOSE_SECONDS))
    assert status == "INTERRUPTED" and tally["dose"] == "UNCERTAIN" and tally["saved"] == 1
    assert [c for c in sent if c.startswith("2 ")][-1] == "2 stop"
    assert "never repeated automatically" in (tmp_path / "run/log.txt").read_text(encoding="utf-8")


@pytest.mark.parametrize("script", ["21", "24"])
@pytest.mark.parametrize("change,message", [
    (dict(initial_volume_ml=1.0), "must cover volume_ml"),
    (dict(delay_after_time_zero_seconds=-1), "delay_after_time_zero_seconds"),
    (dict(rate_ml_min=0), "rate_ml_min"),
    (dict(enabled="yes"), "enabled must be true or false"),
])
def test_bad_dose_settings_are_refused_before_any_port_opens(tmp_path, script, change, message):
    module, base = (s21, "21_stage2only") if script == "21" else (s24, "24_stage2only")
    for enabled in (True, False):
        cfg = {"base_config": f"configs/experiments/{base}.yaml", "channel_2": {"enabled": enabled, **change}}
        path = tmp_path / f"c{enabled}.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
        if enabled or "enabled" in change:
            with pytest.raises(ValueError, match=message):
                module.load_config(path)
        else:
            assert module.load_config(path)["channel_2"]["enabled"] is False  # Ignored while switched off.


def test_24_with_the_dose_still_never_imports_arduino(tmp_path):
    cfg = {"base_config": "configs/experiments/24_stage2only.yaml", "channel_2": {"enabled": True},
           "experiment": {"observation_count": 2}, "review": {"enabled": False}}
    (tmp_path / "c.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    code = r'''
import builtins, importlib.abc, pathlib, runpy, sys
class NoArduino(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "arduino" or fullname.startswith("arduino."):
            raise AssertionError("Arduino import attempted: " + fullname)
sys.meta_path.insert(0, NoArduino())
sys.path.insert(0, str(pathlib.Path.cwd() / "scripts"))
builtins.input = lambda prompt: "y"
ns = runpy.run_path("scripts/24_stage2only.py")
raise SystemExit(ns["main"](["--mock", "--config", sys.argv[1], "--output-dir", sys.argv[2]]))
'''
    proc = subprocess.run([sys.executable, "-c", code, str(tmp_path / "c.yaml"), str(tmp_path / "run")], cwd=ROOT,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "CH2 dose: COMPLETE" in (tmp_path / "run/log.txt").read_text(encoding="utf-8")
