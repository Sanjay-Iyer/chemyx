"""04/05-style acquisition for 12/13, without the standalone CLI or locks.

Completion is delegated to the unchanged driver's wait_for_idle. Its permissive
unknown-status handling is NOT proof of completion: a returned status needs a
recognized legacy state field and a valid FID different from the baseline.
Missing/stale/unknown results retain this invocation's pending START in memory.
No Service/Acquire, receipt matching or scan-count completion checks are used.
"""
from __future__ import annotations

from dataclasses import replace
import math
from pathlib import Path

from ..instruments.nmr import (NmrRpcClient, NmrRpcConfig, _find_key,
    build_iflow_1d_settings, build_iflow_experiment_settings, status_indicates_running)


def raw_fid(payload):
    """Select raw TD, never FD; require FID type, data table/body and END."""
    if not isinstance(payload, dict):
        return None
    for key in ("JDX_FileContents_TD", "Jcamp", "JCAMP", "DataString", "JDX_FileContents"):
        text = payload.get(key)
        if not isinstance(text, str) or not text.lstrip().startswith("##"):
            continue
        kind, table, data, end = "", False, False, False
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("##"):
                name, _, value = stripped.partition("=")
                name = name.upper().replace(" ", "")
                if name == "##DATATYPE":
                    kind = value.upper()
                table = name in {"##XYDATA", "##DATATABLE", "##XYPOINTS"}
                end = end or name == "##END"
            elif table and stripped and not stripped.startswith("$$"):
                data = data or any(c.isdigit() for c in stripped)
        if ("FID" in kind or "FREE INDUCTION DECAY" in kind) and data and end:
            return text
    return None


def known_legacy_state(status):
    """Recognize fields already used by the driver, without changing its rules.

    This does not classify/approve idle before START. It rejects an unknown
    response that the legacy parser otherwise treats as 'not running'. Existing
    legacy precedence (including conflicting flags) remains a documented limit.
    """
    if not isinstance(status, dict):
        return False
    if any(type(_find_key(status, key)) is bool for key in ("InProgress", "Running", "running", "IsRunning")):
        return True
    percent = _find_key(status, "PercentComplete")
    return (type(_find_key(status, "ResultCode")) is int or
            type(percent) in (int, float) and math.isfinite(percent))


def legacy_acquire(cfg, path, *, state, client, now, sleep, emit):
    """One START; the actual legacy wait_for_idle; fresh raw FID saved unchanged.

    State is local to one run. A later call can monitor the same pending attempt
    and save its original file before issuing the next observation's START.
    The one timeout covers this call's setup, waiting and retrieval. No separate
    late-data polling is added: missing/stale data is reported just as in 04/05.
    """
    result = dict(status="NMR_FAILED", path=None, requested=False, method="legacy_simple", reason="")
    original = client.config
    step = "configuration"
    try:
        target = Path(path).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise FileExistsError(f"Existing output preserved; no START: {target}")
        budget, poll = float(cfg["acquisition_timeout_seconds"]), float(cfg["poll_seconds"])
        if not all(math.isfinite(v) and v > 0 for v in (budget, poll, float(cfg["http_timeout_seconds"]))):
            raise ValueError("NMR timeout and poll settings must be finite and positive")
        deadline = now() + budget

        def call(method, *args):
            nonlocal step
            step = method.__name__
            remaining = deadline - now()
            if remaining <= 0:
                raise TimeoutError("Legacy acquisition timeout exhausted")
            client.config = replace(original, timeout=min(float(cfg["http_timeout_seconds"]), remaining))
            value = method(*args)
            if now() > deadline:
                raise TimeoutError(f"Legacy acquisition timeout during {step}")
            return value

        def finish(attempt):
            def read_status():
                value = call(client.iflow_experiment_status)
                fields = value if isinstance(value, dict) else {}
                emit(f"NMR: Legacy status {'WAITING' if status_indicates_running(value) else 'NOT RUNNING'}; "
                     f"ResultCode={fields.get('ResultCode')!r}; scans reported={fields.get('NumberOfScansRun')!r}")
                return value
            emit("NMR: Waiting for completion — legacy wait_for_idle()")
            final = client.wait_for_idle(status_getter=read_status, poll_seconds=poll,
                                         max_wait_seconds=max(.000001, deadline - now()))
            if not known_legacy_state(final):
                return dict(status="NMR_FAILED", path=None,
                    reason="Legacy parser returned on an unsupported status; completion remains uncertain")
            text = raw_fid(final)
            if not text:
                return dict(status="NMR_MISSING", path=None,
                    reason="Legacy wait returned but valid raw FID is missing; START remains pending")
            if attempt["baseline"] and text == attempt["baseline"]:
                return dict(status="NMR_FAILED", path=None,
                    reason="Returned raw FID is unchanged from baseline; START remains pending")
            emit("NMR: Legacy completion and fresh raw FID confirmed; saving data")
            state["pending"] = None
            destination = Path(attempt["path"])
            if destination.exists():
                destination = destination.with_suffix(".rpc.dx")
            with destination.open("x", encoding="utf-8", newline="") as output:
                output.write(text)
            emit(f"NMR: Raw .dx saved: {destination}")
            return dict(status="NMR_SAVED", path=str(destination), reason="Fresh legacy raw FID saved")

        if state.get("pending"):
            emit("NMR: Monitoring the previous pending START; no new START sent")
            previous = state["pending"]
            recovered = finish(previous)
            if recovered["status"] == "NMR_SAVED":
                result["recovered_previous"] = dict(recovered, original_path=previous["path"])
            if state.get("pending"):
                result.update(reason="Previous START unresolved: " + recovered["reason"])
                emit("NMR FAILED: " + result["reason"])
                return result
        emit("NMR: Reading baseline FID for freshness comparison (no idle approval)")
        baseline = raw_fid(call(client.iflow_experiment_status))
        emit(f"NMR: Configuring {cfg['scans']} scan(s) — legacy method")
        one_d, run = call(client.iflow_1d_settings), call(client.iflow_experiment_settings)
        if not isinstance(one_d, dict) or not isinstance(run, dict):
            raise ValueError("NMR settings templates must be JSON objects")
        one_d = build_iflow_1d_settings(one_d, receiver_gain=cfg["gain"], auto_gain=cfg["auto_gain"], export_filename=str(target))
        run = build_iflow_experiment_settings(run, scans=cfg["scans"], receiver_gain=cfg["gain"],
            spectral_center=cfg["center_ppm"], sweep_width=cfg["width_ppm"], export_filename=str(target))
        call(client.set_iflow_1d_settings, one_d)
        if now() >= deadline:
            raise TimeoutError("Legacy acquisition timeout exhausted before START")
        state["pending"] = dict(path=str(target), baseline=baseline)
        result["requested"] = True
        emit("NMR: Sending START once — legacy method")
        reply = call(client.run_iflow_experiment, run)
        emit(f"NMR: START response: {reply!r}")
        if isinstance(reply, dict) and (reply.get("Error") or reply.get("error") or reply.get("Success") is False or reply.get("Status") == "Error"):
            raise RuntimeError(f"START rejected: {reply!r}")
        result.update(finish(state["pending"]))
        if result["status"] != "NMR_SAVED":
            emit("NMR FAILED: " + result["reason"])
        return result
    except Exception as exc:
        result["reason"] = f"{step}: {type(exc).__name__}: {exc}"
        emit("NMR FAILED: " + result["reason"])
        return result
    finally:
        result["unresolved_start"] = bool(state.get("pending"))
        client.config = original


class MockLegacyClient(NmrRpcClient):
    """No-network HTTP client using the real wait_for_idle on fresh fixture copies.

    Each mock START adds a comment so baseline comparisons test fresh data. The
    original fixture/LONG DATE are unchanged; no artificial acquisition times.
    """
    def __init__(self, raw, clock):
        super().__init__(NmrRpcConfig(host="mock.invalid"))
        self.raw, self.clock, self.calls, self.counter, self.active = raw, clock, [], 0, False
        self.current = ""

    def _request(self, method, path, payload=None, params=None):
        self.calls.append((method, path, payload))
        if method == "GET" and path in {"/interfaces/iFlow/Settings/1D", "/interfaces/iFlow/ExperimentSettings"}:
            return {}
        if method == "PUT" and path == "/interfaces/iFlow/Settings/1D":
            return {}
        if method == "PUT" and path == "/interfaces/iFlow/RunExperiment":
            self.counter += 1
            self.current = self.raw.replace("##TITLE=", f"##TITLE=MOCK legacy acquisition {self.counter} ", 1)
            self.active = True
            return {"ResultCode": 0}
        if method == "GET" and path == "/interfaces/iFlow/ExperimentStatus":
            if self.active:
                self.clock.sleep(80)
                self.active = False
            return {"ResultCode": 0, "JDX_FileContents_TD": self.current}
        raise AssertionError(f"Unexpected mock NMR HTTP request: {method} {path}")


class LegacyAcquisition:
    """The same injected acquisition callable for both new scripts."""
    def __init__(self, cfg, *, mock, clock, fixture, client=None):
        self.state, self.last_result = {}, None
        self.client = client if client is not None else (MockLegacyClient(fixture.read_text(encoding="utf-8"), clock) if mock else
            NmrRpcClient(NmrRpcConfig(host=cfg["host"], port=cfg["port"], scheme=cfg["scheme"], timeout=cfg["http_timeout_seconds"])))

    def __call__(self, cfg, path, *, clock, emit):
        self.last_result = legacy_acquire(cfg, path, state=self.state, client=self.client,
                                         now=clock.now, sleep=clock.sleep, emit=emit)
        return self.last_result
