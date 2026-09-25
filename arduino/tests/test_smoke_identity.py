"""The standalone USB smoke check must never dispatch a motor command."""

import importlib.util
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace


def load_smoke():
    path = Path(__file__).resolve().parents[2] / "smoke_test" / "03_smoke_arduino.py"
    spec = importlib.util.spec_from_file_location("arduino_smoke_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_simulated_smoke(monkeypatch, *, startup_ready: str | None, reported_version: str):
    instances = []

    class SimulatedSerial:
        def __init__(self, **_kwargs):
            self.lines = deque()
            if startup_ready is not None:
                self.lines.append(startup_ready.encode("ascii") + b"\n")
            self.writes = []
            instances.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def readline(self):
            return self.lines.popleft() if self.lines else b""

        def write(self, payload):
            self.writes.append(payload)
            if payload == b"1 PING\n":
                self.lines.extend((b"EVENT HOST_CONNECTED\n", b"ACK 1 PING\n", b"DONE 1 PONG\n"))
            elif payload == b"2 IDENTITY\n":
                self.lines.extend((
                    b"ACK 2 IDENTITY\n",
                    (f"DONE 2 device=needle_controller board=uno_r4_minima "
                     f"version={reported_version} driver=DM542S\n").encode("ascii"),
                ))
            else:
                raise AssertionError(f"Unexpected or motor-capable command: {payload!r}")

        def flush(self):
            pass

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=SimulatedSerial, SerialException=OSError))
    smoke = load_smoke()
    result = smoke.main(["--port", "COM3", "--startup-seconds", "0.01", "--timeout", "0.05"])
    return result, instances[0]


def test_smoke_checks_ready_ping_and_identity_without_motion(monkeypatch, capsys):
    result, serial = run_simulated_smoke(
        monkeypatch,
        startup_ready="READY device=needle_controller board=uno_r4_minima version=1.2.1",
        reported_version="1.2.1",
    )
    assert result == 0
    assert serial.writes == [b"1 PING\n", b"2 IDENTITY\n"]
    assert "No motor command was sent." in capsys.readouterr().out


def test_smoke_reports_identity_but_fails_if_ready_was_missed(monkeypatch, capsys):
    result, _ = run_simulated_smoke(monkeypatch, startup_ready=None, reported_version="1.2.1")
    assert result == 1
    output = capsys.readouterr().out
    assert "READY: not observed" in output
    assert "IDENTITY: expected device" in output


def test_smoke_rejects_wrong_firmware_version(monkeypatch):
    result, _ = run_simulated_smoke(
        monkeypatch,
        startup_ready="READY device=needle_controller board=uno_r4_minima version=1.2.0",
        reported_version="1.2.0",
    )
    assert result == 1
