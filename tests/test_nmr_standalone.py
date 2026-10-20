"""Standalone NMR tests (01 connection, 02/03 acquisition) against a loopback fake NMR.

No configured WORK address is contacted: every live-mode run uses 127.0.0.1.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

import pytest
import yaml

from arduino.python.run_lock import PortProcessLock
from chemyx_lab import config
from chemyx_lab.instruments.nmr import (NmrRpcClient, NmrRpcConfig, NmrRpcError,
                                        example_iflow_1d_settings, example_iflow_experiment_settings)
from chemyx_lab.workflows import nmr_standalone as ns
from chemyx_lab.workflows.si6_automated_nmr import build_instrument_settings
from chemyx_lab.workflows.si6_fixed_time_nmr import running_state

FID = (config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx").read_text(encoding="utf-8")
PRESETS = ("configs/experiments/si6_stage2only_6h.yaml", "configs/experiments/si6_stage2only_scipy_6h.yaml")
IDLE = {"ResultCode": 0, "NumberOfScansRun": 0, "JDX_FileContents_TD": "",
        "OriginalReceipt": {"ExperimentNumber": 1, "TimeStamp": "old"}}


class FakeNmr:
    """Scripted NMReady RPC server. ``status`` is returned before START."""

    def __init__(self, *, status=IDLE, rpc_enabled=True, scans=1, raw=None, extra=None):
        self.status, self.rpc_enabled, self.scans = status, rpc_enabled, scans
        self.raw = raw if raw is not None else {"/x": (200, "text/plain", "")}
        self.extra = extra or {}
        self.requests = []
        self.started = False
        self.polls_after_start = 0

    def respond(self, method, path, body):
        self.requests.append((method, path))
        if path in self.raw:
            return self.raw[path]
        if path in self.extra:
            return self.extra[path]
        if path == "/interfaces/iStatus/PingSpectrometer":
            return self.json({"connected": True})
        if path == "/interfaces/iStatus/RpcEnabled":
            return self.json({"RpcEnabled": self.rpc_enabled})
        if path == "/interfaces/Service/RpcActive":
            return self.json({"RpcActive": True})
        if path == "/interfaces/iStatus/SpectrometerStatus":
            return self.json({"StandbyMode": False, "SoftwareVersion": "fake"})
        if path == "/interfaces/iStatus/OperationalMessages":
            return self.json({"messages": []})
        if path == "/interfaces/iFlow/Settings/1D":
            return self.json(example_iflow_1d_settings() if method == "GET" else {})
        if path == "/interfaces/iFlow/ExperimentSettings":
            return self.json(example_iflow_experiment_settings())
        if path == "/interfaces/iFlow/RunExperiment" and method == "PUT":
            self.started = True
            return self.json({"ExperimentNumber": 2, "ResultCode": 1, "TimeStamp": "new"})
        if path == "/interfaces/iFlow/ExperimentStatus":
            if not self.started:
                return self.json(self.status) if not isinstance(self.status, tuple) else self.status
            self.polls_after_start += 1
            if self.polls_after_start == 1:
                return self.json({"ResultCode": 1, "NumberOfScansRun": 0})
            fid = FID.replace("##.AVERAGES=8", f"##.AVERAGES={self.scans}")
            return self.json({"ResultCode": 0, "NumberOfScansRun": self.scans, "JDX_FileContents_TD": fid,
                              "OriginalReceipt": {"ExperimentNumber": 2, "TimeStamp": "new"}})
        return 404, "text/plain", "Not Found"

    @staticmethod
    def json(value):
        return 200, "application/json", json.dumps(value)

    def puts(self, suffix):
        return [path for method, path in self.requests if method == "PUT" and path.endswith(suffix)]


@pytest.fixture
def serve(monkeypatch):
    for name in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    for name in ("NMR_HOST", "NMR_PORT", "NMR_RPC_SCHEME", "NMR_RPC_TIMEOUT", "NMR_RPC_POLL_SECONDS",
                 "NMR_RPC_MAX_WAIT_SECONDS", "NMR_ROUTE", "NMR_RECEIVER_GAIN", "NMR_AUTO_GAIN",
                 "NMR_DEFAULT_SPECTRAL_CENTER", "NMR_DEFAULT_SWEEP_WIDTH", "NMR_SAVE_DIR"):
        monkeypatch.delenv(name, raising=False)
    servers = []

    def start(fake):
        class Handler(BaseHTTPRequestHandler):
            def _answer(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                code, content_type, text = fake.respond(self.command, self.path, body)
                data = text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_PUT = _answer

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield start
    for server in servers:
        server.shutdown()
        server.server_close()


def write_configs(tmp_path, port, retrieval_wait=0.3, timeout=2.0):
    machine = tmp_path / "machine.yaml"
    machine.write_text(yaml.safe_dump({"nmr": {"host": "127.0.0.1", "port": port, "scheme": "http",
                                               "timeout_seconds": timeout, "poll_seconds": 0.05,
                                               "max_wait_seconds": 3.0}}), encoding="utf-8")
    preset = yaml.safe_load((config.REPO_ROOT / PRESETS[0]).read_text(encoding="utf-8"))
    experiment = tmp_path / "experiment.yaml"
    experiment.write_text(yaml.safe_dump({"machine_config": str(machine), "nmr": preset["nmr"],
                                          "retrieval_wait_seconds": retrieval_wait}), encoding="utf-8")
    return ["--experiment-config", str(experiment)]


def closed_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("preset", PRESETS)
def test_settings_match_the_stage2only_runs_exactly(preset, monkeypatch):
    monkeypatch.delenv("NMR_HOST", raising=False)
    raw = config.read_mapping_config(config.REPO_ROOT / preset)
    expected = build_instrument_settings(raw, config.resolve_repo_path(raw["machine_config"]), acquisition_only=True)[1]
    settings, retrieval_wait, sources = ns.load_settings(preset)
    assert settings == expected
    assert retrieval_wait == raw["retrieval_wait_seconds"]
    one, _, _ = ns.load_settings(preset, scans=1)
    assert one.scans == 1 and one.__class__(**{**one.__dict__, "scans": expected.scans}) == expected
    assert sources["environment_overrides"] == {}


def test_settings_report_environment_overrides_and_refuse_missing_machine(monkeypatch, tmp_path):
    monkeypatch.setenv("NMR_HOST", "10.1.2.3")
    settings, _, sources = ns.load_settings()
    assert settings.host == "10.1.2.3" and sources["environment_overrides"] == {"host": "10.1.2.3"}
    with pytest.raises(config.ConfigError, match="Machine YAML not found"):
        ns.load_settings(machine_config=tmp_path / "missing.yaml")


@pytest.mark.parametrize("status, state, phrase", [
    ({"ResultCode": 0}, "idle", "ResultCode=0"),
    ({"Running": False}, "idle", "Running=false"),
    ({"ResultCode": 1}, "busy", "ResultCode=1"),
    ({"Running": True, "ResultCode": 0}, "busy", "Running=true"),
    ({"ResultCode": 0, "PercentComplete": 0}, "busy", "PercentComplete=0"),
    ({"ResultCode": 2, "NumberOfScansRun": 0}, "unknown", "ResultCode=2"),
    ({"ResultCode": "0"}, "unknown", "is a str"),
    ({"Running": "false"}, "unknown", "not true/false"),
    ({"PercentComplete": 100}, "unknown", "does not prove idle"),
    ({"Status": {"Running": False}}, "unknown", "Status.Running"),
    ({"Message": "hi"}, "unknown", "keys received: Message"),
    (' \n{"ResultCode": 0}', "unknown", "arrived as plain text"),
    ("Service unavailable", "unknown", "plain text"),
    (None, "unknown", "empty reply"),
    ([1], "unknown", "JSON list"),
])
def test_status_explanations_follow_the_worker_verdict(status, state, phrase):
    label, reason = ns.explain_status(status)
    assert label == {True: "busy", False: "idle", None: "unknown"}[running_state(status)] == state
    assert phrase in reason


def test_tracing_client_returns_exactly_what_the_experiment_client_returns(serve):
    port = serve(FakeNmr(raw={
        "/json": (200, "application/json", '{"a": 1}'),
        "/ws": (200, "text/plain", ' \n{"ResultCode": 0}'),
        "/text": (200, "text/plain", "hello"),
        "/empty": (200, "application/json", ""),
        "/bad": (200, "application/json", "{bad"),
        "/forbidden": (403, "text/plain", "Forbidden"),
    }))
    cfg = NmrRpcConfig(host="127.0.0.1", port=port, timeout=2)
    plain, traced = NmrRpcClient(cfg), ns.TracingClient(cfg)
    for path in ("/json", "/ws", "/text", "/empty", "/bad"):
        assert traced.get(path) == plain.get(path)
    with pytest.raises(NmrRpcError) as expected:
        plain.put("/forbidden", {"x": 1})
    with pytest.raises(NmrRpcError) as actual:
        traced.put("/forbidden", {"x": 1})
    assert str(actual.value) == str(expected.value)
    by_path = {record["path"]: record for record in traced.exchanges}
    assert by_path["/ws"]["decoded_as"] == "text" and "plain text" in by_path["/ws"]["warning"]
    assert by_path["/forbidden"]["http_status"] == 403 and by_path["/forbidden"]["sent"] == {"x": 1}


def test_bounded_evidence_summarizes_spectra():
    value = ns.bounded({"JDX_FileContents_TD": FID, "JDX_FileContents_FD": "", "Peaks": list(range(50))})
    assert value["JDX_FileContents_TD"].startswith("<text,") and value["JDX_FileContents_FD"] == ""
    assert len(value["Peaks"]) == 21


def test_connection_test_passes_and_saves_full_evidence(serve, tmp_path):
    fake = FakeNmr()
    args = write_configs(tmp_path, serve(fake))
    lines = []
    assert ns.connection_test(args + ["--output-dir", str(tmp_path / "out")], out=lines.append) == 0
    text = "\n".join(lines)
    assert "RESULT: PASS" in text and "NMR IDLE: ResultCode=0 (0 = idle)" in text
    assert not [method for method, _ in fake.requests if method != "GET"]  # Read-only.
    assert ("GET", "/interfaces/iFlow/RunExperiment") not in fake.requests  # A GET would launch a run.
    report = json.loads((tmp_path / "out" / ns.REPORT_NAME).read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS" and report["network"]["tcp"] == "open"
    assert any(r["path"] == ns.STATUS_PATH and r["state"] == "idle" for r in report["http_exchanges"])


@pytest.mark.parametrize("fake, expected", [
    (FakeNmr(status={"ResultCode": 2, "NumberOfScansRun": 0}), "ResultCode=2"),
    (FakeNmr(status={"ResultCode": 1}), "acquisition is running"),
    (FakeNmr(rpc_enabled=False), "Setup -> System -> Remote"),
    (FakeNmr(status=(200, "text/plain", ' {"ResultCode": 0}')), "arrived as plain text"),
    (FakeNmr(extra={"/interfaces/iFlow/ExperimentSettings": (404, "text/plain", "Not Found")}),
     "ExperimentSettings settings template could not be read"),
])
def test_connection_test_explains_each_failure(serve, tmp_path, fake, expected):
    args = write_configs(tmp_path, serve(fake))
    lines = []
    assert ns.connection_test(args + ["--output-dir", str(tmp_path / "out")], out=lines.append) == 1
    text = "\n".join(lines)
    assert "RESULT: FAIL" in text and expected in text
    report = json.loads((tmp_path / "out" / ns.REPORT_NAME).read_text(encoding="utf-8"))
    assert report["verdict"] == "FAIL" and report["findings"]


def test_connection_test_without_a_listener_diagnoses_the_port(tmp_path, serve):
    args = write_configs(tmp_path, closed_port(), timeout=8.0)  # Windows reports refusal after ~2 s.
    lines = []
    assert ns.connection_test(args + ["--output-dir", str(tmp_path / "out")], out=lines.append) == 1
    text = "\n".join(lines)
    assert "REFUSED" in text and "nothing accepts connections" in text and "Steps 3-5 skipped" in text


def test_configuration_error_stops_before_any_request(tmp_path, serve):
    lines = []
    code = ns.connection_test(["--machine-config", str(tmp_path / "missing.yaml")], out=lines.append)
    assert code == 2 and "Machine YAML not found" in "\n".join(lines)


def test_existing_output_folder_is_refused_without_contacting_the_nmr(serve, tmp_path):
    fake = FakeNmr()
    args = write_configs(tmp_path, serve(fake))
    (tmp_path / "out").mkdir()
    lines = []
    assert ns.acquisition_test(args + ["--output-dir", str(tmp_path / "out")], scans=1, out=lines.append) == 2
    assert "cannot create a new results folder" in "\n".join(lines) and not fake.requests


@pytest.mark.parametrize("scans", [1, 4])
def test_acquisition_saves_raw_fid_with_requested_scans(serve, tmp_path, scans):
    fake = FakeNmr(scans=scans)
    args = write_configs(tmp_path, serve(fake))
    lines = []
    code = ns.acquisition_test(args + ["--output-dir", str(tmp_path / "out")], scans=scans,
                               input_fn=lambda prompt: "y", out=lines.append)
    text = "\n".join(lines)
    assert code == 0, text
    assert "RESULT: PASS  (START was sent once)" in text and "START sent" in text
    assert len(fake.puts("/RunExperiment")) == 1
    report = json.loads((tmp_path / "out" / ns.REPORT_NAME).read_text(encoding="utf-8"))
    sent = next(r for r in report["http_exchanges"] if r["path"].endswith("/RunExperiment"))
    assert sent["sent"]["NumberOfScans"] == scans
    assert report["saved_file"]["averages"] == str(scans) and report["saved_file"]["is_fid"]
    assert report["saved_file"]["long_date"] == "2026/08/10 17:27:21-0400"
    assert Path(report["worker_result"]["path"]).name == f"nmr_{scans}scan_0001.dx"


def test_acquisition_refuses_unknown_status_without_start(serve, tmp_path):
    fake = FakeNmr(status={"ResultCode": 2, "NumberOfScansRun": 0})
    args = write_configs(tmp_path, serve(fake))
    lines = []
    code = ns.acquisition_test(args + ["--output-dir", str(tmp_path / "out")], scans=1,
                               input_fn=lambda prompt: pytest.fail("must not ask"), out=lines.append)
    text = "\n".join(lines)
    assert code == 1 and "START was NOT sent" in text and "NMR idle not confirmed" in text
    assert not [method for method, _ in fake.requests if method == "PUT"]


def test_acquisition_declined_changes_nothing(serve, tmp_path):
    fake = FakeNmr()
    args = write_configs(tmp_path, serve(fake))
    code = ns.acquisition_test(args + ["--output-dir", str(tmp_path / "out")], scans=1,
                               input_fn=lambda prompt: "n", out=lambda line: None)
    assert code == 3 and not [method for method, _ in fake.requests if method == "PUT"]


def test_acquisition_respects_experiment_ownership(serve, tmp_path):
    fake = FakeNmr()
    port = serve(fake)
    args = write_configs(tmp_path, port)
    lines = []
    with PortProcessLock(f"fixed-nmr:127.0.0.1:{port}"):
        code = ns.acquisition_test(args + ["--output-dir", str(tmp_path / "out"), "--yes"], scans=1,
                                   out=lines.append)
    assert code == 1 and "Another program on this laptop is using the NMR" in "\n".join(lines)
    assert not [method for method, _ in fake.requests if method == "PUT"]


def test_acquisition_timeout_explains_that_start_was_sent(serve, tmp_path):
    class NeverFinishes(FakeNmr):
        def respond(self, method, path, body):
            if path == ns.STATUS_PATH and self.started:
                self.requests.append((method, path))
                return self.json(IDLE)  # Old receipt and no running state: never proven complete.
            return super().respond(method, path, body)

    fake = NeverFinishes()
    args = write_configs(tmp_path, serve(fake))
    lines = []
    code = ns.acquisition_test(args + ["--output-dir", str(tmp_path / "out"), "--yes"], scans=1, out=lines.append)
    text = "\n".join(lines)
    assert code == 1 and "START was sent once" in text
    assert "not confirmed complete" in text and "never reported 'running'" in text
    assert len(fake.puts("/RunExperiment")) == 1


def test_acquisition_explains_forbidden_settings_change(serve, tmp_path):
    class Forbidden(FakeNmr):
        def respond(self, method, path, body):
            if method == "PUT":
                self.requests.append((method, path))
                return 403, "text/plain", "Forbidden"
            return super().respond(method, path, body)

    fake = Forbidden()
    args = write_configs(tmp_path, serve(fake))
    lines = []
    code = ns.acquisition_test(args + ["--output-dir", str(tmp_path / "out"), "--yes"], scans=1, out=lines.append)
    text = "\n".join(lines)
    assert code == 1 and "HTTP 403 Forbidden" in text and "START was NOT sent" in text
    assert not fake.puts("/RunExperiment")


def test_fid_header_inspection_and_scan_mismatch_warning(tmp_path):
    path = tmp_path / "x.dx"
    path.write_text(FID, encoding="utf-8")
    facts = ns.inspect_fid(path)
    assert facts["is_fid"] and facts["averages"] == "8" and facts["data_type"] == "NMR FID"
    assert ns.fid_findings(facts, 8) == []
    assert "averaged 8 scan(s), not the requested 4" in ns.fid_findings(facts, 4)[0]["problem"]


def test_network_findings_name_the_likely_cause():
    down = ns.network_findings({"host": "169.254.30.54", "port": 5000, "tcp": "timeout",
                                "tcp_error": "TimeoutError: timed out", "local_address": "192.168.1.7"})
    assert "No network link" in down[0]["problem"] and any("ipconfig" in step for step in down[0]["fix"])
    proxy = ns.network_findings({"host": "169.254.30.54", "port": 5000, "tcp": "open", "proxy": "http://p:8080"})
    assert proxy[0]["level"] == "WARN" and "proxy" in proxy[0]["problem"]


def test_standalone_module_has_no_numerical_analysis_imports():
    source = Path(ns.__file__).read_text(encoding="utf-8")
    assert "analysis" not in "".join(line for line in source.splitlines() if line.startswith(("import", "from")))


@pytest.mark.parametrize("script", ["01_test_connection.py", "02_run_1_scan.py", "03_run_4_scans.py"])
def test_scripts_show_help(script):
    result = subprocess.run([sys.executable, "-B", str(config.REPO_ROOT / "scripts/nmr_standalone" / script), "--help"],
                            cwd=config.REPO_ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "--machine-config" in result.stdout
    assert ("--yes" in result.stdout) == (script != "01_test_connection.py")
