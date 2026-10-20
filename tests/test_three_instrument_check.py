"""11 three-instrument check: offline mock tests only; no hardware."""
from contextlib import contextmanager
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
spec = importlib.util.spec_from_file_location("check_entry", ROOT / "scripts/11_3InstrumentCheck.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)
PROTECTED = [ROOT / p for p in ("scripts/10_stage2only.py", "configs/experiments/10_stage2only.yaml",
                                "chemyx_lab/workflows/stage2_demo_instruments.py",
                                "chemyx_lab/workflows/stage2_demo_nmr.py")]
BEFORE = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in PROTECTED}


@pytest.fixture
def cfg():
    value = check.load_check_config()
    value["analysis"]["enabled"] = False
    value["three_instrument_check"]["analyze_spectrum"] = False
    return value


def run(cfg, tmp_path, **kwargs):
    return check.run_check(cfg, mock=True, output_dir=tmp_path / "run", **kwargs)


def capturing(captures, wrap=None):
    @contextmanager
    def factory(*args, **kwargs):
        with check.setup_instruments(*args, **kwargs) as rig:
            if wrap:
                wrap(rig)
            yield rig
            captures.update(pump=list(rig.pump.ser.tx_log), needle=list(rig.controller.transport.tx_log))
    return factory


def test_mock_sequence_pump_channel_volumes_and_single_nmr_start(cfg, tmp_path, capsys):
    captures = {}
    result = run(cfg, tmp_path, rig_factory=capturing(captures))
    assert result["status"] == "COMPLETE"
    pump = [s.strip() for s in captures["pump"]]
    assert [s for s in pump if "set volume" in s] == ["1 set volume -1.0", "1 set volume 1.0"]
    assert [s for s in pump if " start" in s] == ["1 start 0", "1 start 0"]
    assert not any(s.startswith("2 ") for s in pump)
    assert [s for s in pump if "set rate" in s] == ["1 set rate 5.0"] * 2
    calls = result["details"]["nmr_http_calls"]
    assert sum("RunExperiment" in c for c in calls) == 1 and "NumberOfScans=1" in " ".join(calls)
    assert calls.index("PUT /interfaces/iFlow/Settings/1D") < next(i for i, c in enumerate(calls) if "RunExperiment" in c)
    files = list((tmp_path / "run/nmr_raw").glob("*.dx"))
    assert [f.name for f in files] == ["instrument_check_0001.dx"] and files[0].stat().st_size > 0
    assert files[0].read_text().startswith("##")
    assert result["rollup"] == {"Arduino": "PASS", "Chemyx Channel 1": "PASS", "NMR Acquisition": "PASS",
                                "Raw FID Saving": "PASS", "NMR Analysis": "DISABLED"}
    out = capsys.readouterr().out
    order = ["ARDUINO: needle UP", "ARDUINO: needle DOWN", "CHEMYX CH1: withdraw 1 mL", "CHEMYX CH1: infuse 1 mL",
             "ARDUINO: returning needle UP", "NMR: Configuring 1 scan", "Sending START (once)", "Acquisition complete", "Raw .dx file saved"]
    positions = [out.index(item) for item in order]
    assert positions == sorted(positions)
    assert (tmp_path / "run/experiment.log").read_text() == out
    assert (tmp_path / "run/config_used.yaml").is_file() and (tmp_path / "run/summary.json").is_file()


def test_arduino_moves_up_down_up_and_never_sets_home(cfg, tmp_path):
    moves = []
    def wrap(rig):
        original = rig.move_needle
        rig.move_needle = lambda target: (moves.append(target), original(target))[1]
    result = run(cfg, tmp_path, rig_factory=capturing({}, wrap))
    assert moves == [1, 0, 1] and result["status"] == "COMPLETE"
    assert not any("home" in c.lower() and "set" in c.lower() for c in result["details"]["mock_commands"]["arduino"])


def test_nmr_data_only_retrieved_after_completion_and_start_not_repeated(cfg, tmp_path):
    result = run(cfg, tmp_path)
    calls = result["details"]["nmr_http_calls"]
    start = next(i for i, c in enumerate(calls) if "RunExperiment" in c)
    assert calls[start + 1:].count("GET /interfaces/iFlow/ExperimentStatus") >= 3  # incomplete polls precede completion
    assert calls.index("GET /interfaces/Service/Acquire") > start
    assert calls[-1] == "GET /interfaces/iFlow/ExperimentStatus"  # raw FID read only after completion


def test_nmr_failure_identified_and_start_never_repeated(cfg, tmp_path):
    cfg["nmr"]["acquisition_timeout_seconds"] = 20
    client = check.MockNmrClient("##x\n##END=", never_complete=True)
    result = run(cfg, tmp_path, nmr_client=client)
    assert result["status"] == "FAILED" and result["failed_step"] == "nmr_acquisition"
    assert sum("RunExperiment" in c for c in client.calls) == 1
    assert result["rollup"]["NMR Acquisition"] == "FAIL" and result["rollup"]["Arduino"] == "PASS"


def test_missing_raw_fid_is_not_reported_as_saved(cfg, tmp_path):
    client = check.MockNmrClient("not a jcamp payload")
    cfg["nmr"]["acquisition_timeout_seconds"] = 20
    result = run(cfg, tmp_path, nmr_client=client)
    assert result["failed_step"] == "raw_saving" and result["rollup"]["NMR Acquisition"] == "PASS"
    assert result["rollup"]["Raw FID Saving"] == "FAIL"


@pytest.mark.parametrize("failing,expected_step", [("move_needle", "arduino_up"), ("transfer", "chemyx_withdraw")])
def test_physical_failure_names_step_and_stops_dependents(cfg, tmp_path, failing, expected_step, capsys):
    def wrap(rig):
        def boom(*_, **__):
            raise RuntimeError(f"{failing} no reply")
        setattr(rig, failing, boom)
    acquired = []
    result = run(cfg, tmp_path, rig_factory=capturing({}, wrap),
                 acquisition=lambda *a, **k: acquired.append(1))
    assert result["status"] == "FAILED" and result["failed_step"] == expected_step
    assert acquired == [] and result["steps"]["nmr_acquisition"] == "NOT_RUN"
    assert result["steps"]["chemyx_infuse"] == "NOT_RUN" and result["steps"]["arduino_return_up"] == "NOT_RUN"
    assert "FAILED" in capsys.readouterr().out


def test_connection_failure_identified(cfg, tmp_path):
    @contextmanager
    def broken(*_, **__):
        raise OSError("COM3 unavailable")
        yield
    result = run(cfg, tmp_path, rig_factory=broken)
    assert result["status"] == "FAILED" and result["failed_step"] == "connect" and "COM3" in result["error"]


def test_analysis_runs_on_saved_dx_and_cannot_alter_hardware_results(tmp_path):
    cfg = check.load_check_config()
    result = check.run_check(cfg, mock=True, output_dir=tmp_path / "run")
    assert result["steps"]["analysis"] == "PASS", result["details"]
    assert (tmp_path / "run/analysis/instrument_check_0001.png").is_file()
    assert run_dir_title(tmp_path / "run")  # dataset identity recorded in plot manifest
    # A crashing analyzer never changes the hardware verdicts.
    boom = check.run_check(cfg, mock=True, output_dir=tmp_path / "run2", analyzer=lambda *_: {"status": "FAIL", "detail": "x"})
    assert boom["rollup"]["Arduino"] == boom["rollup"]["NMR Acquisition"] == "PASS" and boom["status"] == "FAILED"


def run_dir_title(run):
    return "3instrument_check" in (run.name) or run.name in (run / "analysis/_work/analysis/plot_manifest.csv").read_text()


def test_config_in_memory_only_and_channel_two_disabled(cfg, tmp_path):
    assert cfg["nmr"]["scans"] == 1 and cfg["channel_2"]["enabled"] is False
    base = yaml.safe_load((ROOT / "configs/experiments/10_stage2only.yaml").read_text())
    assert base["nmr"]["scans"] == 8
    data = yaml.safe_load((ROOT / "configs/experiments/11_3InstrumentCheck.yaml").read_text())
    data["three_instrument_check"]["chemyx_channel"] = 2
    bad = tmp_path / "bad.yaml"
    bad.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        check.load_check_config(bad)


def test_no_ownership_or_history_and_10_unmodified(cfg, tmp_path):
    source = (ROOT / "scripts/11_3InstrumentCheck.py").read_text()
    assert not any(word in source.lower() for word in ("ownership", "ledger", "dose_guard", "set_home"))
    (tmp_path / "old.dx").write_bytes(b"historical")
    run(cfg, tmp_path)
    with pytest.raises(FileExistsError):
        run(cfg, tmp_path)
    assert {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in PROTECTED} == BEFORE


def test_mock_cli_cannot_open_network_or_real_serial(tmp_path):
    code = """import sys, socket
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('real network forbidden'))
import serial
serial.Serial=lambda *a,**k: (_ for _ in ()).throw(AssertionError('real serial forbidden'))
import runpy
sys.path.insert(0,'scripts')
sys.argv=['11_3InstrumentCheck.py','--mock','--output-dir',sys.argv[1]]
runpy.run_path('scripts/11_3InstrumentCheck.py',run_name='__main__')
"""
    proc = subprocess.run([sys.executable, "-c", code, str(tmp_path / "cli")], cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "THREE-INSTRUMENT CHECK COMPLETE" in proc.stdout
    summary = json.loads((tmp_path / "cli/summary.json").read_text())
    assert summary["status"] == "COMPLETE" and summary["mode"] == "MOCK"


def test_script_runs_directly_from_another_cwd(tmp_path):
    proc = subprocess.run([sys.executable, str(ROOT / "scripts/11_3InstrumentCheck.py"), "--mock", "--output-dir", str(tmp_path / "direct")],
                          cwd=tmp_path, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, proc.stdout + proc.stderr
