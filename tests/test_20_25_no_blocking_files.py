"""Scripts 20-25 leave no blocking files: Ctrl+C during any motion, then an immediate fresh run (mock only).

Every file write is recorded with a Python audit hook. An interrupted run may only
leave log.txt, saved .dx spectra and (three instruments) the two needle integers.
The next run must complete without reading anything from the interrupted run.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

import pytest

from arduino.python.controller import NeedleController
from chemyx_lab.workflows.simple_rig import SimulatedClock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
FILES = {20: "20_stage1stage2.py", 21: "21_stage2only.py", 22: "22_3instrumentquickcheck.py",
         23: "23_stage1stage2.py", 24: "24_stage2only.py", 25: "25_2instrumentquickcheck.py"}


def load(number):
    spec = importlib.util.spec_from_file_location(f"no_blocking_{number}", ROOT / "scripts" / FILES[number])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCRIPTS = {number: load(number) for number in FILES}
RECORD = []
RECORDING = [False]


def _audit(event, args):
    if not RECORDING[0]:
        return
    try:
        if event == "open" and not isinstance(args[0], int):
            mode, flags = args[1], args[2] or 0
            writing = any(c in mode for c in "wax+") if mode else bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND))
            RECORD.append(("write" if writing else "read", str(args[0])))
        elif event in ("os.rename", "os.replace"):
            RECORD.append(("write", str(args[1])))
        elif event in ("os.mkdir", "os.remove", "os.rmdir", "os.truncate"):
            RECORD.append(("write", str(args[0])))
        elif event == "subprocess.Popen":
            RECORD.append(("spawn", str(args[0])))
    except Exception:
        pass


sys.addaudithook(_audit)  # Records only while a test sets RECORDING.


class CtrlC(SimulatedClock):
    """Ctrl+C halfway through matching waits: [(seconds, occurrence), ...] fired in order."""
    def __init__(self, targets):
        super().__init__()
        self.targets, self.seen = list(targets), {}

    def sleep(self, seconds):
        if self.targets:
            target, nth = self.targets[0]
            if seconds == pytest.approx(target):
                self.seen[target] = self.seen.get(target, 0) + 1
                if self.seen[target] == nth:
                    self.targets.pop(0)
                    super().sleep(seconds / 2)
                    raise KeyboardInterrupt
        super().sleep(seconds)


def wait(volume, rate=5.0):
    return volume / rate * 60 + 2  # Timed transfer + 2 s extra before STOP.


NMR = 80  # The mock NMR's acquisition wait.
SCENARIOS = [
    (20, "needle_move", "jog", 2), (20, "ch1_withdraw", [(wait(8), 1)], None), (20, "nmr", [(NMR, 1)], None),
    (20, "ch1_return", [(wait(13), 1)], None), (20, "ch2_dose", [(wait(1.86, 1.0), 1)], None),
    (21, "needle_move", "jog", 2), (21, "ch1_withdraw", [(wait(8), 1)], None), (21, "nmr", [(NMR, 1)], None),
    (21, "ch1_return", [(wait(13), 1)], None),
    (21, "withdraw_then_ctrl_c_again_while_emptying", [(wait(8), 1), (wait(3.916), 1)], None),
    (22, "needle_move", "jog", 1), (22, "ch1_withdraw", [(wait(1), 1)], None), (22, "ch1_infuse", [(wait(1), 2)], None),
    (22, "nmr", [(NMR, 1)], None),
    (23, "ch1_withdraw", [(wait(5), 1)], None), (23, "nmr", [(NMR, 1)], None), (23, "ch1_infuse", [(wait(5.2), 1)], None),
    (23, "ch2_dose", [(wait(1.86, 1.0), 1)], None),
    (24, "ch1_withdraw", [(wait(5), 1)], None), (24, "nmr", [(NMR, 1)], None), (24, "ch1_infuse", [(wait(5.2), 1)], None),
    (24, "nmr_then_ctrl_c_again_during_the_return", [(NMR, 1), (wait(5.2), 1)], None),
    (25, "ch1_withdraw", [(wait(1), 1)], None), (25, "nmr", [(NMR, 1)], None), (25, "ch1_infuse", [(wait(1), 2)], None),
]


def config(number, home):
    cfg = SCRIPTS[number].load_config()
    if number in (20, 21, 22):
        cfg["arduino"]["position_file"] = str(home / "simple_needle_position.txt")  # Mock -> *_mock.txt here.
    if number in (20, 23):
        cfg["stages"]["stage_1"].update(duration_minutes=240, interval_minutes=120)
        cfg["stages"]["stage_2"].update(duration_minutes=30, interval_minutes=30)
    if number == 21:
        cfg["experiment"]["duration_minutes"] = 30
    if number == 24:
        cfg["experiment"]["observation_count"] = 2
    return cfg


def unexpected(path, home):
    """None for the files an interrupted run may leave; otherwise a description."""
    p = Path(path)
    if "__pycache__" in p.parts or p.suffix == ".pyc":
        return None
    try:
        rel = p.resolve().relative_to(home)
    except ValueError:
        return f"outside the run folders: {p}"
    if rel == Path(".") or rel.name in {"log.txt", "nmr_raw", "stage_1", "stage_2", "interrupted", "rerun"} \
            or p.suffix == ".dx" or rel.name.startswith(("simple_needle_position_mock.txt", "simple_needle_steps_mock.txt")):
        return None
    return str(rel)


@pytest.mark.parametrize("number,motion,how,nth", SCENARIOS, ids=[f"{n}-{m}" for n, m, _, _ in SCENARIOS])
def test_ctrl_c_mid_motion_leaves_no_blocking_files(tmp_path, monkeypatch, number, motion, how, nth):
    home, script = tmp_path.resolve(), SCRIPTS[number]
    clock = SimulatedClock()
    if how == "jog":
        original, calls = NeedleController.jog, []

        def jog(self, steps, speed):
            calls.append(steps)
            if len(calls) == nth:
                raise KeyboardInterrupt  # Ctrl+C while the Arduino is moving.
            return original(self, steps, speed)
        monkeypatch.setattr(NeedleController, "jog", jog)
    else:
        clock = CtrlC(how)
    RECORD.clear()
    RECORDING[0] = True
    try:
        status, results = script.run(config(number, home), mock=True, output_dir=home / "interrupted", clock=clock)
    finally:
        RECORDING[0] = False
    monkeypatch.undo()
    assert status in ("INTERRUPTED", False), status  # 22 returns passed=False with its part results.
    if number == 22:
        assert "INTERRUPTED" in results.values()
    writes = [path for kind, path in RECORD if kind == "write"]
    assert [unexpected(p, home) for p in writes if unexpected(p, home)] == []
    assert not [path for kind, path in RECORD if kind == "spawn"]
    left = sorted(p.relative_to(home).as_posix() for p in (home / "interrupted").rglob("*") if p.is_file())
    assert left and all(name.endswith(("log.txt", ".dx")) for name in left), left
    RECORD.clear()
    RECORDING[0] = True
    try:
        again = script.run(config(number, home), mock=True, output_dir=home / "rerun", clock=SimulatedClock())
    finally:
        RECORDING[0] = False
    assert again[0] in ("COMPLETE", True), again
    if number in (22, 25):
        assert set(again[1].values()) == {"PASS"}
    old = str(home / "interrupted")
    assert not [path for kind, path in RECORD if kind == "read" and path.startswith(old)]
