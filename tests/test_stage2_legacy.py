"""12/13: actual legacy driver, HTTP transport fakes, unchanged physical recipes."""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from urllib import error

import pytest
import yaml

from chemyx_lab.instruments import nmr
from chemyx_lab.instruments.nmr import NmrRpcClient, NmrRpcConfig
from chemyx_lab.workflows import stage2_legacy_nmr as legacy
from test_stage2_demo_nmr import HTTP, RAW, ONE_D, SETTINGS, START, STATUS

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

full = load("legacy_full_test", "12_stage2only.py")
short = load("legacy_short_test", "13_3InstrumentCheck.py")
PROTECTED = ["scripts/10_stage2only.py", "scripts/11_3InstrumentCheck.py",
    "chemyx_lab/workflows/nmr_standalone.py", "chemyx_lab/workflows/stage2_demo_nmr.py",
    "chemyx_lab/workflows/stage2_demo_instruments.py", "chemyx_lab/instruments/nmr.py",
    "chemyx_lab/analysis/stage2_demo_analysis.py", "configs/experiments/10_stage2only.yaml",
    "configs/experiments/11_3InstrumentCheck.yaml"]
BEFORE = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in PROTECTED}


class LegacyHTTP(HTTP):
    def accept(self, settings):
        self.counter += 1
        self.current = settings
        return {"ResultCode": 0}  # No receipt identity is needed.

    def complete(self, *, raw=None, **extra):
        if raw is None:
            raw = RAW.replace("Demo", f"Fresh {self.counter}") if self.current else ""
        return {"ResultCode": 0, "NumberOfScansRun": -999,
                "JDX_FileContents_TD": raw, **extra}  # Scan count is not a gate.


@pytest.fixture
def clock(monkeypatch):
    clock = full.demo.SimulatedClock()
    monkeypatch.setattr(nmr, "time", SimpleNamespace(monotonic=clock.now, sleep=clock.sleep))
    return clock


@pytest.fixture
def http(monkeypatch):
    return LegacyHTTP(monkeypatch)


@pytest.fixture
def cfg():
    cfg = full.load_config()
    cfg["analysis"]["enabled"] = False
    return cfg


def acquire(tmp_path, clock, cfg, *, state=None, filename="one.dx", client=None, emit=lambda _: None):
    config = dict(cfg["nmr"], acquisition_timeout_seconds=5, poll_seconds=1, http_timeout_seconds=2)
    return legacy.legacy_acquire(config, tmp_path / filename, state={} if state is None else state,
        client=client or NmrRpcClient(NmrRpcConfig(host="example.invalid")), now=clock.now, sleep=clock.sleep, emit=emit)


def test_actual_http_sequence_uses_existing_wait_and_no_new_completion_checks(tmp_path, clock, cfg, http, monkeypatch):
    waits = []
    original = NmrRpcClient.wait_for_idle
    def wait(client, **kwargs):
        waits.append(kwargs)
        return original(client, **kwargs)
    monkeypatch.setattr(NmrRpcClient, "wait_for_idle", wait)
    result = acquire(tmp_path, clock, cfg)
    assert result["status"] == "NMR_SAVED" and not result["unresolved_start"]
    assert http.commands == [("GET", STATUS), ("GET", ONE_D), ("GET", SETTINGS),
                             ("PUT", ONE_D), ("PUT", START), ("GET", STATUS)]
    assert len(waits) == len(http.starts) == 1
    assert http.starts[0]["NumberOfScans"] == 8 and http.starts[0]["ReceiverGain"] == 12
    assert http.starts[0]["SpectralCentreInPpm"] == 5 and http.starts[0]["SpectralWidthInPpm"] == 20
    assert Path(result["path"]).read_bytes() == RAW.replace("Demo", "Fresh 1").encode()


def test_busy_baseline_is_informational_and_does_not_authorize_start(tmp_path, clock, cfg, http):
    http.returns("GET", STATUS, {"ResultCode": 1, "JDX_FileContents_TD": RAW}, lambda _: http.complete())
    assert acquire(tmp_path, clock, cfg)["status"] == "NMR_SAVED"
    assert len(http.starts) == 1


def test_real_legacy_wait_polls_nonzero_code_then_completes(tmp_path, clock, cfg, http):
    http.returns("GET", STATUS, {"ResultCode": 0}, {"ResultCode": 2}, lambda _: http.complete())
    assert acquire(tmp_path, clock, cfg)["status"] == "NMR_SAVED"
    assert clock.now() == 1 and len(http.starts) == 1


@pytest.mark.parametrize("status", [{"Running": False}, {"PercentComplete": 100}, {"Running": False, "ResultCode": 99}])
def test_legacy_boolean_percent_and_precedence_are_preserved(tmp_path, clock, cfg, http, status):
    http.returns("GET", STATUS, {"ResultCode": 0}, dict(status, JDX_FileContents_TD=RAW))
    assert acquire(tmp_path, clock, cfg)["status"] == "NMR_SAVED"


@pytest.mark.parametrize("status", [None, {}, {"ResultCode": "0"}, {"Unexpected": "done"},
                                     {"PercentComplete": float("nan")}, {"PercentComplete": float("inf")}])
def test_unknown_parser_return_never_proves_success_or_permits_another_start(tmp_path, clock, cfg, http, status):
    response = dict(status, JDX_FileContents_TD=RAW) if isinstance(status, dict) else status
    http.returns("GET", STATUS, {"ResultCode": 0}, response)
    state = {}
    result = acquire(tmp_path, clock, cfg, state=state)
    assert result["status"] != "NMR_SAVED" and state["pending"]
    acquire(tmp_path, clock, cfg, state=state, filename="two.dx")
    assert len(http.starts) == 1 and not (tmp_path / "two.dx").exists()


@pytest.mark.parametrize("raw", ["", RAW.replace("NMR FID", "NMR SPECTRUM"),
    "##TITLE=empty\n##DATA TYPE=NMR FID\n##END=\n", RAW.replace("##END=", "##END NTUPLES=")])
def test_missing_or_invalid_raw_is_not_saved_and_keeps_start_pending(tmp_path, clock, cfg, http, raw):
    http.returns("GET", STATUS, {"ResultCode": 0}, {"ResultCode": 0, "JDX_FileContents_TD": raw})
    state = {}
    result = acquire(tmp_path, clock, cfg, state=state)
    assert result["status"] == "NMR_MISSING" and state["pending"]
    assert not (tmp_path / "one.dx").exists()


def test_stale_baseline_blocks_new_start_until_fresh_prior_data_is_returned(tmp_path, clock, cfg, http):
    http.returns("GET", STATUS, {"ResultCode": 0, "JDX_FileContents_TD": RAW})
    state = {}
    first = acquire(tmp_path, clock, cfg, state=state)
    assert first["status"] == "NMR_FAILED" and "unchanged" in first["reason"]
    acquire(tmp_path, clock, cfg, state=state, filename="two.dx")
    assert len(http.starts) == 1
    http.routes.pop(("GET", STATUS))
    third = acquire(tmp_path, clock, cfg, state=state, filename="three.dx")
    assert third["status"] == "NMR_SAVED" and len(http.starts) == 2
    assert third["recovered_previous"]["path"] == str(tmp_path / "one.dx")


def test_delayed_raw_is_reported_missing_then_recovered_without_duplicate_start(tmp_path, clock, cfg, http):
    http.returns("GET", STATUS, {"ResultCode": 0})
    state = {}
    assert acquire(tmp_path, clock, cfg, state=state)["status"] == "NMR_MISSING"
    assert len(http.starts) == 1
    http.routes.pop(("GET", STATUS))
    second = acquire(tmp_path, clock, cfg, state=state, filename="two.dx")
    assert len(http.starts) == 2 and second["recovered_previous"]["status"] == "NMR_SAVED"


@pytest.mark.parametrize("failure", ["timeout", "lost_start", "status_http"])
def test_uncertain_start_or_completion_never_repeats_start(tmp_path, clock, cfg, http, failure):
    if failure == "lost_start":
        def lost(settings):
            http.accept(settings)
            raise error.URLError("START reply lost")
        http.returns("PUT", START, lost)
    http.returns("GET", STATUS, {"ResultCode": 0},
                 error.URLError("status timeout") if failure == "status_http" else {"ResultCode": 1})
    state = {}
    first = acquire(tmp_path, clock, cfg, state=state)
    assert first["status"] == "NMR_FAILED" and state["pending"]
    second = acquire(tmp_path, clock, cfg, state=state, filename="two.dx")
    assert not second["requested"] and len(http.starts) == 1


def test_output_and_raw_io_failures_preserve_data_and_do_not_replay_start(tmp_path, clock, cfg, http, monkeypatch):
    original = Path.open
    def denied(path, mode="r", *args, **kwargs):
        if mode == "x":
            raise PermissionError("cannot save")
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    state = {}
    result = acquire(tmp_path, clock, cfg, state=state)
    assert result["status"] == "NMR_FAILED" and "cannot save" in result["reason"]
    assert not state["pending"] and len(http.starts) == 1


def test_full_25_slot_default_mock_and_exact_physical_commands(cfg, tmp_path, capsys):
    clock = full.demo.SimulatedClock()
    client = legacy.MockLegacyClient(full.demo.FIXTURE.read_text(), clock)
    commands = {}
    @contextmanager
    def rig(*args, **kwargs):
        with full.demo.setup_instruments(*args, **kwargs) as value:
            yield value
            commands.update(pump=list(value.pump.ser.tx_log), needle=list(value.controller.transport.tx_log))
    result = full.run_experiment(cfg, mock=True, clock=clock, client=client, output_dir=tmp_path / "run", rig_factory=rig)
    assert result["status"] == "COMPLETE" and result["nmr_saved"] == result["attempted_observations"] == 25
    assert [row["scheduled_seconds"] for row in result["observations"]] == list(range(0, 21601, 900))
    assert result["elapsed_seconds"] == 21600 + 532
    assert [s.strip() for s in commands["pump"] if "set volume" in s] == [
        "1 set volume -8.0", "1 set volume -5.0", "1 set volume 13.0", "1 set volume -5.0", "1 set volume 5.0"] * 25
    assert not any(s.startswith("2 ") for s in commands["pump"])
    starts = [payload for method, path, payload in client.calls if (method, path) == ("PUT", START)]
    assert len(starts) == 25 and all(payload["NumberOfScans"] == 8 for payload in starts)
    raw = list((tmp_path / "run/nmr_raw").glob("stage_2_*.dx"))
    assert len(raw) == 25 and len({p.read_bytes() for p in raw}) == 25
    assert all(row["method"] == "legacy_simple" for row in result["observations"])
    assert "Service/Acquire" not in str(client.calls)
    output = capsys.readouterr().out
    assert "LEGACY NMR METHOD" in output and "OBSERVATION 25 / 25" in output and "Raw .dx saved" in output
    snapshot = yaml.safe_load((tmp_path / "run/config_used.yaml").read_text())
    assert snapshot["analysis"]["dataset_display_name"] == "run"
    assert "dataset_display_name" not in cfg["analysis"]


def test_short_check_same_physical_sequence_one_scan_and_same_helper(tmp_path, capsys):
    cfg = short.load_check_config()
    cfg["three_instrument_check"]["analyze_spectrum"] = False
    clock = short.check.demo.SimulatedClock()
    client = legacy.MockLegacyClient(full.demo.FIXTURE.read_text(), clock)
    result = short.run_check(cfg, mock=True, clock=clock, client=client, output_dir=tmp_path / "run")
    assert result["status"] == "COMPLETE" and all(value == "PASS" for key,value in result["rollup"].items() if key != "NMR Analysis")
    tx = result["details"]["mock_commands"]["chemyx"]
    assert [s for s in tx if "set volume" in s] == ["1 set volume -1.0", "1 set volume 1.0"]
    assert not any(s.startswith("2 ") for s in tx)
    assert len([p for m,p,_ in client.calls if (m,p) == ("PUT", START)]) == 1
    assert next(body for method,path,body in client.calls if (method,path) == ("PUT", START))["NumberOfScans"] == 1
    assert short.LegacyAcquisition is full.LegacyAcquisition
    assert "LEGACY NMR METHOD" in capsys.readouterr().out


def test_short_missing_raw_is_not_a_pass(tmp_path, clock, http):
    cfg = short.load_check_config()
    cfg["three_instrument_check"]["analyze_spectrum"] = False
    http.returns("GET", STATUS, {"ResultCode": 0})
    result = short.run_check(cfg, mock=True, clock=clock, client=NmrRpcClient(NmrRpcConfig()), output_dir=tmp_path / "run")
    assert result["status"] == "FAILED" and result["rollup"]["NMR Acquisition"] == "FAIL"
    assert len(http.starts) == 1


def test_configs_are_physical_copies_with_separate_defaults(cfg):
    old = full.demo.load_config()
    new = deepcopy(cfg)
    new["analysis"]["enabled"] = True
    old["experiment"]["name"] = new["experiment"]["name"]
    assert new == old
    short_cfg = short.load_check_config()
    assert short_cfg["nmr"]["scans"] == 1 and not short_cfg["channel_2"]["enabled"]


@pytest.mark.parametrize("failure", ["launch", "crash", "bad_json"])
def test_analysis_cannot_control_schedule_or_optional_dose(cfg, tmp_path, failure):
    cfg["analysis"]["enabled"] = True
    cfg["channel_2"]["enabled"] = True
    dose_times = []
    clock = full.demo.SimulatedClock()
    @contextmanager
    def rig(*args, **kwargs):
        with full.demo.setup_instruments(*args, **kwargs) as value:
            sample, dose = value.sample, value.dose
            def sampled(callback):
                sample(callback)
                if not dose_times:
                    dose_times.append(clock.now())
            def dosed():
                dose_times.append(clock.now())
                dose()
            value.sample, value.dose = sampled, dosed
            yield value
    def analysis(run, *args):
        if failure == "launch": raise ImportError("science unavailable")
        (run / "analysis").mkdir()
        (run / "analysis/analysis_summary.json").write_text("{bad" if failure == "bad_json" else '{}')
    result = full.run_experiment(cfg, mock=True, clock=clock, output_dir=tmp_path / "run", rig_factory=rig, analysis_launcher=analysis)
    assert result["status"] == "COMPLETE" and result["nmr_saved"] == 25 and result["channel_2_status"] == "COMPLETED"
    assert dose_times[1] == dose_times[0] + 180


def test_all_existing_sources_and_configs_remain_byte_identical():
    assert not [name for name,digest in BEFORE.items() if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != digest]


def test_physical_command_streams_match_the_original_corresponding_pair(cfg, tmp_path):
    captured = {}
    def factory(label):
        @contextmanager
        def opened(*args, **kwargs):
            with full.demo.setup_instruments(*args, **kwargs) as rig:
                yield rig
                captured[label] = (list(rig.pump.ser.tx_log), list(rig.controller.transport.tx_log))
        return opened
    full.demo.run_experiment(cfg, mock=True, output_dir=tmp_path / "10", rig_factory=factory("10"))
    full.run_experiment(cfg, mock=True, output_dir=tmp_path / "12", rig_factory=factory("12"))
    short_cfg = short.load_check_config()
    short_cfg["three_instrument_check"]["analyze_spectrum"] = False
    short.check.run_check(short_cfg, mock=True, output_dir=tmp_path / "11", rig_factory=factory("11"))
    short.run_check(short_cfg, mock=True, output_dir=tmp_path / "13", rig_factory=factory("13"))
    assert captured["10"] == captured["12"] and captured["11"] == captured["13"]


def test_normal_legacy_http_sequence_and_saved_data_match_standalone_04_05(tmp_path, cfg, clock, http):
    from chemyx_lab.workflows import nmr_standalone as old
    settings = SimpleNamespace(receiver_gain=12, auto_gain=False, scans=8, spectral_center=5,
                               sweep_width=20, poll_seconds=1, max_wait_seconds=5)
    tracing = old.TracingClient(NmrRpcConfig(host="example.invalid"))
    baseline = old.raw_text(tracing.iflow_experiment_status())
    templates = (tracing.iflow_1d_settings(), tracing.iflow_experiment_settings())
    prior = old.legacy_acquire(settings, tmp_path / "old.dx", tracing, templates, baseline_fid=baseline, out=lambda _: None)
    prior_commands = http.commands
    http.calls, http.counter, http.current = [], 0, None
    current = acquire(tmp_path, clock, cfg)
    assert current["status"] == prior["status"] == "NMR_SAVED"
    assert Path(current["path"]).read_bytes() == Path(prior["path"]).read_bytes()
    assert http.commands == prior_commands


@pytest.mark.parametrize("server_export", ["", "damaged export", "##TITLE=server FD\n##DATA TYPE=NMR SPECTRUM\n##END=\n"])
def test_short_validates_actual_raw_and_preserves_any_colliding_server_export(tmp_path, clock, http, server_export):
    cfg = short.load_check_config()
    def start(settings):
        Path(settings["ExportFilename"]).parent.mkdir(exist_ok=True)
        Path(settings["ExportFilename"]).write_text(server_export)
        return http.accept(settings)
    http.returns("PUT", START, start)
    inputs = []
    def analyze(run, snapshot, raw, emit):
        inputs.append(raw)
        assert legacy.raw_fid({"Jcamp": raw.read_text()})
        return {"status": "PASS", "detail": str(raw)}
    result = short.run_check(cfg, mock=True, clock=clock, client=NmrRpcClient(NmrRpcConfig()),
                             output_dir=tmp_path / "run", analyzer=analyze)
    assert result["status"] == "COMPLETE" and inputs[0].name == "instrument_check_0001.dx"
    assert (tmp_path / "run/nmr_raw/instrument_check_0001.server_export.dx").read_text() == server_export
    assert legacy.raw_fid({"Jcamp": (tmp_path / "run/nmr_raw/instrument_check_0001.dx").read_text()})


@pytest.mark.parametrize("script", ["12_stage2only.py", "13_3InstrumentCheck.py"])
def test_mock_cli_cannot_connect_real_serial_or_network_and_does_not_import_numerics(tmp_path, script):
    cfg = full.load_config() if script.startswith("12") else short.load_check_config()
    cfg["analysis"]["enabled"] = False
    if script.startswith("13"):
        cfg["three_instrument_check"]["analyze_spectrum"] = False
        # The check configuration is its own small YAML pointing at the new preset.
        path = ROOT / "configs/experiments/13_3InstrumentCheck.yaml"
        config_path = tmp_path / "check.yaml"
        own = yaml.safe_load(path.read_text())
        own["three_instrument_check"]["analyze_spectrum"] = False
        config_path.write_text(yaml.safe_dump(own))
    else:
        config_path = tmp_path / "full.yaml"
        config_path.write_text(yaml.safe_dump(cfg))
    source = """import sys,socket,serial,runpy
class NoScience:
 def find_spec(self,name,*args):
  if name.split('.')[0] in {'numpy','scipy','matplotlib','nmrglue'}: raise ModuleNotFoundError(name)
sys.meta_path.insert(0,NoScience())
socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('no real network'))
serial.Serial=lambda *a,**k: (_ for _ in ()).throw(AssertionError('no real serial'))
script,cfg,out=sys.argv[1:]
sys.path.insert(0,'scripts')
sys.argv=[script,'--config',cfg,'--output-dir',out,'--mock']
runpy.run_path('scripts/'+script,run_name='__main__')
"""
    output = tmp_path / "run"
    proc = subprocess.run([sys.executable,"-c",source,script,str(config_path),str(output)],
                          capture_output=True,text=True,cwd=ROOT,timeout=60)
    assert proc.returncode == 0, proc.stdout+proc.stderr
    report = json.loads((output / "summary.json").read_text())
    assert report["status"] == "COMPLETE"
