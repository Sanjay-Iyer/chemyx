"""Script 26: manual Chemyx withdraw / infuse / stop / status with no saved state (mock only)."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.workflows.simple_rig import SimulatedClock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("chemyxmanual_26_test", ROOT / "scripts/26_chemyxmanual.py")
s26 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s26)
RECORD, RECORDING = [], [False]


def _audit(event, args):
    if RECORDING[0] and event == "open" and not isinstance(args[0], int):
        mode, flags = args[1], args[2] or 0
        writing = any(c in mode for c in "wax+") if mode else bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND))
        RECORD.append(("write" if writing else "read", str(args[0])))
    elif RECORDING[0] and event in ("os.rename", "os.replace", "os.mkdir", "os.remove"):
        RECORD.append(("write", str(args[0])))


sys.addaudithook(_audit)


@pytest.fixture
def sent(monkeypatch):
    """Every command the real Pump driver sends (channel-prefixed), in order."""
    commands = []
    original = Pump.send_command

    def recorded(self, command, channel=None):
        commands.append(self._with_channel(command, channel).strip())
        return original(self, command, channel)
    monkeypatch.setattr(Pump, "send_command", recorded)
    return commands


def main(*args, answer="y"):
    return s26.main([*args, "--mock"], input_fn=lambda prompt: answer)


def volumes(commands, channel):
    return [float(c.split()[-1]) for c in commands if c.startswith(f"{channel} set volume")]


def test_infuse_and_withdraw_send_only_that_move_after_one_stop(sent):
    assert main("infuse", "--channel", "1", "--volume", "3.9") == 0
    assert volumes(sent, 1) == [3.9] and [c for c in sent if c.endswith("stop")] == ["1 stop", "1 stop"]
    assert [c for c in sent if c.startswith("1 set rate")] == ["1 set rate 5.0"]
    sent.clear()
    assert main("withdraw", "--channel", "2", "--volume", "0.5", "--rate", "0.8") == 0
    assert volumes(sent, 2) == [-0.5] and "2 set rate 0.8" in sent and not [c for c in sent if c.startswith("1 ")]


def test_stop_and_status(sent):
    assert main("stop") == 0 and sent == ["1 stop", "2 stop"]
    sent.clear()
    assert main("stop", "--channel", "2") == 0 and sent == ["2 stop"]
    sent.clear()
    assert main("status") == 0 and sent == ["1 help"]  # No motion and no STOP.


def test_declined_or_bad_arguments_never_contact_the_pump(sent):
    assert main("infuse", "--channel", "1", "--volume", "2", answer="n") == 3
    assert main("infuse", "--channel", "1", "--volume", "25", answer="y") == 2  # Bigger than the 20 mL syringe.
    assert main("withdraw", "--channel", "1", "--volume", "0") == 2
    assert main("infuse", "--channel", "both", "--volume", "1") == 2
    assert main("infuse", "--channel", "1", "--volume", "1", "--rate", "-1") == 2
    assert sent == []


class CtrlCHalfway(SimulatedClock):
    def sleep(self, seconds):
        if seconds > 10:
            super().sleep(seconds / 2)
            raise KeyboardInterrupt
        super().sleep(seconds)


def test_ctrl_c_mid_move_sends_stop_and_reports_about_how_much_moved(sent, capsys):
    code = s26.run("infuse", s26.load_config(), mock=True, channels=[1], volume=4.0, rate=5.0, yes=True,
                   clock=CtrlCHalfway())
    assert code == 130 and sent[-1] == "1 stop"
    assert "About 2.08 of 4 mL was infused" in capsys.readouterr().out  # 25 s (half of 48 + 2 s) at 5 mL/min.


def test_pump_error_stops_and_fails(sent, monkeypatch):
    monkeypatch.setattr(Pump, "start", lambda self, channel=None, delay=None: "error: syringe stalled")
    assert main("withdraw", "--channel", "1", "--volume", "1") == 1
    assert sent[-1] == "1 stop"


def test_never_blocked_by_earlier_state_and_writes_nothing(tmp_path, monkeypatch, sent):
    """An UNKNOWN chemyx_control record, an old dose record and a stopped run change nothing for 26."""
    state = tmp_path / "state"
    monkeypatch.setenv("CHEMYX_POSITION_STATE_DIR", str(state))
    run = lambda *a: subprocess.run([sys.executable, "scripts/chemyx_control.py", *a, "--mock"], cwd=ROOT,
                                    env=dict(os.environ), stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
    assert run("set-home", "--channel", "1").returncode == 0
    assert run("stop", "--channel", "1").returncode == 0  # chemyx_control now refuses every move on Channel 1 ...
    assert "REFUSED" in run("infuse", "--channel", "1", "--volume", "1").stdout
    before = sorted(p for p in state.rglob("*") if p.is_file())
    RECORD.clear()
    RECORDING[0] = True
    try:
        assert main("infuse", "--channel", "1", "--volume", "1") == 0  # ... 26 does not.
        assert main("stop") == 0
        assert main("withdraw", "--channel", "1", "--volume", "1") == 0
    finally:
        RECORDING[0] = False
    assert not [p for kind, p in RECORD if kind == "write" and "__pycache__" not in p]
    assert not [p for kind, p in RECORD if kind == "read" and str(state) in p]
    assert sorted(p for p in state.rglob("*") if p.is_file()) == before


def test_cli_needs_live_or_mock_and_asks_once(tmp_path):
    proc = subprocess.run([sys.executable, "scripts/26_chemyxmanual.py", "infuse", "--channel", "1", "--volume", "1",
                           "--mock"], cwd=ROOT, input="y\n", capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0 and proc.stdout.count("Start this move? [y/N]:") == 1, proc.stdout + proc.stderr
    assert "syringe holds about" not in proc.stdout  # 26 tracks no contents, so it shows none.
    none = subprocess.run([sys.executable, "scripts/26_chemyxmanual.py", "status"], cwd=ROOT,
                          capture_output=True, text=True, timeout=60)
    assert none.returncode == 2  # Must choose --live or --mock.
