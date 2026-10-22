"""Scripts 23 / 25 on 24's two-instrument rig: mock Chemyx serial + fixture NMR, no hardware."""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path
import re
import subprocess
import sys

import pytest
import yaml

from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.workflows import simple_rig
from chemyx_lab.workflows.simple_rig import FIXTURE, SimulatedClock, close_logger, make_logger
from chemyx_lab.workflows.stage2_legacy_nmr import MockLegacyClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


s23 = load("stage1stage2_23_test", "23_stage1stage2.py")
s25 = load("quickcheck_25_test", "25_2instrumentquickcheck.py")
C23 = s23.load_config()["chemyx"]  # Tests follow the YAML, so editing volumes needs no test change.
WITHDRAW, INFUSE, INITIAL = C23["withdraw_volume_ml"], C23["infuse_volume_ml"], C23["initial_volume_ml"]
D23 = s23.load_config()["channel_2"]


def files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())


def sent(rig, channel):
    return [command.strip() for command in rig.transport.tx_log if command.startswith(f"{channel} ")]


def volumes(rig, channel):
    return [float(command.split()[-1]) for command in sent(rig, channel) if "set volume " in command]


def short(cfg):
    cfg["stages"]["stage_1"].update(duration_minutes=240, interval_minutes=120)  # Samples at 0 and 2 h.
    cfg["stages"]["stage_2"].update(duration_minutes=30, interval_minutes=30)    # Samples at 0 and 30 min.
    return cfg


def write(tmp_path, cfg, name="cfg.yaml"):
    path = tmp_path / name
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


@pytest.fixture
def rigs():
    made, events = [], []

    def factory(cfg, *, mock, clock, log):
        rig = s23.StageRig(cfg, mock=mock, clock=clock, log=log)
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


class CtrlCAt(SimulatedClock):
    """Ctrl+C halfway through the first wait of `seconds`."""
    def __init__(self, seconds):
        super().__init__()
        self.target, self.fired = seconds, False

    def sleep(self, seconds):
        if not self.fired and seconds == pytest.approx(self.target):
            self.fired = True
            super().sleep(seconds / 2)
            raise KeyboardInterrupt
        super().sleep(seconds)


# ----------------------------------------------------------------------- 23
def test_23_full_schedule_one_dose_between_stages(tmp_path, rigs):
    cfg = s23.load_config()
    status, tally = s23.run(cfg, mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "COMPLETE" and tally["dose"] == "COMPLETE"
    assert tally["stage_1"] == dict(slots=24, saved=24, nmr_failed=0, missed=0)
    assert tally["stage_2"] == dict(slots=13, saved=13, nmr_failed=0, missed=0)
    rig = rigs.made[0]
    assert volumes(rig, 1) == [-WITHDRAW, INFUSE] * 37 and volumes(rig, 2) == [D23["volume_ml"]]
    ch1 = [e for e in rigs.events if e[0] == 1 and e[1] == "withdraw"]
    [dose] = [e for e in rigs.events if e[0] == 2]
    assert [e[3] for e in ch1[:24]] == [i * 7200.0 for i in range(24)]
    assert dose[1:3] == ("infuse", D23["volume_ml"]) and dose[3] == 48 * 3600.0  # After the full 48 h window.
    dose_end = dose[3] + D23["volume_ml"] / D23["rate_ml_min"] * 60 + C23["extra_seconds"]
    assert [e[3] for e in ch1[24:]] == pytest.approx([dose_end + j * 1800.0 for j in range(13)])
    assert rig.pump.contents_ml == pytest.approx(INITIAL - 37 * (INFUSE - WITHDRAW))
    assert rig.ch2.contents_ml == pytest.approx(D23["initial_volume_ml"] - D23["volume_ml"])
    assert files(tmp_path / "run") == (["log.txt"] + [f"stage_1/nmr_raw/stage_1_{i:04d}.dx" for i in range(1, 25)]
                                       + [f"stage_2/nmr_raw/stage_2_{i:04d}.dx" for i in range(1, 14)])
    assert not rig.pump.pump.is_connected
    starts = [payload for _, path, payload in rig.nmr.fake.calls if path.endswith("RunExperiment")]
    assert len(starts) == 37 and {payload["NumberOfScans"] for payload in starts} == {8}


def test_23_volumes_come_from_the_yaml(tmp_path, rigs):
    cfg = short(s23.load_config())
    cfg["chemyx"]["infuse_volume_ml"] = 5.1
    assert s23.run(s23.load_config(write(tmp_path, cfg)), mock=True, output_dir=tmp_path / "run",
                        rig_factory=rigs)[0] == "COMPLETE"
    assert volumes(rigs.made[0], 1) == [-WITHDRAW, 5.1] * 4


@pytest.mark.parametrize("change,message", [
    (dict(initial_volume_ml=3.0), "runs out at sample 16 of 37"),
    (dict(infuse_volume_ml=5.5), "need at least 18.50 mL loaded"),
    (dict(initial_volume_ml=16.0), "would hold 21.00 mL at sample 1"),
])
def test_23_refuses_a_loading_that_cannot_cover_every_sample(tmp_path, change, message):
    cfg = s23.load_config()
    cfg["chemyx"].update(change)
    path = write(tmp_path, cfg)
    with pytest.raises(ValueError, match=re.escape(message)):
        s23.load_config(path)
    assert s23.main(["--mock", "--config", str(path), "--output-dir", str(tmp_path / "run")]) == 2
    assert not (tmp_path / "run").exists()


def test_23_nmr_failure_is_skipped_and_the_run_continues(tmp_path, rigs, monkeypatch):
    monkeypatch.setattr(simple_rig, "MockLegacyClient", FirstScanMissing)
    status, tally = s23.run(short(s23.load_config()), mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "COMPLETE" and tally["dose"] == "COMPLETE"
    assert tally["stage_1"]["saved"] == 1 and tally["stage_1"]["nmr_failed"] == 1 and tally["stage_2"]["saved"] == 2
    assert volumes(rigs.made[0], 1) == [-WITHDRAW, INFUSE] * 4  # The failed sample was still returned.


def test_23_dose_rejected_stops_before_stage_2(tmp_path, rigs, monkeypatch):
    original = Pump.set_volume
    monkeypatch.setattr(Pump, "set_volume", lambda self, value, channel=None: "error: syringe stalled"
                        if channel == 2 else original(self, value, channel=channel))
    status, tally = s23.run(short(s23.load_config()), mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "STOPPED" and tally["dose"] == "UNCERTAIN" and tally["stage_2"]["saved"] == 0
    assert not list((tmp_path / "run/stage_2/nmr_raw").iterdir())
    log = (tmp_path / "run/log.txt").read_text(encoding="utf-8")
    assert "Channel 2 dose interrupted; CH2 STOP sent" in log and "never repeated automatically" in log
    assert log.index("Chemyx CH2: STOP sent", log.index("CHANNEL 2 DOSE")) < log.index("Chemyx connection closed")


def test_23_ctrl_c_during_the_dose_stops_ch2_and_never_starts_stage_2(tmp_path, rigs):
    dose_wait = D23["volume_ml"] / D23["rate_ml_min"] * 60 + C23["extra_seconds"]
    status, tally = s23.run(short(s23.load_config()), mock=True, output_dir=tmp_path / "run",
                                 clock=CtrlCAt(dose_wait), rig_factory=rigs)
    assert status == "INTERRUPTED" and tally["dose"] == "UNCERTAIN" and tally["stage_2"]["saved"] == 0
    rig = rigs.made[0]
    assert sent(rig, 2)[-1] == "2 stop" and not rig.pump.pump.is_connected
    assert volumes(rig, 1) == [-WITHDRAW, INFUSE] * 2  # Stage 1 only; no blind CH1 action after Ctrl+C.


def test_23_review_starts_once_per_stage_before_the_port_opens(tmp_path, rigs):
    calls = []

    def launcher(stage_dir, raw_dir, cfg, log):
        calls.append((stage_dir.name, raw_dir.is_dir(), len(rigs.made)))
    cfg = short(s23.load_config())
    assert s23.run(cfg, mock=True, output_dir=tmp_path / "on", rig_factory=rigs, review_launcher=launcher)[0] == "COMPLETE"
    assert calls == [("stage_1", True, 0), ("stage_2", True, 0)]
    cfg["review"]["enabled"] = False
    assert s23.run(cfg, mock=True, output_dir=tmp_path / "off", rig_factory=rigs, review_launcher=launcher)[0] == "COMPLETE"
    assert len(calls) == 2


def test_23_review_that_cannot_start_never_affects_the_run(tmp_path, rigs, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("no python here")
    monkeypatch.setattr(s23.subprocess, "Popen", broken)
    status, tally = s23.run(short(s23.load_config()), mock=True, output_dir=tmp_path / "run",
                                 rig_factory=rigs, review_launcher=s23.start_review)
    assert status == "COMPLETE" and tally["stage_2"]["saved"] == 2
    assert "Review plots (stage_1) NOT started: OSError: no python here" in (tmp_path / "run/log.txt").read_text(encoding="utf-8")


def shifted(data, minutes):
    stamp = re.search(rb"##LONG DATE=2026/08/10 17:(\d\d):21-0400", data)
    return data.replace(stamp[0], b"##LONG DATE=2026/08/10 17:%02d:21-0400" % (int(stamp[1]) + minutes), 1)


def test_23_review_config_drives_the_review_worker_with_labelled_plots(tmp_path, monkeypatch):
    stage = tmp_path / "23_stage1stage2_20261021_170000/stage_1"
    (stage / "nmr_raw").mkdir(parents=True)
    for i in range(2):
        (stage / "nmr_raw" / f"stage_1_{i + 1:04d}.dx").write_bytes(shifted(FIXTURE.read_bytes(), 120 * i // 60))
    launched = []
    monkeypatch.setattr(s23.subprocess, "Popen", lambda command, **kwargs: launched.append(command))
    log = make_logger(tmp_path, SimulatedClock(), "review_test")
    try:
        s23.start_review(stage, stage / "nmr_raw", s23.load_config(), log)
    finally:
        close_logger(log)
    monkeypatch.undo()  # Popen was replaced module-wide; restore it before running the worker.
    [command] = launched
    assert command[command.index("--parent-pid") + 1].isdigit() and "--watch" in command
    once = command[:command.index("--watch")]  # The same worker, one pass, in this process tree.
    proc = subprocess.run(once, cwd=ROOT, capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = stage / "analysis"
    assert {"observation_0001.png", "observation_0002.png", "peak_area_vs_time.png", "peak_areas.csv"} <= {
        p.name for p in out.iterdir()}
    with (out / "plot_manifest.csv").open(encoding="utf-8") as handle:
        titles = [row["visible_title"] for row in csv.DictReader(handle)]
    assert titles and all(t.startswith("23_stage1stage2_20261021_170000 stage_1 ") for t in titles)


def test_23_cli_without_arduino_and_declined_confirmation(tmp_path):
    cfg = short(s23.load_config())
    cfg["review"]["enabled"] = False
    path = write(tmp_path, cfg)
    code = r'''
import builtins, importlib.abc, pathlib, runpy, sys
class NoArduino(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "arduino" or fullname.startswith("arduino."):
            raise AssertionError("Arduino import attempted: " + fullname)
sys.meta_path.insert(0, NoArduino())
sys.path.insert(0, str(pathlib.Path.cwd() / "scripts"))
builtins.input = lambda prompt: sys.argv[3]
ns = runpy.run_path("scripts/23_stage1stage2.py")
raise SystemExit(ns["main"](["--mock", "--config", sys.argv[1], "--output-dir", sys.argv[2]]))
'''
    run = lambda folder, answer: subprocess.run([sys.executable, "-c", code, str(path), str(tmp_path / folder), answer],
                                                cwd=ROOT, capture_output=True, text=True, timeout=120)
    accepted = run("yes", "y")
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert len(list((tmp_path / "yes").rglob("*.dx"))) == 4
    declined = run("no", "n")
    assert declined.returncode == 3 and files(tmp_path / "no") == ["log.txt"]


# ----------------------------------------------------------------------- 25
def test_25_check_passes_and_leaves_the_loading_unchanged(tmp_path, rigs):
    cfg = s25.load_config()
    status, results = s25.run(cfg, mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "COMPLETE" and set(results.values()) == {"PASS"}
    rig = rigs.made[0]
    assert volumes(rig, 1) == [-1.0, 1.0] and rig.pump.contents_ml == pytest.approx(INITIAL)
    assert sent(rig, 2) == ["2 stop"]  # CH2 answers STOP and never moves.
    starts = [payload for _, path, payload in rig.nmr.fake.calls if path.endswith("RunExperiment")]
    assert [payload["NumberOfScans"] for payload in starts] == [1]
    assert files(tmp_path / "run") == ["check_0001.dx", "log.txt"]
    assert "CHECK PASSED" in (tmp_path / "run/log.txt").read_text(encoding="utf-8")


def test_25_uses_23_hardware_settings_and_only_overrides_the_check():
    c25, c23 = s25.load_config(), s23.load_config()
    for section in ("nmr", "chemyx", "channel_2"):
        differ = {k for k in c23[section] if c25[section][k] != c23[section][k]}
        assert differ == {"nmr": {"scans"}, "chemyx": {"withdraw_volume_ml", "infuse_volume_ml"}, "channel_2": set()}[section]
    assert c25["chemyx"]["withdraw_volume_ml"] == c25["chemyx"]["infuse_volume_ml"]


def test_25_nmr_failure_fails_the_check_but_returns_the_sample(tmp_path, rigs, monkeypatch):
    monkeypatch.setattr(simple_rig, "MockLegacyClient", FirstScanMissing)
    status, results = s25.run(s25.load_config(), mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert results["nmr"] == "FAIL" and results["withdraw"] == results["infuse"] == results["close"] == "PASS"
    assert volumes(rigs.made[0], 1) == [-1.0, 1.0]
    assert "CHECK FAILED" in (tmp_path / "run/log.txt").read_text(encoding="utf-8")


def test_25_pump_failure_fails_without_a_blind_infusion(tmp_path, rigs, monkeypatch):
    monkeypatch.setattr(Pump, "start", lambda self, **kwargs: (_ for _ in ()).throw(TimeoutError("no reply")))
    status, results = s25.run(s25.load_config(), mock=True, output_dir=tmp_path / "run", rig_factory=rigs)
    assert status == "STOPPED" and results["withdraw"] == "FAIL" and results["infuse"] == "FAIL"
    assert results["close"] == "PASS" and not rigs.made[0].pump.pump.is_connected
    assert volumes(rigs.made[0], 1) == [-1.0]


@pytest.mark.parametrize("answer,code", [("y", 0), ("n", 3)])
def test_25_cli_one_confirmation_and_exit_codes(tmp_path, answer, code):
    proc = subprocess.run([sys.executable, "scripts/25_2instrumentquickcheck.py", "--mock", "--output-dir",
                           str(tmp_path / "run")], cwd=ROOT, input=answer + "\n", capture_output=True, text=True, timeout=120)
    assert proc.returncode == code, proc.stdout + proc.stderr
    assert proc.stdout.count("Confirm rig is ready [y/N]:") == 1
