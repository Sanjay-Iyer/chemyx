"""Direct iFlow acquisition for the attended Stage 2 demo.

There are no pre-START status checks, ownership locks or historical permission
files. Only this invocation's unresolved START is retained in memory. Completion
uses the documented receipt, scan count and Service/Acquire boolean, not a
guessed iFlow ResultCode enum. All monitoring occurs after the relevant START.
The monotonic budget covers setup, START, completion and raw retrieval. Existing
urllib socket timeouts are capped to its remainder; DNS, a continuously
trickling HTTP response and filesystem calls are not hard wall-clock bounded.
"""
from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import time

from ..instruments.nmr import (
    NmrRpcClient, NmrRpcConfig, build_iflow_1d_settings,
    build_iflow_experiment_settings,
)

_IDENTITY = ("ExperimentNumber", "TimeStamp")


def _decoded(value):
    """The existing client can return whitespace-prefixed JSON as text."""
    if isinstance(value, str):
        try:
            return json.loads(value.lstrip("\ufeff \t\r\n"))
        except (ValueError, TypeError):
            pass
    return value


def _evidence(status):
    """Keep unknown response shapes/types for troubleshooting, never full spectra."""
    remaining = 80

    def bounded(value, depth=0, key=""):
        nonlocal remaining
        remaining -= 1
        if remaining < 0:
            return "<evidence item limit>"
        if (key.lower().startswith("jdx_filecontents") or key in {"Jcamp", "JCAMP", "DataString"}
                or isinstance(value, str) and value.lstrip().startswith("##")
                or "peak" in key.lower() and isinstance(value, list) and len(value) > 12):
            return f"<omitted {type(value).__name__}; length={len(value) if hasattr(value, '__len__') else '?'}>"
        if isinstance(value, (dict, list)) and depth >= 4:
            return f"<{type(value).__name__} depth limit>"
        if isinstance(value, dict):
            result = {str(name)[:120]: bounded(child, depth + 1, str(name))
                      for name, child in list(value.items())[:12]}
            if len(value) > 12:
                result["<more keys>"] = len(value) - 12
            return result
        if isinstance(value, list):
            return [bounded(child, depth + 1) for child in value[:6]] + (
                [f"<{len(value) - 6} more items>"] if len(value) > 6 else [])
        return value[:256] + "<truncated>" if isinstance(value, str) and len(value) > 256 else value

    return bounded(status)


def _receipt(value):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in _IDENTITY
            if isinstance(value.get(key), (str, int, float))
            and not isinstance(value[key], bool) and value[key] != ""}


def _raw_fid(payload):
    """Require FID type, a nonempty data table and END; never process spectra."""
    if not isinstance(payload, dict):
        return None
    for key in ("JDX_FileContents_TD", "Jcamp", "JCAMP", "DataString", "JDX_FileContents"):
        text = payload.get(key)
        if not isinstance(text, str) or not text.lstrip().startswith("##"):
            continue
        kind = next((line.partition("=")[2].upper() for line in text.splitlines()
                     if line.partition("=")[0].upper().replace(" ", "") == "##DATATYPE"), "")
        if "FID" not in kind and "FREE INDUCTION DECAY" not in kind:
            continue
        in_table, has_data, ended = False, False, False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("##"):
                label = stripped.partition("=")[0].upper().replace(" ", "")
                in_table = label in {"##XYDATA", "##DATATABLE", "##XYPOINTS"}
                ended = ended or label == "##END"
            elif in_table and stripped and not stripped.startswith("$$"):
                has_data = has_data or any(character.isdigit() for character in stripped)
        if has_data and ended:
            return text
    return None


def acquire_nmr(cfg, path, *, state=None, client=None, now=time.monotonic,
                sleep=time.sleep, emit=lambda message: None):
    """Configure and START once, then monitor that acquisition only.

    Pass one ``state`` dictionary throughout a run. An uncertain dispatch stays
    pending until its specific completion is proved; no files authorize START.
    A later observation may finish monitoring that prior START before issuing
    its own. Recovered raw data remains associated with the original output.
    """
    state = {} if state is None else state
    result = dict(status="NMR_FAILED", path=None, requested=False,
                  start_state="not_sent", reason="")
    original_config = None
    step = "preparing raw output"
    try:
        path = Path(path).resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"Raw output exists; preserved without START: {path}")
        http_timeout = float(cfg.get("http_timeout_seconds", 10))
        budget = float(cfg.get("acquisition_timeout_seconds", 300))
        poll = float(cfg.get("poll_seconds", 2))
        if not all(0 < value < float("inf") for value in (http_timeout, budget, poll)):
            raise ValueError("NMR HTTP timeout, acquisition timeout and poll interval must be finite and positive")
        deadline = now() + budget
        client = client or NmrRpcClient(NmrRpcConfig(host=cfg["host"], port=int(cfg.get("port", 5000)),
            scheme=cfg.get("scheme", "http"), timeout=http_timeout))
        original_config = client.config if isinstance(client, NmrRpcClient) else None

        def call(method, *args):
            nonlocal step
            step = method.__name__
            remaining = deadline - now()
            if remaining <= 0:
                raise TimeoutError(f"NMR acquisition budget exhausted ({budget:g}s)")
            if original_config is not None:
                client.config = replace(original_config, timeout=min(http_timeout, remaining))
            value = _decoded(method(*args))
            if now() > deadline:
                raise TimeoutError(f"NMR acquisition budget exhausted during {step}")
            return value

        def belongs(status, attempt):
            if not isinstance(status, dict):
                return False
            receipt = status.get("OriginalReceipt")
            if not isinstance(receipt, dict):
                return False
            current, expected = _receipt(receipt), attempt["receipt"]
            settings = receipt.get("Settings")
            export = settings.get("ExportFilename") if isinstance(settings, dict) else None
            same_path = isinstance(export, str) and Path(export).resolve() == Path(attempt["path"])
            if export is not None and not same_path:
                return False
            if expected:
                if not all(current.get(key) == value for key, value in expected.items()):
                    return False
                full_identity = (all(key in expected for key in _IDENTITY)
                                 and isinstance(expected["TimeStamp"], str) and bool(expected["TimeStamp"].strip()))
                if not full_identity and not same_path:
                    return False  # A partial receipt can refer to stale data.
                # A repeated old receipt alone cannot identify another run.
                if current == attempt.get("previous_receipt") and not same_path:
                    return False
            elif not same_path:
                return False  # Lost reply: only this attempt's unique echoed path can identify it.
            count = status.get("NumberOfScansRun")
            return type(count) is int and count >= attempt["scans"]

        def finish(attempt):
            nonlocal step
            completed = False
            reason = "waiting for this acquisition's receipt and requested scan count"
            last_count = None
            while now() < deadline:
                try:
                    status = call(client.iflow_experiment_status)
                except Exception as exc:
                    reason = f"ExperimentStatus: {type(exc).__name__}: {exc}"
                else:
                    result["final"] = _evidence(status)
                    if belongs(status, attempt):
                        count = status["NumberOfScansRun"]
                        if count != last_count:
                            emit(f"NMR: {count}/{attempt['scans']} scans reported for this acquisition")
                            last_count = count
                        activity = call(client.acquire_status)
                        result["acquire_status"] = _evidence(activity)
                        activity = activity.get("Status") if isinstance(activity, dict) else None
                        if (isinstance(activity, dict) and activity.get("InProgress") is False
                                and activity.get("LastAcquireSuccessful") is True):
                            # Re-read the same acquisition after the documented
                            # activity indication; never retrieve a stale FID.
                            status = call(client.iflow_experiment_status)
                            if not belongs(status, attempt):
                                state["pending"] = attempt
                                raise RuntimeError("acquisition receipt/scans changed during raw retrieval")
                            if not completed:
                                emit("NMR: Acquisition complete; retrieving raw FID")
                            completed = True
                            state["pending"] = None
                            state["last_receipt"] = _receipt(status.get("OriginalReceipt"))
                            text = _raw_fid(status)
                            target = Path(attempt["path"])
                            if text:
                                destination = target if not target.exists() else target.with_suffix(".rpc.dx")
                                step = "saving raw FID"
                                with destination.open("x", encoding="utf-8", newline="") as output:
                                    output.write(text)
                                emit(f"NMR: Raw data saved: {destination}")
                                return dict(status="NMR_SAVED", path=str(destination), reason="Raw FID saved")
                            if target.is_file() and target.stat().st_size:
                                text = target.read_text(encoding="utf-8", errors="replace")
                                if _raw_fid({"Jcamp": text}):
                                    emit(f"NMR: Raw data saved: {target}")
                                    return dict(status="NMR_SAVED", path=str(target), reason="Current local raw FID saved")
                            reason = "acquisition complete but valid raw FID is not yet available"
                        else:
                            state["pending"] = attempt
                            completed = False
                            reason = f"Service/Acquire has not confirmed completed acquisition: {_evidence(activity)!r}"
                    else:
                        state["pending"] = attempt
                        completed = False
                        reason = "ExperimentStatus does not yet match this START and requested scan count"
                sleep(max(0, min(poll, deadline - now())))
            return dict(status="NMR_MISSING" if completed else "NMR_FAILED", path=None,
                        reason=f"{reason}; acquisition budget exhausted ({budget:g}s)")

        if state.get("pending"):
            emit("NMR: Continuing completion monitoring for the previous unresolved START; no new START sent")
            previous = state["pending"]
            recovered = finish(previous)
            if recovered["status"] == "NMR_SAVED":
                recovered["original_path"] = previous["path"]
                result["recovered_previous"] = recovered
            if state.get("pending"):
                result["reason"] = "Previous START remains unresolved; " + recovered["reason"]
                emit("NMR FAILED: " + result["reason"])
                return result

        emit("NMR: Configuring acquisition")
        one_d = call(client.iflow_1d_settings)
        run = call(client.iflow_experiment_settings)
        if not isinstance(one_d, dict) or not isinstance(run, dict):
            raise ValueError("NMR settings templates are not JSON objects")
        gain = cfg.get("gain", 12)
        one_d = build_iflow_1d_settings(one_d, receiver_gain=gain,
            auto_gain=cfg.get("auto_gain", False), export_filename=str(path))
        run = build_iflow_experiment_settings(run, scans=cfg.get("scans", 8), receiver_gain=gain,
            spectral_center=cfg.get("center_ppm", 5), sweep_width=cfg.get("width_ppm", 20), export_filename=str(path))
        call(client.set_iflow_1d_settings, one_d)
        emit(f"NMR: {run['NumberOfScans']} scans configured")
        if now() >= deadline:
            raise TimeoutError("NMR acquisition budget exhausted before START")
        attempt = dict(path=str(path), receipt={}, scans=run["NumberOfScans"],
                       previous_receipt=state.get("last_receipt"))
        state["pending"] = attempt  # Set before dispatch, even if the HTTP reply is lost.
        result.update(requested=True, start_state="uncertain")
        emit("NMR: Sending START (once)")
        started = call(client.run_iflow_experiment, run)
        result.update(start_state="reply_received", start_reply=_evidence(started))
        emit(f"NMR: START response: {_evidence(started)!r}")
        attempt["receipt"] = _receipt(started)
        if isinstance(started, dict) and (started.get("Error") or started.get("error")
                or started.get("Success") is False or started.get("Status") == "Error"):
            attempt["receipt"] = {}  # A rejection cannot vouch for a current acquisition identity.
            raise RuntimeError(f"START server response: {_evidence(started)!r}")
        result.update(finish(attempt))
        if result["status"] != "NMR_SAVED":
            emit("NMR FAILED: " + result["reason"])
        return result
    except Exception as exc:
        result["reason"] = f"{step}: {type(exc).__name__}: {exc}"
        emit("NMR FAILED: " + result["reason"])
        return result
    finally:
        result["unresolved_start"] = bool(state.get("pending"))
        if original_config is not None:
            client.config = original_config
