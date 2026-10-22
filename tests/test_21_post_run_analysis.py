"""Script 21 (was 15) post-run analysis: only after the physical run, never changes it (mock only)."""
import csv
import hashlib
import re
import subprocess
from pathlib import Path

import pytest
import yaml
from PIL import Image

from chemyx_lab.workflows.simple_rig import FIXTURE, SimulatedClock, close_logger, make_logger
from test_simple_rig import cli, home, rig_cfg, rigs, saved, stage2  # noqa: F401  (home/rigs are fixtures)


def digests(folder):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(Path(folder).glob("*.dx"))}


def physical_log(run_dir):
    """log.txt up to the end-of-run summary, without wall-clock time or the run folder name."""
    lines = []
    for line in (run_dir / "log.txt").read_text(encoding="utf-8").splitlines():
        text = line.split(" ", 2)[2].replace(str(run_dir), "<RUN>")  # Drop the date and time.
        if "Spectra: " in text:
            break
        lines.append(text)
    return lines


def streams(rig, run_dir):
    """What reached the instruments, in order: Arduino lines, NMR requests and the logged Chemyx exchanges."""
    nmr = [(method, path, {k: v.replace(str(run_dir), "<RUN>") if isinstance(v, str) else v for k, v in (payload or {}).items()})
           for method, path, payload in rig.nmr.fake.calls]
    return rig.needle.controller.transport.tx_log, nmr, physical_log(run_dir)


class Recorder:
    def __init__(self):
        self.calls = []

    def __call__(self, raw_dir, log):
        self.calls.append((raw_dir, (raw_dir.parent / "log.txt").read_text(encoding="utf-8"), digests(raw_dir)))


class CtrlCAtSecondSlot(SimulatedClock):
    """Ctrl+C during the wait for slot 2, after slot 1 saved its spectrum."""
    def sleep(self, seconds):
        if seconds > 300:
            raise KeyboardInterrupt
        super().sleep(seconds)


def test_analysis_starts_after_home_and_closed_ports_and_changes_no_instrument_command(home, rigs):
    assert cli(home, "set-home")[0] == 0  # Both runs then start from the same saved HOME.
    cfg = rig_cfg(stage2, home, duration_minutes=30)
    plain = stage2.run(cfg, mock=True, output_dir=home / "plain", rig_factory=rigs)
    recorder = Recorder()
    analysed = stage2.run(cfg, mock=True, output_dir=home / "analysed", rig_factory=rigs, analyze=recorder)
    assert plain == analysed == ("COMPLETE", dict(slots=3, saved=3, nmr_failed=0, missed=0))
    assert streams(rigs.made[0], home / "plain") == streams(rigs.made[1], home / "analysed")
    [(raw_dir, log_then, raw_then)] = recorder.calls
    assert raw_dir == home / "analysed/nmr_raw" and list(raw_then) == [f"stage_2_{i:04d}.dx" for i in (1, 2, 3)]
    assert log_then.index("Needle at HOME") < log_then.index("Serial ports closed") < log_then.index("COMPLETE | slots 3")
    assert rigs.made[1].pump.contents_ml == 0 and saved(home) == (0, 0)
    assert "Analysis later" in (home / "plain/log.txt").read_text(encoding="utf-8")


def fake_09(tmp_path, body):
    script = tmp_path / "fake_09.py"
    script.write_text("import sys\n" + body, encoding="utf-8")
    return script


FAILURES = {"exit_1": "print('Output: nowhere')\nsys.exit(1)\n", "crash": "import numpy_missing_on_this_laptop\n",
            "missing_script": None, "start_error": OSError("cannot start"), "timeout": subprocess.TimeoutExpired("09", 1),
            "ctrl_c": KeyboardInterrupt(), "callable_raises": ZeroDivisionError("bug")}


@pytest.mark.parametrize("case", list(FAILURES))
def test_analysis_failure_keeps_status_raw_data_and_exit_code(home, rigs, monkeypatch, tmp_path, case):
    analyze, failure = stage2.analyze_after_run, FAILURES[case]
    if isinstance(failure, str):
        monkeypatch.setattr(stage2, "ANALYSIS_SCRIPT", fake_09(tmp_path, failure))
    elif failure is None:
        monkeypatch.setattr(stage2, "ANALYSIS_SCRIPT", tmp_path / "missing_09.py")
    elif case == "callable_raises":
        def analyze(raw_dir, log):
            raise failure
    else:
        def boom(*args, **kwargs):
            raise failure
        monkeypatch.setattr(stage2.subprocess, "run", boom)
    status, tally = stage2.run(rig_cfg(stage2, home, duration_minutes=30), mock=True, output_dir=home / "run",
                               rig_factory=rigs, analyze=analyze)
    assert status == "COMPLETE" and tally["saved"] == 3
    assert list(digests(home / "run/nmr_raw")) == [f"stage_2_{i:04d}.dx" for i in (1, 2, 3)]
    assert rigs.made[0].pump.contents_ml == 0 and saved(home) == (0, 0) and rigs.made[0].returned_home
    log = (home / "run/log.txt").read_text(encoding="utf-8")
    marker = "Analysis skipped (Ctrl+C)" if case == "ctrl_c" else "ANALYSIS FAILED"
    assert log.index("Serial ports closed") < log.index("COMPLETE | slots 3") < log.index(marker)
    if case != "callable_raises":
        assert "09_analyze_stage2only_scipy.py" in log.split(marker)[1]  # The command to run it later.


def test_cli_exit_code_follows_the_physical_run_when_analysis_fails(home, monkeypatch, tmp_path):
    cfg = {"base_config": "configs/experiments/21_stage2only.yaml", "experiment": {"duration_minutes": 15},
           "arduino": {"position_file": str(home / "simple_needle_position.txt")}}
    (home / "c15.yaml").write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(stage2, "ANALYSIS_SCRIPT", fake_09(tmp_path, "sys.exit(1)\n"))
    assert stage2.main(["--mock", "--config", str(home / "c15.yaml"), "--output-dir", str(home / "run")]) == 0
    assert "ANALYSIS FAILED" in (home / "run/log.txt").read_text(encoding="utf-8")


def test_ctrl_c_during_the_run_never_starts_analysis(home, rigs):
    recorder = Recorder()
    status, tally = stage2.run(rig_cfg(stage2, home, duration_minutes=30), mock=True, output_dir=home / "run",
                               clock=CtrlCAtSecondSlot(), rig_factory=rigs, analyze=recorder)
    assert status == "INTERRUPTED" and tally["saved"] == 1 and not recorder.calls
    assert "Analysis later" in (home / "run/log.txt").read_text(encoding="utf-8")


def shifted(data, minutes):
    """The tracked fixture re-stamped `minutes` later, as a second real acquisition would be."""
    stamp = re.search(rb"##LONG DATE=2026/08/10 17:(\d\d):21-0400", data)
    return data.replace(stamp[0], b"##LONG DATE=2026/08/10 17:%02d:21-0400" % (int(stamp[1]) + minutes), 1)


@pytest.mark.parametrize("count", [1, 2])
def test_real_09_processing_writes_results_and_labelled_pngs(tmp_path, count):
    raw = tmp_path / "21_stage2only_20261021_170000/nmr_raw"
    raw.mkdir(parents=True)
    for i in range(count):
        (raw / f"stage_2_{i + 1:04d}.dx").write_bytes(shifted(FIXTURE.read_bytes(), 15 * i))
    before = digests(raw)
    clock = SimulatedClock()
    log = make_logger(raw.parent, clock, "analysis_test")
    try:
        code = stage2.analyze_after_run(raw, log)
    finally:
        close_logger(log)
    assert code == 0, (raw.parent / "log.txt").read_text(encoding="utf-8")
    assert digests(raw) == before
    [out] = (raw.parent / "analysis_stage2only_scipy").iterdir()
    plots = out / "stage_2/plots"
    names = [f"observations/obs_{i + 1:04d}_full_spectrum.png" for i in range(count)] + ["area_vs_time.png"]
    assert all((plots / name).is_file() for name in names)
    with (out / "stage_2/target_peak_measurements.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    assert [r["file"] for r in rows] == [f"stage_2_{i + 1:04d}.dx" for i in range(count)]
    assert [float(r["elapsed_hours"]) for r in rows] == pytest.approx([.25 * i for i in range(count)])
    assert all(r["timestamp_source"] == "LONG DATE header" for r in rows)
    with Image.open(plots / "observations/obs_0001_full_spectrum.png") as im:
        assert im.info["Title"] == "21_stage2only_20261021_170000 Stage 2 obs_0001 Full Processed Spectrum"
    assert "stage_2_0001.dx" in (plots / "observations/obs_0001_full_spectrum.svg").read_text(encoding="utf-8")
    text = (raw.parent / "log.txt").read_text(encoding="utf-8")
    assert "Analysis COMPLETE" in text and str(plots) in text
