"""Nanalysis/NMReady RPC client helpers.

The endpoints implemented here come from
``docs/reference/nmr_rpc_api/html/rpc-api.md``. The client uses
only the Python standard library so it can run on a fresh instrument laptop
after installing the existing repo dependencies.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, parse, request

from .. import config


class NmrRpcError(Exception):
    """Raised when the NMReady RPC server cannot complete a request."""


@dataclass(frozen=True)
class NmrRpcConfig:
    host: str = config.NMR_HOST
    port: int = config.NMR_PORT
    scheme: str = config.NMR_RPC_SCHEME
    timeout: float = config.NMR_RPC_TIMEOUT
    poll_seconds: float = config.NMR_RPC_POLL_SECONDS
    max_wait_seconds: float = config.NMR_RPC_MAX_WAIT_SECONDS

    @property
    def base_url(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"


class NmrRpcClient:
    """Thin HTTP wrapper for the documented NMReady RPC endpoints."""

    def __init__(self, rpc_config: NmrRpcConfig | None = None, base_url: str | None = None):
        self.config = rpc_config or NmrRpcConfig()
        self.base_url = (base_url or self.config.base_url).rstrip("/")

    def get(self, path: str, params: dict[str, Any] | None = None):
        return self._request("GET", path, params=params)

    def put(self, path: str, payload: Any | None = None, params: dict[str, Any] | None = None):
        return self._request("PUT", path, payload=payload if payload is not None else {}, params=params)

    def _request(
        self,
        method: str,
        path: str,
        payload: Any | None = None,
        params: dict[str, Any] | None = None,
    ):
        url = self._url(path, params)
        data = None
        headers = {"Accept": "application/json, text/plain, */*"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        try:
            with request.urlopen(req, timeout=self.config.timeout) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise NmrRpcError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            raise NmrRpcError(f"{method} {url} failed: {exc.reason}") from exc

        text = raw.decode("utf-8", errors="replace")
        if not text:
            return None
        if "json" in content_type.lower() or text[:1] in "[{":
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                pass
        return text

    def _url(self, path: str, params: dict[str, Any] | None = None) -> str:
        normalized = path if path.startswith("/") else f"/{path}"
        query = ""
        if params:
            query = "?" + parse.urlencode(
                {key: value for key, value in params.items() if value is not None}
            )
        return self.base_url + normalized + query

    # Status and setup endpoints.
    def ping(self):
        return self.get("/interfaces/iStatus/PingSpectrometer")

    def rpc_enabled(self):
        return self.get("/interfaces/iStatus/RpcEnabled")

    def rpc_active(self):
        return self.get("/interfaces/Service/RpcActive")

    def spectrometer_status(self):
        return self.get("/interfaces/iStatus/SpectrometerStatus")

    def startup_test_status(self):
        return self.get("/interfaces/iStatus/StartupTestStatus")

    def solvents(self):
        return self.get("/interfaces/iStatus/Solvents")

    # Experiment API, documented as the newer generic route.
    def experiment_list(self):
        return self.get("/interfaces/Experiment/List")

    def experiment_settings(self, experiment_name: str = config.NMR_DEFAULT_EXPERIMENT):
        quoted = parse.quote(experiment_name, safe="")
        return self.get(f"/interfaces/Experiment/{quoted}/Settings")

    def start_experiment(self, settings: dict[str, Any], experiment_name: str = config.NMR_DEFAULT_EXPERIMENT):
        quoted = parse.quote(experiment_name, safe="")
        return self.put(f"/interfaces/Experiment/{quoted}/Start", settings)

    def experiment_status(self):
        return self.get("/interfaces/Experiment/Status")

    def cancel_experiment(self):
        return self.put("/interfaces/Experiment/Cancel", {})

    def experiment_results(self):
        return self.get("/interfaces/Experiment/Results")

    def experiment_result(self, result_name: str, fmt: str = "jdx", data_type: str = "fid"):
        quoted = parse.quote(result_name, safe="")
        return self.get(
            f"/interfaces/Experiment/Results/{quoted}",
            params={"format": fmt, "type": data_type},
        )

    # iFlow route, useful on some NMReady software versions.
    def iflow_1d_settings(self):
        return self.get("/interfaces/iFlow/Settings/1D")

    def set_iflow_1d_settings(self, settings: dict[str, Any]):
        return self.put("/interfaces/iFlow/Settings/1D", settings)

    def iflow_experiment_settings(self):
        return self.get("/interfaces/iFlow/ExperimentSettings")

    def set_iflow_experiment_settings(self, settings: dict[str, Any]):
        return self.put("/interfaces/iFlow/ExperimentSettings", settings)

    def run_iflow_experiment(self, settings: dict[str, Any] | None = None):
        if settings is None:
            return self.get("/interfaces/iFlow/RunExperiment")
        return self.put("/interfaces/iFlow/RunExperiment", settings)

    def iflow_experiment_status(self):
        return self.get("/interfaces/iFlow/ExperimentStatus")

    def cancel_iflow_experiment(self):
        return self.put("/interfaces/iFlow/CancelExperiment", {})

    # Lower-level service route.
    def acquire(self):
        return self.put("/interfaces/Service/Acquire", {})

    def acquire_status(self):
        return self.get("/interfaces/Service/Acquire")

    def save_results(self, filename: str):
        return self.put("/interfaces/Service/SaveResults", {"Filename": filename})

    def service_fd(self):
        return self.get("/interfaces/Service/Fd")

    def service_td(self):
        return self.get("/interfaces/Service/Td")

    def td_stats(self):
        return self.get("/interfaces/Service/TdStats")

    def wait_for_idle(
        self,
        status_getter=None,
        poll_seconds: float | None = None,
        max_wait_seconds: float | None = None,
    ):
        """Poll an experiment/acquisition status endpoint until it looks idle."""
        getter = status_getter or self.experiment_status
        poll = self.config.poll_seconds if poll_seconds is None else poll_seconds
        max_wait = self.config.max_wait_seconds if max_wait_seconds is None else max_wait_seconds
        deadline = time.monotonic() + max_wait
        last_status = None

        while True:
            last_status = getter()
            if not status_indicates_running(last_status):
                return last_status
            if time.monotonic() >= deadline:
                raise NmrRpcError(f"Timed out waiting for NMR run to finish: {last_status!r}")
            time.sleep(poll)


def status_indicates_running(status: Any) -> bool:
    """Best-effort status parser across documented NMReady endpoints."""
    if status is None:
        return False
    if isinstance(status, dict):
        for key in ("InProgress", "Running", "running", "IsRunning"):
            value = _find_key(status, key)
            if isinstance(value, bool):
                return value
        percent = _find_key(status, "PercentComplete")
        if isinstance(percent, (int, float)) and percent < 100:
            return True
        result_code = _find_key(status, "ResultCode")
        if isinstance(result_code, int):
            return result_code != 0
    return False


def build_1d_experiment_settings(
    template: dict[str, Any],
    scans: int | None = None,
    solvent: str | None = None,
    spectral_center: float | None = None,
    sweep_width: float | None = None,
    receiver_gain: float | None = None,
    export_filename: str | None = None,
) -> dict[str, Any]:
    """Patch a documented ``Experiment/<name>/Settings`` response for a 1D run."""
    settings = json.loads(json.dumps(template))
    instrument = settings.get("setup", {}).get("instrument", {})
    params = settings.get("setup", {}).get("params", {})

    if scans is not None:
        instrument["numScans"] = int(scans)
    if solvent is not None:
        instrument["activeSolvent"] = solvent
    if spectral_center is not None:
        instrument["spectralCenter"] = float(spectral_center)
    if sweep_width is not None:
        instrument["sweepWidth"] = float(sweep_width)
    if receiver_gain is not None:
        current = params.get("receiverGain")
        if isinstance(current, list) and len(current) >= 2:
            params["receiverGain"] = [receiver_gain, current[1]]
        else:
            params["receiverGain"] = receiver_gain
    if export_filename is not None:
        metadata = settings.setdefault("metadata", {})
        metadata["resultName"] = Path(export_filename).stem
        params["ExportFilename"] = export_filename
    return settings


def build_iflow_1d_settings(
    template: dict[str, Any],
    receiver_gain: float | None = None,
    auto_gain: bool | None = None,
    export_filename: str | None = None,
) -> dict[str, Any]:
    """Patch ``iFlow/Settings/1D`` without dropping instrument-specific keys."""
    settings = json.loads(json.dumps(template))
    if receiver_gain is not None:
        settings["ReceiverGain"] = float(receiver_gain)
    if auto_gain is not None:
        settings["AutoGain"] = bool(auto_gain)
    if export_filename is not None:
        settings["ExportFilename"] = export_filename
    return settings


def build_iflow_experiment_settings(
    template: dict[str, Any],
    scans: int | None = None,
    receiver_gain: float | None = None,
    spectral_center: float | None = None,
    sweep_width: float | None = None,
    export_filename: str | None = None,
) -> dict[str, Any]:
    """Patch ``iFlow/ExperimentSettings`` or ``iFlow/RunExperiment`` settings."""
    settings = json.loads(json.dumps(template))
    if scans is not None:
        settings["NumberOfScans"] = int(scans)
    if receiver_gain is not None:
        settings["ReceiverGain"] = float(receiver_gain)
    if spectral_center is not None:
        settings["SpectralCentreInPpm"] = float(spectral_center)
    if sweep_width is not None:
        settings["SpectralWidthInPpm"] = float(sweep_width)
    if export_filename is not None:
        settings["ExportFilename"] = export_filename
    return settings


def example_1d_experiment_settings() -> dict[str, Any]:
    """Return a documented 1D settings shape for offline command checks."""
    return {
        "metadata": {
            "id": "1D",
            "nucleus": "1H",
            "resultName": "1D_Result",
            "type": "experiment",
        },
        "setup": {
            "instrument": {
                "activeSolvent": config.NMR_DEFAULT_SOLVENT,
                "configGroup": "1H",
                "interscanTime": 6,
                "numPoints": 2048,
                "numScans": config.NMR_DEFAULT_SCANS,
                "spectralCenter": config.NMR_DEFAULT_SPECTRAL_CENTER,
                "sweepWidth": config.NMR_DEFAULT_SWEEP_WIDTH,
            },
            "params": {
                "dummyScans": 0,
                "pulseAngleOrWidth": [90, "deg"],
                "receiverGain": [10, "AUTO"],
            },
        },
    }


def example_iflow_experiment_settings() -> dict[str, Any]:
    """Return a documented iFlow 1D settings shape for offline checks."""
    return {
        "ActiveTimeScanInSeconds": 2.5559999644756317,
        "Apodization": 0.2,
        "DigitalResolutionInHz": 0.0762939453125,
        "Experiment": 1,
        "ExperimentName": "1D",
        "NumberOfPoints": 2048,
        "NumberOfScans": config.NMR_DEFAULT_SCANS,
        "PeakIntegrationMethod": 1,
        "PulseWidthInMicroseconds": 16.628877639770508,
        "ReceiverGain": 12,
        "ScanDelayInSeconds": 0.0,
        "Solvent": 8,
        "SolventGroup": 0,
        "Nucleus": "1H",
        "LockNucleus": "2H",
        "SpectralCentreInPpm": config.NMR_DEFAULT_SPECTRAL_CENTER,
        "SpectralWidthInPpm": config.NMR_DEFAULT_SWEEP_WIDTH,
        "ZeroFillingFactor": 7.0,
    }


def example_iflow_1d_settings() -> dict[str, Any]:
    """Return a documented iFlow Settings/1D shape for offline checks."""
    return {
        "AutoBaseline": False,
        "AutoGain": False,
        "AutoPhase": False,
        "CurrentGain": 12.0,
        "PulseAngle": 85.92698762441455,
        "PulseWidth": 15.0,
        "ReceiverGain": 12.0,
        "ExportFilename": "",
    }


def extract_text_payload(payload: Any) -> str:
    """Extract a JCAMP/CSV/text payload from common RPC response shapes."""
    if payload is None:
        return ""
    if isinstance(payload, str):
        return payload
    if isinstance(payload, dict):
        for key in (
            "Value",
            "DataString",
            "JDX_FileContents_FD",
            "JDX_FileContents_TD",
            "JDX_FileContents",
            "Jcamp",
            "JCAMP",
            "Data",
        ):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
        return json.dumps(payload, indent=2)
    return str(payload)


def _find_key(value: Any, key: str):
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = _find_key(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_key(child, key)
            if found is not None:
                return found
    return None
