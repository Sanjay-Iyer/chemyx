"""Script 20: three-instrument Stage 1 -> CH2 dose -> Stage 2 on 21's simple rig (mock only)."""
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
from chemyx_lab.workflows.stage2_legacy_nmr import MockLegacyClient
from test_simple_rig import home, jogs, saved  # noqa: F401  (home is a fixture)

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("stage1stage2_20_test", ROOT / "scripts/20_stage1stage2.py")
s20 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s20)
C = s20.load_config()["chemyx"]  # Tests follow the YAML (21's recipe), so editing it needs no test change.
D = s20.load_config()["channel_2"]
RECIPE = [-C["pre_sample_volume_ml"], -C["sample_volume_ml"], C["pre_sample_volume_ml"] + C["sample_volume_ml"],
          -C["wash_volume_ml"], C["wash_volume_ml"]]


def cfg20(home):
    cfg = s20.load_config()
    cfg["arduino"]["position_file"] = str(home / "simple_needle_position.txt")  # Mock -> *_mock.txt here.
    return cfg


def short(cfg):
    cfg["stages"]["stage_1"].update(duration_minutes=240, interval_minutes=120)  # Samples at 0 and 2 h.
    cfg["stages"]["stage_2"].update(duration_minutes=30, interval_minutes=30)    # Samples at 0 and 30 min.
    return cfg


def files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def sent(rig, channel):
    return [command.strip() for command in rig.transport.tx_log if command.startswith(f"{channel} ")]


def volumes(rig, channel):
    return [float(command.split()[-1]) for command in sent(rig, channel) if "set volume " in command]


@pytest.fixture
def rigs():
    made, events = [], []

    def factory(cfg, *, mock, clock, log):
        rig = s20.StageRig(cfg, mock=mock, clock=clock, log=log)
        for pump in (rig.pump, rig.ch2):
            def recorded(action, volume, rate, original=pump.transfer, channel=pump.channel):
                events.append((channel, action, float(volume), clock.now()))
                return original(action, volume, rate)
            pump.transfer = recorded
        connect = rig.pump.pump.connect

        def connect_recorded():
            result = connect()
            rig.transport = rig.pump.pump.ser  # Kept after the port closes.
            return result
        rig.pump.pump.connect = connect_recorded
        made.append(rig)
        return rig
    factory.made, factory.events = made, events
    return factory


class FirstScanReturnsNoData(MockLegacyClient):
    def _request(self, method, path, payload=None, params=None):
        reply = super()._request(method, path, payload, params)
        return {"ResultCode": 0, "JDX_FileContents_TD": ""} if path.endswith("ExperimentStatus") and self.counter == 1 else reply


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


def test_20_full_schedule_21_recipe_and_one_dose_at_home(home, rigs):
    status, tally = s20.run(cfg20(home), mock=True, output_dir=home / "run", rig_factory=rigs)
    assert status == "COMPLETE" and tally["dose"] == "COMPLETE"
    assert tally["stage_1"] == dict(slots=24, saved=24, nmr_failed=0, missed=0)
    assert tally["stage_2"] == dict(slots=13, saved=13, nmr_failed=0, missed=0)
    rig = rigs.made[0]
    assert volumes(rig, 1) == RECIPE * 37 and volumes(rig, 2) == [D["volume_ml"]]
    starts = [e[3] for e in rigs.events if e[0] == 1 and e[2] == C["pre_sample_volume_ml"] and e[1] == "withdraw"]
    [dose] = [e for e in rigs.events if e[0] == 2]
    assert starts[:24] == pytest.approx([i * 7200.0 for i in range(24)]) and dose[3] == pytest.approx(48 * 3600.0)
    dose_end = dose[3] + D["volume_ml"] / D["rate_ml_min"] * 60 + C["extra_seconds"]
    assert starts[24:] == pytest.approx([dose_end + j * 1800.0 for j in range(13)])
    # UP once, DOWN/UP per sample, DOWN/UP around the dose, then back to HOME.
    assert jogs(rig.needle.controller.transport) == [200] + [-200, 200] * 24 + [-200, 200] + [-200, 200] * 13 + [-200]
    assert saved(home) == (0, 0) and rig.returned_home and rig.pump.contents_ml == 0
    assert rig.ch2.contents_ml == pytest.approx(D["initial_volume_ml"] - D["volume_ml"])
    assert files(home / "run") == (["log.txt"] + [f"stage_1/nmr_raw/stage_1_{i:04d}.dx" for i in range(1, 25)]
                                   + [f"stage_2/nmr_raw/stage_2_{i:04d}.dx" for i in range(1, 14)])
    payloads = [p for _, path, p in rig.nmr.fake.calls if path.endswith("RunExperiment")]
    assert len(payloads) == 37 and {p["NumberOfScans"] for p in payloads} == {8}


def test_20_nmr_failure_is_skipped_and_the_run_continues(home, rigs, monkeypatch):
    monkeypatch.setattr(simple_rig, "MockLegacyClient", FirstScanReturnsNoData)
    status, tally = s20.run(short(cfg20(home)), mock=True, output_dir=home / "run", rig_factory=rigs)
    assert status == "COMPLETE" and tally["dose"] == "COMPLETE"
    assert tally["stage_1"]["nmr_failed"] == 1 and tally["stage_1"]["saved"] == 1 and tally["stage_2"]["saved"] == 2
    assert volumes(rigs.made[0], 1) == RECIPE * 4  # The failed slot's sample was still returned and washed.


def test_20_dose_rejected_stops_before_stage_2_and_returns_home(home, rigs, monkeypatch):
    original = Pump.set_volume
    monkeypatch.setattr(Pump, "set_volume", lambda self, value, channel=None: "error: syringe stalled"
                        if channel == 2 else original(self, value, channel=channel))
    status, tally = s20.run(short(cfg20(home)), mock=True, output_dir=home / "run", rig_factory=rigs)
    assert status == "STOPPED" and tally["dose"] == "UNCERTAIN" and tally["stage_2"]["saved"] == 0
    assert not list((home / "run/stage_2/nmr_raw").iterdir())
    assert saved(home) == (0, 0) and rigs.made[0].returned_home
    log = (home / "run/log.txt").read_text(encoding="utf-8")
    assert "Channel 2 dose interrupted; CH2 STOP sent" in log and "never repeated automatically" in log
    assert log.index("Chemyx CH2: STOP sent", log.index("CHANNEL 2 DOSE")) < log.index("Serial ports closed")


def test_20_ctrl_c_during_the_dose_stops_ch2_and_returns_home(home, rigs):
    dose_wait = D["volume_ml"] / D["rate_ml_min"] * 60 + C["extra_seconds"]
    status, tally = s20.run(short(cfg20(home)), mock=True, output_dir=home / "run", clock=CtrlCAt(dose_wait),
                            rig_factory=rigs)
    rig = rigs.made[0]
    assert status == "INTERRUPTED" and tally["dose"] == "UNCERTAIN" and tally["stage_2"]["saved"] == 0
    assert sent(rig, 2)[-1] == "2 stop" and saved(home) == (0, 0) and rig.returned_home
    assert volumes(rig, 1) == RECIPE * 2 and rig.pump.contents_ml == 0


def test_20_review_starts_once_per_stage_before_the_ports_open(home, rigs):
    calls = []

    def launcher(stage_dir, raw_dir, cfg, log):
        calls.append((stage_dir.name, raw_dir.is_dir(), len(rigs.made)))
    cfg = short(cfg20(home))
    assert s20.run(cfg, mock=True, output_dir=home / "on", rig_factory=rigs, review_launcher=launcher)[0] == "COMPLETE"
    assert calls == [("stage_1", True, 0), ("stage_2", True, 0)]
    cfg["review"]["enabled"] = False
    assert s20.run(cfg, mock=True, output_dir=home / "off", rig_factory=rigs, review_launcher=launcher)[0] == "COMPLETE"
    assert len(calls) == 2


def test_20_settings_come_from_21_and_bad_dose_settings_are_refused(home):
    c20, c21 = s20.load_config(), s20.stage2only.load_config()
    for section in ("arduino", "chemyx", "nmr", "output"):
        assert c20[section] == c21[section]
    cfg = short(cfg20(home))
    cfg["review"]["enabled"] = False
    good = home / "c20.yaml"
    good.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    cfg["channel_2"]["initial_volume_ml"] = 1.0  # Less than the 1.86 mL dose.
    bad = home / "bad.yaml"
    bad.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match="must cover volume_ml"):
        s20.load_config(bad)
    run = lambda *args: subprocess.run([sys.executable, "scripts/20_stage1stage2.py", *args], cwd=ROOT,
                                       stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    ok = run("--mock", "--config", str(good), "--output-dir", str(home / "cli"))  # No prompt: stdin is closed.
    assert ok.returncode == 0, ok.stdout + ok.stderr
    assert len(list((home / "cli").rglob("*.dx"))) == 4
    assert run("--mock", "--config", str(bad), "--output-dir", str(home / "bad_run")).returncode == 2
    assert not (home / "bad_run").exists()
