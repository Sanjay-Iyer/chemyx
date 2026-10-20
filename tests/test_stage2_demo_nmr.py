"""10-only acquisition contracts through the real HTTP client, without a server."""
from collections import deque
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
from urllib import error, parse

import pytest

from chemyx_lab.instruments import nmr
from chemyx_lab.instruments.nmr import NmrRpcClient, NmrRpcConfig
from chemyx_lab.workflows.stage2_demo_nmr import _evidence, acquire_nmr


ROOT = Path(__file__).resolve().parents[1]
RAW = "##TITLE=Demo\r\n##DATA TYPE=NMR FID\r\n##XYDATA=(X++(Y..Y))\r\n0 1 2\r\n##END=\r\n"
CFG = {"host": "example.invalid", "acquisition_timeout_seconds": 5, "poll_seconds": 1,
       "http_timeout_seconds": 2, "scans": 8, "gain": 12, "auto_gain": False,
       "center_ppm": 5, "width_ppm": 20}
ONE_D = "/interfaces/iFlow/Settings/1D"
SETTINGS = "/interfaces/iFlow/ExperimentSettings"
START = "/interfaces/iFlow/RunExperiment"
STATUS = "/interfaces/iFlow/ExperimentStatus"
SERVICE = "/interfaces/Service/Acquire"


class Clock:
    def __init__(self):
        self.value = 0.

    def now(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class Response:
    def __init__(self, value):
        self.body = json.dumps(value).encode()
        self.headers = {"Content-Type": "application/json"}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def read(self):
        return self.body


class HTTP:
    """Replace only urlopen: request encoding/routing/error decoding remain real."""
    def __init__(self, monkeypatch):
        self.calls = []
        self.routes = {}
        self.current = None
        self.counter = 0
        self.status_reads = 0
        self.on_request = None
        monkeypatch.setattr(nmr.request, "urlopen", self)

    def returns(self, method, path, *values):
        self.routes[method, path] = deque(values)
        return self

    def __call__(self, request, timeout):
        method, path = request.get_method(), parse.urlsplit(request.full_url).path
        payload = json.loads(request.data) if request.data is not None else None
        self.calls.append((method, path, payload, timeout))
        if self.on_request:
            self.on_request(method, path, payload)
        queue = self.routes.get((method, path))
        if queue:
            response = queue.popleft() if len(queue) > 1 else queue[0]
            if callable(response):
                response = response(payload)
        elif (method, path) == ("GET", ONE_D):
            response = {"keep_server_key": "yes"}
        elif (method, path) == ("GET", SETTINGS):
            response = {"keep_server_key": 8}
        elif (method, path) == ("PUT", ONE_D):
            response = {}
        elif (method, path) == ("PUT", START):
            response = self.accept(payload)
        elif (method, path) == ("GET", STATUS):
            self.status_reads += 1
            response = self.complete()
        elif (method, path) == ("GET", SERVICE):
            response = {"Status": {"InProgress": False, "LastAcquireSuccessful": True}}
        else:
            raise AssertionError(f"Unexpected HTTP command: {method} {path}")
        if isinstance(response, BaseException):
            raise response
        return response if isinstance(response, Response) else Response(response)

    def accept(self, settings):
        self.counter += 1
        self.current = {"ExperimentNumber": self.counter, "TimeStamp": f"acquisition-{self.counter}",
                        "Settings": deepcopy(settings), "ResultCode": 1}
        return deepcopy(self.current)

    def complete(self, *, receipt=None, scans=8, raw=RAW, **extra):
        return {"ResultCode": 2, "OriginalReceipt": deepcopy(receipt if receipt is not None else self.current),
                "NumberOfScansRun": scans, "JDX_FileContents_TD": raw, **extra}

    @property
    def commands(self):
        return [(method, path) for method, path, _, _ in self.calls]

    @property
    def starts(self):
        return [payload for method, path, payload, _ in self.calls if (method, path) == ("PUT", START)]


@pytest.fixture
def http(monkeypatch):
    return HTTP(monkeypatch)


def acquire(tmp_path, http, *, clock=None, state=None, filename="observation.dx", **kwargs):
    clock = clock or Clock()
    client = kwargs.pop("client", NmrRpcClient(NmrRpcConfig(host="example.invalid", timeout=40)))
    return acquire_nmr(CFG, tmp_path / filename, client=client, state=state,
                       now=clock.now, sleep=clock.sleep, **kwargs)


def test_exact_http_sequence_has_no_status_before_start_and_applies_eight_scans(tmp_path, http):
    messages, state = [], {}
    result = acquire(tmp_path, http, state=state, emit=messages.append)
    assert result["status"] == "NMR_SAVED"
    assert Path(result["path"]).read_bytes() == RAW.encode()
    assert http.commands == [("GET", ONE_D), ("GET", SETTINGS), ("PUT", ONE_D), ("PUT", START),
                             ("GET", STATUS), ("GET", SERVICE), ("GET", STATUS)]
    assert len(http.starts) == 1
    settings = http.starts[0]
    assert settings["NumberOfScans"] == 8 and settings["keep_server_key"] == 8
    assert settings["ReceiverGain"] == 12
    assert settings["SpectralCentreInPpm"] == 5 and settings["SpectralWidthInPpm"] == 20
    applied = next(payload for method, path, payload, _ in http.calls if (method, path) == ("PUT", ONE_D))
    assert applied["keep_server_key"] == "yes" and applied["AutoGain"] is False
    assert Path(settings["ExportFilename"]) == tmp_path / "observation.dx"
    assert not state.get("pending")
    text = "\n".join(messages)
    assert "8 scans" in text and "START" in text and "complete" in text and "saved" in text.lower()
    assert "idle" not in text.lower()


def test_waits_for_matching_acquisition_scans_and_actual_service_completion(tmp_path, http):
    clock = Clock()
    http.returns("GET", STATUS,
                 lambda _: http.complete(receipt={"ExperimentNumber": 0, "TimeStamp": "stale"}),
                 lambda _: http.complete(scans=3), lambda _: http.complete(),
                 lambda _: http.complete(), lambda _: http.complete())
    http.returns("GET", SERVICE,
                 {"Status": {"InProgress": True, "LastAcquireSuccessful": False}},
                 {"Status": {"InProgress": False, "LastAcquireSuccessful": True}})
    result = acquire(tmp_path, http, clock=clock)
    assert result["status"] == "NMR_SAVED" and clock.value >= 3
    first_service = http.commands.index(("GET", SERVICE))
    assert http.commands[:first_service].count(("GET", STATUS)) == 3
    assert len(http.starts) == 1


@pytest.mark.parametrize("wrong", [
    {"ExperimentNumber": 99, "TimeStamp": "unrelated"},
    {"ExperimentNumber": 1},
    {"TimeStamp": "acquisition-1"},
    {},
])
def test_stale_partial_or_conflicting_receipt_never_completes(tmp_path, http, wrong):
    http.returns("GET", STATUS, lambda _: http.complete(receipt=wrong))
    state = {}
    result = acquire(tmp_path, http, state=state)
    assert result["status"] == "NMR_FAILED"
    assert state.get("pending") and len(http.starts) == 1
    assert ("GET", SERVICE) not in http.commands
    assert not (tmp_path / "observation.dx").exists()


@pytest.mark.parametrize("scans", [0, 7, "8", True, None])
def test_unconfirmed_scan_count_never_finishes(tmp_path, http, scans):
    http.returns("GET", STATUS, lambda _: http.complete(scans=scans))
    state = {}
    result = acquire(tmp_path, http, state=state)
    assert result["status"] == "NMR_FAILED" and state.get("pending")
    assert len(http.starts) == 1 and ("GET", SERVICE) not in http.commands


@pytest.mark.parametrize("service", [
    {"Status": {"InProgress": True, "LastAcquireSuccessful": True}},
    {"Status": {"InProgress": False, "LastAcquireSuccessful": False}},
    {"Status": {"InProgress": "false", "LastAcquireSuccessful": "true"}},
    {"ResultCode": 0}, {},
])
def test_service_completion_must_be_explicit_and_successful(tmp_path, http, service):
    http.returns("GET", SERVICE, service)
    state = {}
    result = acquire(tmp_path, http, state=state)
    assert result["status"] == "NMR_FAILED" and state.get("pending")
    assert len(http.starts) == 1 and not (tmp_path / "observation.dx").exists()


def test_status_resultcode_is_not_an_idle_permission_or_completion_enum(tmp_path, http):
    http.returns("GET", STATUS, lambda _: http.complete(ResultCode="unverified-server-value"))
    assert acquire(tmp_path, http)["status"] == "NMR_SAVED"
    assert http.commands.index(("PUT", START)) < http.commands.index(("GET", STATUS))


@pytest.mark.parametrize("key", ["ExperimentNumber", "TimeStamp"])
def test_partial_start_identity_cannot_attribute_an_old_receipt_without_unique_export(tmp_path, http, key):
    def partial(settings):
        receipt = http.accept(settings)
        return {key: receipt[key]}
    def stale(_):
        receipt = {"ExperimentNumber": http.current["ExperimentNumber"],
                   "TimeStamp": http.current["TimeStamp"]}
        return http.complete(receipt=receipt)
    http.returns("PUT", START, partial)
    http.returns("GET", STATUS, stale)
    state = {}
    result = acquire(tmp_path, http, state=state)
    assert result["status"] == "NMR_FAILED" and state.get("pending")
    assert len(http.starts) == 1 and not (tmp_path / "observation.dx").exists()
    assert ("GET", SERVICE) not in http.commands


@pytest.mark.parametrize("key", ["ExperimentNumber", "TimeStamp"])
def test_partial_start_identity_is_attributable_with_unique_export_echo(tmp_path, http, key):
    def partial(settings):
        receipt = http.accept(settings)
        return {key: receipt[key]}
    http.returns("PUT", START, partial)
    state = {}
    result = acquire(tmp_path, http, state=state)
    assert result["status"] == "NMR_SAVED" and len(http.starts) == 1
    assert Path(result["path"]).read_bytes() == RAW.encode() and not state.get("pending")


def test_three_consecutive_observations_have_three_real_http_starts(tmp_path, http):
    state, clock = {}, Clock()
    results = [acquire(tmp_path, http, state=state, clock=clock, filename=f"stage_2_{i:04d}.dx")
               for i in range(1, 4)]
    assert [r["status"] for r in results] == ["NMR_SAVED"] * 3
    assert len(http.starts) == 3
    assert len({settings["ExportFilename"] for settings in http.starts}) == 3
    assert [command for command in http.commands if command[1] == START] == [("PUT", START)] * 3
    assert all(Path(r["path"]).read_bytes() == RAW.encode() for r in results)
    assert not state.get("pending")


def test_uncertain_start_never_automatically_repeats_or_accepts_unrelated_completion(tmp_path, http):
    def lost(settings):
        http.accept(settings)
        return error.URLError("reply lost after dispatch")
    http.returns("PUT", START, lost)
    http.returns("GET", STATUS, lambda _: http.complete(receipt={"ExperimentNumber": 99, "TimeStamp": "other"}))
    state = {}
    first = acquire(tmp_path, http, state=state)
    assert first["requested"] and first["start_state"] == "uncertain"
    assert "reply lost after dispatch" in first["reason"] and state.get("pending")
    before = len(http.calls)
    second = acquire(tmp_path, http, state=state, filename="next.dx")
    assert second["status"] == "NMR_FAILED" and not second["requested"]
    assert len(http.starts) == 1 and state.get("pending")
    assert all(method == "GET" and path == STATUS for method, path, _, _ in http.calls[before:])
    assert not (tmp_path / "next.dx").exists()


def test_lost_start_receipt_recovers_only_unique_export_then_can_start_next(tmp_path, http):
    def lost(settings):
        http.accept(settings)
        return TimeoutError("START response lost")
    http.returns("PUT", START, lost)
    state = {}
    assert acquire(tmp_path, http, state=state)["status"] == "NMR_FAILED"
    assert state["pending"]["path"] == str((tmp_path / "observation.dx").resolve())
    http.routes.pop(("PUT", START))
    result = acquire(tmp_path, http, state=state, filename="next.dx")
    assert result["status"] == "NMR_SAVED" and len(http.starts) == 2
    previous = result["recovered_previous"]
    assert previous["status"] == "NMR_SAVED"
    assert Path(previous["path"]) == tmp_path / "observation.dx"
    assert Path(previous["path"]).read_bytes() == RAW.encode()
    assert not state.get("pending")


def test_timeout_blocks_later_start_until_prior_specific_acquisition_completes(tmp_path, http):
    http.returns("GET", STATUS, lambda _: http.complete(scans=4))
    state = {}
    first = acquire(tmp_path, http, state=state)
    assert first["status"] == "NMR_FAILED" and state.get("pending")
    second = acquire(tmp_path, http, state=state, filename="second.dx")
    assert second["status"] == "NMR_FAILED" and not second["requested"]
    assert len(http.starts) == 1 and not (tmp_path / "second.dx").exists()
    http.routes.pop(("GET", STATUS))
    third = acquire(tmp_path, http, state=state, filename="third.dx")
    assert third["status"] == "NMR_SAVED" and len(http.starts) == 2
    assert third["recovered_previous"]["status"] == "NMR_SAVED"
    assert Path(third["recovered_previous"]["path"]) == tmp_path / "observation.dx"
    assert not state.get("pending")


def test_actual_http_error_details_are_exposed_without_start_retry(tmp_path, http):
    class Body:
        def read(self):
            return b'{"Message":"Spectrometer rejected START: already acquiring"}'
    rejection = error.HTTPError("http://example.invalid" + START, 409, "Conflict", {}, Body())
    http.returns("PUT", START, rejection)
    messages = []
    result = acquire(tmp_path, http, emit=messages.append)
    assert result["status"] == "NMR_FAILED" and len(http.starts) == 1
    assert "HTTP 409" in result["reason"]
    assert "Spectrometer rejected START: already acquiring" in result["reason"]


def test_json_start_rejection_is_printed_exactly_and_not_resent(tmp_path, http):
    response = {"Success": False, "Message": "Server refuses START for this observation", "ResultCode": 403}
    http.returns("PUT", START, response)
    messages, state = [], {}
    result = acquire(tmp_path, http, emit=messages.append, state=state)
    assert result["status"] == "NMR_FAILED" and len(http.starts) == 1
    assert "Server refuses START for this observation" in result["reason"]
    assert repr(response) in "\n".join(messages) and state.get("pending")


def test_existing_output_is_preserved_without_any_http_commands(tmp_path, http):
    path = tmp_path / "observation.dx"
    path.write_text("previous")
    result = acquire(tmp_path, http)
    assert result["status"] == "NMR_FAILED" and not result["requested"]
    assert not http.calls and path.read_text() == "previous"


def test_server_export_preserved_when_inline_raw_is_saved(tmp_path, http):
    def accepted(settings):
        Path(settings["ExportFilename"]).write_text("server processed export")
        return http.accept(settings)
    http.returns("PUT", START, accepted)
    result = acquire(tmp_path, http)
    assert result["status"] == "NMR_SAVED" and result["path"].endswith(".rpc.dx")
    assert (tmp_path / "observation.dx").read_text() == "server processed export"
    assert Path(result["path"]).read_bytes() == RAW.encode()


@pytest.mark.parametrize("raw", ["", "##TITLE=empty\n##DATA TYPE=NMR FID\n##END=\n",
                                   RAW.replace("NMR FID", "NMR SPECTRUM"), RAW.replace("##END=", ""),
                                   RAW.replace("##END=", "##END NTUPLES=")])
def test_empty_invalid_or_processed_payload_is_not_saved_as_raw(tmp_path, http, raw):
    http.returns("GET", STATUS, lambda _: http.complete(raw=raw))
    state = {}
    result = acquire(tmp_path, http, state=state)
    assert result["status"] == "NMR_MISSING"
    assert len(http.starts) == 1 and not (tmp_path / "observation.dx").exists()
    assert not state.get("pending")  # Acquisition completed; only data is missing.


def test_raw_arriving_after_completion_is_waited_for_without_start_retry(tmp_path, http):
    clock = Clock()
    http.returns("GET", STATUS, lambda _: http.complete(raw=""), lambda _: http.complete(raw=""),
                 lambda _: http.complete())
    result = acquire(tmp_path, http, clock=clock)
    assert result["status"] == "NMR_SAVED" and clock.value >= 1
    assert len(http.starts) == 1


@pytest.mark.parametrize("activity", [
    {"Status": {"InProgress": True, "LastAcquireSuccessful": True}},
    {"Status": {"InProgress": "unknown", "LastAcquireSuccessful": True}},
])
def test_later_busy_or_unknown_activity_rearms_pending_during_delayed_raw(tmp_path, http, activity):
    http.returns("GET", STATUS, lambda _: http.complete(raw=""))
    http.returns("GET", SERVICE,
                 {"Status": {"InProgress": False, "LastAcquireSuccessful": True}}, activity)
    state = {}
    first = acquire(tmp_path, http, state=state)
    assert first["status"] != "NMR_SAVED" and state.get("pending")
    second = acquire(tmp_path, http, state=state, filename="next.dx")
    assert second["status"] == "NMR_FAILED" and not second["requested"]
    assert len(http.starts) == 1 and state.get("pending")


@pytest.mark.parametrize("receipt", [{}, {"ExperimentNumber": 99, "TimeStamp": "other"}])
def test_raw_retrieval_rechecks_the_completed_receipt(tmp_path, http, receipt):
    http.returns("GET", STATUS, lambda _: http.complete(), lambda _: http.complete(receipt=receipt))
    result = acquire(tmp_path, http)
    assert result["status"] != "NMR_SAVED"
    assert len(http.starts) == 1 and not (tmp_path / "observation.dx").exists()


def test_current_local_export_can_be_used_after_matching_completion(tmp_path, http):
    def accepted(settings):
        Path(settings["ExportFilename"]).write_bytes(RAW.encode())
        return http.accept(settings)
    http.returns("PUT", START, accepted)
    http.returns("GET", STATUS, lambda _: http.complete(raw=""))
    result = acquire(tmp_path, http)
    assert result["status"] == "NMR_SAVED" and Path(result["path"]).read_bytes() == RAW.encode()


def test_raw_write_failure_does_not_repeat_status_or_start_after_completion(tmp_path, http, monkeypatch):
    original = Path.open
    def denied(path, mode="r", *args, **kwargs):
        if mode == "x":
            raise PermissionError("raw directory is read-only")
        return original(path, mode, *args, **kwargs)
    monkeypatch.setattr(Path, "open", denied)
    state, clock = {}, Clock()
    result = acquire(tmp_path, http, state=state, clock=clock)
    assert result["status"] == "NMR_FAILED" and "raw directory is read-only" in result["reason"]
    assert len(http.calls) == 7 and len(http.starts) == 1 and clock.value == 0
    assert not state.get("pending")


def test_http_timeout_is_clamped_and_original_client_config_restored(tmp_path, http):
    clock = Clock()
    http.on_request = lambda *_: clock.sleep(2)
    client = NmrRpcClient(NmrRpcConfig(host="example.invalid", timeout=40))
    original = client.config
    result = acquire(tmp_path, http, clock=clock, client=client)
    assert result["status"] == "NMR_FAILED" and not result["requested"]
    assert [timeout for _, _, _, timeout in http.calls] == [2, 2, 1]
    assert client.config == original


def test_whitespace_prefixed_json_is_decoded_locally(tmp_path, http):
    def status(_):
        response = Response(http.complete())
        response.body = b" \r\n" + response.body
        response.headers["Content-Type"] = "text/plain"
        return response
    http.returns("GET", STATUS, status)
    assert acquire(tmp_path, http)["status"] == "NMR_SAVED"


def test_diagnostic_evidence_bounds_spectra_arrays_and_unknown_values():
    raw = "##TITLE=SECRET_RAW_DATA\n" * 10000
    status = {"NewStatusWrapper": {"Active": "unsupported"}, "JDX_FileContents_TD": raw,
              "JDX_FileContents_FD": raw, "Peaks": [999123] * 10000,
              "message": "x" * 20000, "more": list(range(10000))}
    evidence = _evidence(status)
    assert evidence["NewStatusWrapper"] == {"Active": "unsupported"}
    assert "SECRET_RAW_DATA" not in repr(evidence) and "999123" not in repr(evidence)
    assert len(repr(evidence)) < 1500


def test_accelerated_six_hour_schedule_drives_25_http_acquisitions_and_review_paths(tmp_path, http):
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location("demo_http_schedule", ROOT / "scripts/10_stage2only.py")
    demo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(demo)
    cfg = demo.load_config()
    cfg["analysis"]["enabled"] = True
    client = NmrRpcClient(NmrRpcConfig(host="example.invalid"))
    state, saved = {}, []
    def acquire_http(nmr_cfg, path, *, clock, emit):
        result = acquire_nmr(nmr_cfg, path, state=state, client=client,
                             now=clock.now, sleep=clock.sleep, emit=emit)
        if result["status"] == "NMR_SAVED":
            saved.append(Path(result["path"]))
        return result
    watched = {}
    def review(_run, _config, raw_dir, _emit):
        watched["raw_dir"] = raw_dir
        return None
    result = demo.run_experiment(cfg, mock=True, output_dir=tmp_path / "run", acquisition=acquire_http,
                                 analysis_launcher=review)
    assert result["scheduled_observations"] == result["attempted_observations"] == result["nmr_saved"] == 25
    assert [row["scheduled_seconds"] for row in result["observations"]] == list(range(0, 21601, 900))
    assert len(http.starts) == 25 and all(settings["NumberOfScans"] == 8 for settings in http.starts)
    assert len(saved) == 25 and all(path.parent == watched["raw_dir"] for path in saved)
    assert all(path.read_bytes() == RAW.encode() for path in saved)
    assert result["elapsed_seconds"] == 21600 + 452
    assert not state.get("pending")
