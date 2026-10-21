"""Standalone NMR tests: a read-only connection test and single 1- or 4-scan runs.

Settings come from the same preset, machine YAML and ``NMR_*`` environment
rules as 06/08. By default the acquisition tests call the simplified
``stage2_demo_nmr.acquire_nmr`` used by 10_stage2only and 11_3InstrumentCheck
(no status check before START); ``--experiment-worker`` calls the 06/08 worker
(``si6_fixed_time_nmr.acquire``) instead, for comparison. Tests 04/05 use the
simple legacy-style completion of the older three-instrument test: the driver's
``wait_for_idle`` on ExperimentStatus until ResultCode 0. Every HTTP exchange
is printed, explained and saved for debugging. No pump, needle, HOME or dose
record is opened, no spectrum is processed, and START is sent at most once.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import errno
import json
from pathlib import Path
import platform
import re
import socket
import sys
import time
from urllib import error, request

from .. import config
from ..instruments.nmr import (NmrRpcClient, NmrRpcConfig, NmrRpcError, build_iflow_1d_settings,
                               build_iflow_experiment_settings, status_indicates_running)
from ..runtime_state import write_json_atomic
from .si6_fixed_time_nmr import acquire, declared_type, is_fid_type, is_jcamp, raw_text, running_state

DEFAULT_EXPERIMENT = Path("configs/experiments/si6_stage2only_6h.yaml")
OUTPUT_ROOT = Path("results/nmr_standalone")
REPORT_NAME = "nmr_debug_report.json"
STATUS_PATH = "/interfaces/iFlow/ExperimentStatus"
FLAGS = ("InProgress", "Running", "running", "IsRunning")
STATE_FIELDS = {*FLAGS, "ResultCode", "PercentComplete"}
# Bulky reply fields are summarized in printed and saved evidence.
BULKY_KEYS = {"JDX_FileContents_TD", "JDX_FileContents_FD", "JDX_FileContents", "Jcamp", "JCAMP", "DataString"}
UNREACHABLE = {errno.ENETUNREACH, errno.EHOSTUNREACH, 10051, 10065}


# --- Configuration ----------------------------------------------------------

def load_settings(experiment_config=None, machine_config=None, *, scans=None, host=None, port=None):
    """Return (NmrSettings, retrieval_wait, sources) resolved exactly like 06/08.

    Mirrors the NMR part of ``si6_automated_nmr.build_instrument_settings``
    with ``acquisition_only=True``; a test keeps the two identical.
    """
    path = config.resolve_repo_path(experiment_config or DEFAULT_EXPERIMENT)
    raw = config.read_mapping_config(path, "NMR source experiment")
    nmr = raw.get("nmr")
    if not isinstance(nmr, dict):
        raise config.ConfigError(f"{path} has no nmr: section")
    machine_value = machine_config or raw.get("machine_config")
    if not machine_value:
        raise config.ConfigError(f"{path} names no machine_config; pass --machine-config")
    machine_path = config.resolve_repo_path(machine_value)
    if not machine_path.is_file():
        # load_machine_config silently returns defaults for a missing file.
        raise config.ConfigError(f"Machine YAML not found: {machine_path} (the NMR IP address and port come from it)")
    machine = config.load_machine_config(machine_path).nmr
    try:
        values = dict(host=machine.host, port=machine.port, scheme=machine.scheme,
                      timeout=machine.timeout_seconds, poll_seconds=machine.poll_seconds,
                      max_wait_seconds=machine.max_wait_seconds, route=nmr["route"], scans=nmr["scans"],
                      receiver_gain=nmr["receiver_gain"], auto_gain=nmr["auto_gain"],
                      spectral_center=nmr["spectral_center"], sweep_width=nmr["sweep_width"],
                      result_type=nmr["result_type"], target_ppm=0.0)
    except KeyError as exc:
        raise config.ConfigError(f"{path} nmr: section is missing {exc}") from exc
    environment = config._explicit_env_nmr_overrides(acquisition_only=True)
    values.update(environment)
    command_line = {key: value for key, value in (("host", host), ("port", port), ("scans", scans))
                    if value is not None}
    values.update(command_line)
    settings = config.load_nmr_settings(load_local=False, **values)
    if settings.route != "iflow" or settings.result_type != "fid":
        raise config.ConfigError(f"{path} must use route iflow and result_type fid, like the 06/08 runs")
    if not str(settings.host).strip():
        raise config.ConfigError(f"NMR host is empty: set nmr.host in {machine_path}")
    if settings.scans < 1:
        raise config.ConfigError("NMR scans must be at least 1")
    for key in ("timeout", "poll_seconds", "max_wait_seconds"):
        if not getattr(settings, key) > 0:
            raise config.ConfigError(f"NMR {key} must be positive in {machine_path}")
    retrieval_wait = float(raw.get("retrieval_wait_seconds", 20))
    sources = {"experiment_config": str(path), "machine_config": str(machine_path),
               "environment_overrides": {key: str(value) for key, value in environment.items()},
               "command_line_overrides": command_line}
    return settings, retrieval_wait, sources


def print_settings(settings, retrieval_wait, sources, out=print, acquiring=True):
    out(f"  Preset (NMR settings)  : {_rel(sources['experiment_config'])}")
    out(f"  Machine YAML (address) : {_rel(sources['machine_config'])}")
    out(f"  NMR address            : {settings.scheme}://{settings.host}:{settings.port}")
    out(f"  Timing                 : HTTP timeout {settings.timeout:g} s, status poll every {settings.poll_seconds:g} s, "
        f"completion limit {settings.max_wait_seconds:g} s, raw-data wait {retrieval_wait:g} s")
    gain = "auto" if settings.auto_gain else f"{settings.receiver_gain:g} (auto gain off)"
    out(f"  Acquisition{'' if acquiring else ' (06/08)'}{' ' * (12 if acquiring else 4)}: {settings.scans} scan(s), "
        f"receiver gain {gain}, centre {settings.spectral_center:g} ppm, width {settings.sweep_width:g} ppm, iFlow raw FID"
        + ("" if acquiring else "  (nothing is acquired by this test)"))
    env = sources["environment_overrides"]
    out("  NMR_* environment      : " + (", ".join(f"{k}={v}" for k, v in env.items()) + "  <-- overrides the YAML"
                                       if env else "none (YAML values used)"))
    if sources["command_line_overrides"]:
        out(f"  Command-line overrides : {sources['command_line_overrides']}")


# --- Network ---------------------------------------------------------------

def network_check(host, port, timeout, scheme="http"):
    """Which laptop address routes to the NMR, is a proxy in the way, does the port accept?"""
    result = {"host": host, "port": port}
    try:
        # urllib (and so the experiment) silently uses Windows/environment proxies.
        proxy = request.getproxies().get(scheme)
        if proxy and not request.proxy_bypass(host):
            result["proxy"] = proxy
    except Exception:
        pass
    try:
        # A UDP connect only selects a route; no packet is sent.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect((host, port))
            result["local_address"] = probe.getsockname()[0]
    except OSError as exc:
        result["local_address_error"] = f"{type(exc).__name__}: {exc}"
    try:
        result["laptop_ipv4_addresses"] = sorted({info[4][0] for info in socket.getaddrinfo(
            socket.gethostname(), None, socket.AF_INET)})
    except OSError:
        pass
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            result["tcp"] = "open"
    except socket.gaierror as exc:
        result.update(tcp="bad_address", tcp_error=f"{type(exc).__name__}: {exc}")
    except (socket.timeout, TimeoutError) as exc:
        result.update(tcp="timeout", tcp_error=f"{type(exc).__name__}: {exc or 'timed out'}")
    except ConnectionRefusedError as exc:
        result.update(tcp="refused", tcp_error=f"{type(exc).__name__}: {exc}")
    except OSError as exc:
        code = getattr(exc, "winerror", None) or exc.errno
        result.update(tcp="unreachable" if code in UNREACHABLE else "error",
                      tcp_error=f"{type(exc).__name__}: {exc}")
    result["tcp_seconds"] = round(time.monotonic() - started, 3)
    return result


def print_network(net, out=print):
    local = net.get("local_address")
    if local:
        note = ""
        if str(net["host"]).startswith("169.254."):
            note = ("  (link-local, same network as the NMR)" if local.startswith("169.254.")
                    else "  <-- NOT a 169.254.x.x address: the Ethernet link to the NMR looks down")
        out(f"  Laptop address toward the NMR : {local}{note}")
    elif net.get("local_address_error"):
        out(f"  Laptop route to the NMR       : NONE ({net['local_address_error']})")
    if net.get("laptop_ipv4_addresses"):
        out(f"  All laptop IPv4 addresses     : {', '.join(net['laptop_ipv4_addresses'])}")
    if net.get("proxy"):
        out(f"  Web proxy for NMR requests    : {net['proxy']}  <-- requests would NOT go directly to the NMR")
    if net["tcp"] == "open":
        out(f"  TCP connection to NMR port    : open ({net['tcp_seconds']:.2f} s)")
    else:
        out(f"  TCP connection to NMR port    : {net['tcp'].upper()} after {net['tcp_seconds']:.1f} s "
            f"({net['tcp_error']})")


def network_findings(net):
    host, port = net["host"], net["port"]
    local = net.get("local_address") or ""
    ping = [f"Run `ping {host}` (separate command) to test the cable without Python."]
    if net["tcp"] == "open":
        if not net.get("proxy"):
            return []
        return [finding("WARN", f"Python would send NMR requests through the web proxy {net['proxy']}.",
                        "Windows or environment proxy settings apply to this address, but the NMR can only be "
                        "reached directly. HTTP failures below may come from the proxy, not the NMR.",
                        [f"Add {host} to the proxy exceptions (Windows Settings -> Network -> Proxy), or ask IT.",
                         "Run this test again: the 'Web proxy' line should disappear."])]
    if net["tcp"] == "bad_address":
        return [finding("FAIL", f"'{host}' is not a usable IP address or host name.",
                        "Python could not turn the configured NMR host into an address.",
                        ["Set nmr.host in the machine YAML to the NMR's IP address (normally 169.254.30.54)."])]
    if net["tcp"] == "refused":
        return [finding("FAIL", f"The NMR computer answered, but nothing accepts connections on port {port}.",
                        "The cable and IP address work. The NMReady software or its remote (RPC) server is not "
                        "running, or the port number is wrong.",
                        ["Make sure the NMReady software is open on the NMR touchscreen (restart it, or power-cycle "
                         "the NMR if it is frozen).",
                         f"Check nmr.port in the machine YAML (normally 5000; it is {port} now).",
                         "Then run the connection test again."])]
    if str(host).startswith("169.254.") and local and not local.startswith("169.254."):
        return [finding("FAIL", f"No network link to the NMR ({host}).",
                        f"Windows would send NMR traffic through {local}, which is not a 169.254.x.x address. "
                        "The laptop's Ethernet adapter is not connected to the NMR (cable unplugged, wrong adapter, "
                        "adapter disabled, or the NMR is off).",
                        ["Check the Ethernet cable from the laptop (or its USB-Ethernet adapter) to the NMR, at both ends.",
                         "Check the NMR is powered on and NMReady has finished starting.",
                         "Run `ipconfig` (separate command): the Ethernet adapter should show a 169.254.x.x address, "
                         "not 'Media disconnected'.",
                         *ping])]
    if net["tcp"] == "unreachable" or net.get("local_address_error"):
        return [finding("FAIL", f"Windows has no route to {host}.",
                        "The network adapter that should reach the NMR is down or has no address.",
                        ["Check the Ethernet cable and that the NMR is on.",
                         "Run `ipconfig` (separate command) and look for the adapter connected to the NMR.", *ping])]
    return [finding("FAIL", f"Nothing answered at {host}:{port} ({net['tcp_error']}).",
                    "The laptop is on a network that should reach the NMR, but the NMR did not reply. It may be off, "
                    "still booting, on a different IP address, or blocked by a firewall.",
                    ["Check the NMR is on and NMReady is open.",
                     f"Confirm the NMR's IP address in its network settings matches nmr.host ({host}) in the machine YAML.",
                     *ping])]


# --- HTTP evidence ---------------------------------------------------------

def bounded(value, depth=0):
    """JSON-safe copy small enough to print and save; spectra become summaries."""
    if isinstance(value, dict):
        if depth >= 6:
            return f"<object with {len(value)} keys>"
        copy = {}
        for index, (key, child) in enumerate(value.items()):
            if index == 60:
                copy["<truncated>"] = f"{len(value) - 60} more keys"
                break
            if key in BULKY_KEYS and isinstance(child, str) and child:
                copy[key] = f"<text, {len(child)} characters, starts {child[:40]!r}>"
            else:
                copy[str(key)] = bounded(child, depth + 1)
        return copy
    if isinstance(value, (list, tuple)):
        if depth >= 6:
            return f"<list of {len(value)} items>"
        items = [bounded(child, depth + 1) for child in value[:20]]
        if len(value) > 20:
            items.append(f"<{len(value) - 20} more items>")
        return items
    if isinstance(value, str) and len(value) > 400:
        return f"<text, {len(value)} characters, starts {value[:120]!r}>"
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    return repr(value)


def looks_like_json(text):
    try:
        return isinstance(json.loads(text.strip().lstrip("﻿")), (dict, list))
    except (ValueError, TypeError, AttributeError):
        return False


def compact(value, limit=240):
    text = value if isinstance(value, str) else json.dumps(bounded(value), default=str)
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit] + " ..."


class TracingClient(NmrRpcClient):
    """NmrRpcClient that records, explains and optionally prints each exchange.

    ``_request`` repeats the parent's transport and decoding exactly (a parity
    test guards this), so the experiment worker receives identical values.
    """

    def __init__(self, rpc_config, echo=None, show_replies=False):
        super().__init__(rpc_config)
        self.exchanges = []
        self.echo = echo
        self.show_replies = show_replies
        self.view = "strict"  # "simple": show raw status fields instead of the 06/08 verdict.
        self.quiet_status = False  # True: status polls are recorded but not printed (04/05 print their own).
        self._t0 = time.monotonic()

    def _request(self, method, path, payload=None, params=None):
        url = self._url(path, params)
        record = {"n": len(self.exchanges) + 1, "t_s": round(time.monotonic() - self._t0, 2),
                  "method": method, "path": path, "url": url}
        if payload is not None and payload != {}:
            record["sent"] = bounded(payload)
        self.exchanges.append(record)
        data = None
        headers = {"Accept": "application/json, text/plain, */*"}
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = request.Request(url, data=data, headers=headers, method=method)
        started = time.monotonic()
        try:
            with request.urlopen(req, timeout=self.config.timeout) as response:
                raw = response.read()
                content_type = response.headers.get("Content-Type", "")
                record["http_status"] = getattr(response, "status", None)
        except error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            record.update(http_status=exc.code, error_type="HTTPError", error=f"HTTP {exc.code}",
                          body=compact(detail, 1500))
            self._done(record, started)
            raise NmrRpcError(f"{method} {url} failed: HTTP {exc.code}: {detail}") from exc
        except error.URLError as exc:
            record.update(error_type=type(exc.reason).__name__, error=str(exc.reason))
            self._done(record, started)
            raise NmrRpcError(f"{method} {url} failed: {exc.reason}") from exc
        except Exception as exc:
            record.update(error_type=type(exc).__name__, error=str(exc) or repr(exc))
            self._done(record, started)
            raise
        text = raw.decode("utf-8", errors="replace")
        record.update(content_type=content_type, body_bytes=len(raw))
        value = None
        if text:
            value = text
            if "json" in content_type.lower() or text[:1] in "[{":
                try:
                    value = json.loads(text)
                except json.JSONDecodeError:
                    pass
        record["decoded_as"] = "empty" if not text else "text" if isinstance(value, str) else "json"
        if isinstance(value, str):
            record["body"] = compact(text, 1500)
            if looks_like_json(text):
                record["warning"] = ("valid JSON arrived as plain text (leading whitespace and/or a non-JSON "
                                     "Content-Type); the experiment worker cannot read it as an object")
        else:
            record["reply"] = bounded(value)
        if path == STATUS_PATH:
            record["state"], record["state_reason"] = explain_status(value)
        self._done(record, started)
        return value

    def _done(self, record, started):
        record["seconds"] = round(time.monotonic() - started, 3)
        if self.quiet_status and record["path"] == STATUS_PATH and "error_type" not in record:
            return
        if self.echo:
            self.echo(describe(record, self.show_replies, self.view))


def describe(record, show_reply=False, view="strict"):
    head = f"  [{record['t_s']:6.1f} s] {record['method']:<3} {record['path'].replace('/interfaces/', '')}"
    if "error_type" in record:
        line = f"{head} -> FAILED after {record['seconds']:.1f} s: {record['error_type']}: {record['error']}"
        return line + (f"\n             reply: {compact(record['body'])}" if record.get("body") else "")
    line = f"{head} -> HTTP {record.get('http_status')} in {record['seconds']:.2f} s"
    reply = record.get("reply") if isinstance(record.get("reply"), dict) else {}
    if "state" in record and view == "simple":
        parts = [f"{key}={reply[key]}" for key in ("ResultCode", "NumberOfScansRun") if key in reply]
        receipt = reply.get("OriginalReceipt")
        if isinstance(receipt, dict):
            parts.append("receipt " + "/".join(str(receipt.get(key)) for key in ("ExperimentNumber", "TimeStamp")))
        line += " | " + (", ".join(parts) or f"status: {compact(record.get('reply', record.get('body', '')), 120)}")
    elif "state" in record:
        line += f" | NMR {record['state'].upper()}: {record['state_reason']}"
        if reply.get("NumberOfScansRun") is not None:
            line += f" | scans run: {reply['NumberOfScansRun']}"
    elif record["path"].endswith("/Service/Acquire"):
        show_reply = True
    elif record["method"] == "PUT" and record["path"].endswith("/RunExperiment"):
        line += " | START sent"
        show_reply = True
    if record.get("warning"):
        line += f"\n             WARNING: {record['warning']}"
    if show_reply or ("state" not in record and record["method"] == "GET" and record["decoded_as"] != "json"):
        line += f"\n             reply: {compact(record.get('reply', record.get('body', '')))}"
    return line


def _nested_state_paths(value, prefix=""):
    paths = []
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "OriginalReceipt":
                continue  # The previous run's receipt, not the current state.
            if prefix and key in STATE_FIELDS:
                paths.append(prefix + key)
            paths += _nested_state_paths(child, f"{prefix}{key}.")
    return paths


def explain_status(status):
    """Plain-language reason for the worker's own idle/busy/unknown verdict.

    The verdict always comes from ``running_state``; this only explains it.
    """
    state = running_state(status)
    label = {True: "busy", False: "idle", None: "unknown"}[state]
    if status is None:
        return label, "empty reply, no JSON object"
    if isinstance(status, str):
        if looks_like_json(status):
            return label, ("the reply is JSON but arrived as plain text (leading whitespace or a non-JSON "
                           "Content-Type), so the worker cannot read it")
        return label, f"the reply is plain text, not a JSON object: {compact(status, 120)!r}"
    if not isinstance(status, dict):
        return label, f"the reply is a JSON {type(status).__name__}, not an object"
    flags = {key: status[key] for key in FLAGS if key in status}
    bool_flags = {key: value for key, value in flags.items() if type(value) is bool}
    code = status.get("ResultCode")
    percent = status.get("PercentComplete")
    numeric_percent = isinstance(percent, (int, float)) and not isinstance(percent, bool)
    if state is True:
        true_flags = [key for key, value in bool_flags.items() if value]
        if true_flags:
            return label, f"{true_flags[0]}=true"
        if numeric_percent and 0 <= percent < 100:
            extra = f" (ResultCode={code!r} is not used once PercentComplete says running)" if "ResultCode" in status else ""
            return label, f"PercentComplete={percent:g}: any value from 0 up to 100 counts as running{extra}"
        return label, "ResultCode=1 (1 = running)"
    if state is False:
        parts = [f"{key}=false" for key in bool_flags]
        if type(code) is int and code == 0:
            parts.append("ResultCode=0 (0 = idle)")
        return label, ", ".join(parts)
    reasons = []
    if type(code) is int:
        reasons.append(f"ResultCode={code}: only 0 (idle) and 1 (running) are understood, so any other code "
                       "counts as UNKNOWN")
    elif "ResultCode" in status:
        reasons.append(f"ResultCode={code!r} is a {type(code).__name__}, not a whole number")
    for key, value in flags.items():
        if type(value) is not bool:
            reasons.append(f"{key}={value!r} is a {type(value).__name__}, not true/false")
    if numeric_percent and percent >= 100 and not reasons:
        reasons.append(f"PercentComplete={percent:g} on its own does not prove idle")
    nested = _nested_state_paths(status)
    if nested:
        reasons.append(f"state fields only appear nested ({', '.join(nested[:5])}), not at the top level")
    if not reasons:
        reasons.append("no top-level InProgress/Running/IsRunning flag or ResultCode; keys received: "
                       + ", ".join(sorted(map(str, status))[:20]))
    return label, "; ".join(reasons)


# --- Findings, files and output --------------------------------------------

def finding(level, problem, meaning, fix):
    return {"level": level, "problem": problem, "meaning": meaning, "fix": list(fix)}


def print_findings(findings, out=print):
    for number, item in enumerate(findings, 1):
        out(f"  {number}. [{item['level']}] {item['problem']}")
        out(f"     Why: {item['meaning']}")
        for step in item["fix"]:
            out(f"     - {step}")


def status_findings(record, context):
    """Findings for an ExperimentStatus exchange that did not prove idle."""
    if record is None:
        return [finding("FAIL", "The acquisition status (iFlow/ExperimentStatus) was never read.",
                        "Without a status reply nothing can prove the NMR is idle.", ["See the earlier failures."])]
    if "error_type" in record:
        missing = record.get("http_status") == 404
        return [finding("FAIL", f"Reading iFlow/ExperimentStatus failed: {record['error_type']}: {record['error']}.",
                        "This NMReady version does not offer the iFlow interface the experiment uses." if missing
                        else "The experiment reads this before every START; a failed read means START is skipped.",
                        ["Run the connection test (01) and fix the first failure it lists."])]
    reply = compact(record.get("reply", record.get("body")), 600)
    if record["state"] == "busy":
        return [finding("FAIL", f"The NMR reports that an acquisition is running {context}.",
                        f"NMR said: {record['state_reason']}. Reply: {reply}",
                        ["Look at the NMR touchscreen: wait for any running experiment to finish, or cancel it there.",
                         "Close any other program that controls the NMR (NMReady remote tools, another script).",
                         "Run the connection test (01) again; step 5 must show NMR IDLE."])]
    fix = ["Bring the debug report printed below back to HOME so this exact reply can be reviewed.",
           "Do not change the experiment code to accept this reply without that review."]
    if "ResultCode=" in record["state_reason"] and "only 0 (idle)" in record["state_reason"]:
        fix.insert(0, "Note: the vendor manual's own ExperimentStatus example also shows ResultCode 2 and "
                      "does not say what it means.")
    if record.get("warning"):
        fix.insert(0, "This is a known transport limitation (JSON received as text); report it with the evidence.")
    return [finding("FAIL", f"The NMR's acquisition status is not understood as idle {context}.",
                    f"{record['state_reason']}. This is exactly the condition that makes the experiment print "
                    f"'NMR idle not confirmed; START skipped to avoid overlap'. Reply: {reply}", fix)]


def unrecognised_idle_warning(record):
    """Test 01: a status reply 06/08 cannot read as idle (not positively busy)."""
    reply = compact(record.get("reply", record.get("body")), 600)
    fix = ["Tests 02-05 still run: none of them checks this reply before START.",
           "Bring this debug report back to HOME so the reply can be reviewed before changing 06/08."]
    if "ResultCode=" in record["state_reason"] and "only 0 (idle)" in record["state_reason"]:
        fix.append("Note: the vendor manual's own ExperimentStatus example also shows ResultCode 2 and "
                   "does not say what it means.")
    return finding("WARN", "06/08 do not recognise this status reply as idle.",
                   f"{record['state_reason']}. With this reply 06/08 and 04 --nmr-check print "
                   f"'NMR idle not confirmed; START skipped to avoid overlap'. Reply: {reply}", fix)


def new_output_dir(kind, output_dir=None):
    if output_dir:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=False)
        return path
    root = config.resolve_repo_path(OUTPUT_ROOT)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for suffix in range(100):
        path = root / (f"{stamp}_{kind}" + (f"_{suffix:02d}" if suffix else ""))
        try:
            path.mkdir(parents=True, exist_ok=False)
            return path
        except FileExistsError:
            continue
    raise FileExistsError(f"Could not create a new folder under {root}")


def save_report(output, report, out=print):
    report["finished"] = datetime.now().astimezone().isoformat(timespec="seconds")
    path = output / REPORT_NAME
    try:
        write_json_atomic(path, json.loads(json.dumps(report, default=str)))
    except Exception as exc:
        out(f"WARNING: debug report not written to {path}: {type(exc).__name__}: {exc}")
        return None
    return path


def inspect_fid(path):
    """Header facts from the saved raw file; format identification only."""
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    header = {}
    for line in text.splitlines()[:600]:
        if line.startswith("##") and "=" in line:
            key, _, value = line[2:].partition("=")
            header.setdefault(key.strip().upper(), value.split("$$")[0].strip())
    kind = declared_type(text)
    return {"path": str(path), "bytes": path.stat().st_size, "jcamp": is_jcamp(text),
            "data_type": kind.strip() if kind else None, "is_fid": is_fid_type(kind),
            "title": header.get("TITLE"), "long_date": header.get("LONG DATE"),
            "averages": header.get(".AVERAGES"), "solvent": header.get(".SOLVENT NAME"),
            "software": header.get("JCAMP-DX")}


def base_report(test, settings, retrieval_wait, sources):
    return {"test": test, "started": datetime.now().astimezone().isoformat(timespec="seconds"),
            "python": sys.version.split()[0], "platform": platform.platform(),
            "repository": str(config.REPO_ROOT), "settings": asdict(settings),
            "retrieval_wait_seconds": retrieval_wait, "sources": sources}


def add_common_arguments(parser):
    parser.add_argument("--machine-config", type=Path,
                        help="machine YAML with the NMR address (default: the one named in the preset, "
                             "configs/machines/si6_real_COM4.yaml)")
    parser.add_argument("--experiment-config", type=Path,
                        help=f"preset whose nmr: section is used (default: {DEFAULT_EXPERIMENT.as_posix()})")
    parser.add_argument("--host", help="NMR IP address for this run only (overrides the YAML)")
    parser.add_argument("--port", type=int, help="NMR RPC port for this run only (overrides the YAML)")
    parser.add_argument("--output-dir", type=Path, help="new folder for the debug report (default: results/nmr_standalone/<time>_...)")


def _rel(path):
    try:
        return str(Path(path).resolve().relative_to(config.REPO_ROOT))
    except ValueError:
        return str(path)


def _load(args, out, scans=None):
    try:
        return load_settings(args.experiment_config, args.machine_config, scans=scans, host=args.host, port=args.port)
    except Exception as exc:
        out(f"  FAILED: {type(exc).__name__}: {exc}")
        out("  Fix the file named above, then run the command again. No NMR request was sent.")
        return None


def _output(out, kind, output_dir):
    try:
        return new_output_dir(kind, output_dir)
    except OSError as exc:
        out(f"  FAILED: cannot create a new results folder: {type(exc).__name__}: {exc}")
        out("  Choose a folder that does not exist yet. No NMR request was sent.")
        return None


def _client(settings, out, show_replies):
    return TracingClient(NmrRpcConfig(host=settings.host, port=settings.port, scheme=settings.scheme,
                                      timeout=settings.timeout, poll_seconds=settings.poll_seconds,
                                      max_wait_seconds=settings.max_wait_seconds),
                         echo=out, show_replies=show_replies)


def _read(client, getter):
    try:
        return getter(), None
    except Exception as exc:
        return None, f"{type(exc).__name__}: {exc}"


def _last(client, path):
    return next((record for record in reversed(client.exchanges) if record["path"] == path), None)


# --- Test 01: read-only connection test ------------------------------------

def connection_test(argv=None, out=print):
    parser = argparse.ArgumentParser(description="Read-only NMR connection test: network, RPC service, "
                                     "acquisition status and settings templates. Never changes a setting "
                                     "and never starts an acquisition.")
    add_common_arguments(parser)
    args = parser.parse_args(argv)
    out("NMR TEST 01 - CONNECTION (read-only: no setting is changed, no acquisition is started)")
    out("")
    out("[1/5] Configuration")
    loaded = _load(args, out)
    if loaded is None:
        return 2
    settings, retrieval_wait, sources = loaded
    print_settings(settings, retrieval_wait, sources, out, acquiring=False)
    output = _output(out, "connection", args.output_dir)
    if output is None:
        return 2
    report = base_report("01_connection", settings, retrieval_wait, sources)
    findings = []
    try:
        out("")
        out(f"[2/5] Network path from this laptop to {settings.host}:{settings.port}")
        net = network_check(settings.host, settings.port, settings.timeout, settings.scheme)
        report["network"] = net
        print_network(net, out)
        findings += network_findings(net)
        if net["tcp"] != "open":
            out("  Steps 3-5 skipped: they need this connection.")
        else:
            findings += _connection_http_checks(settings, report, out)
    except KeyboardInterrupt:
        findings.append(finding("FAIL", "Stopped with Ctrl+C.", "The test did not finish.", ["Run it again."]))
    return _finish(output, report, findings, out, read_only=True)


def _connection_http_checks(settings, report, out):
    findings = []
    client = _client(settings, out, show_replies=True)
    report["http_exchanges"] = client.exchanges
    out("")
    out("[3/5] NMReady remote (RPC) service")
    ping, ping_error = _read(client, client.ping)
    enabled, enabled_error = _read(client, client.rpc_enabled)
    _read(client, client.rpc_active)
    all_failed = ping_error and enabled_error
    if all_failed and not any(record.get("http_status") for record in client.exchanges):
        findings.append(finding("FAIL", "The TCP port is open but the RPC service did not answer HTTP requests.",
                                f"Errors: {ping_error}; {enabled_error}",
                                ["Restart the NMReady software on the NMR, then run this test again.",
                                 "If it continues, check nmr.scheme (http) and nmr.port in the machine YAML."]))
        return findings
    if isinstance(ping, dict) and ping.get("connected") is False:
        findings.append(finding("FAIL", "NMReady answered, but says it is not connected to the spectrometer.",
                                "PingSpectrometer returned connected=false: the software runs but cannot talk "
                                "to the magnet/console electronics.",
                                ["Restart NMReady or power-cycle the NMR, wait for start-up to finish, test again."]))
    if isinstance(enabled, dict) and enabled.get("RpcEnabled") is False:
        findings.append(finding("FAIL", "Remote control (RPC API) is disabled on the NMR.",
                                "RpcEnabled=false: status can be read, but every settings change and START is "
                                "refused with HTTP 403 Forbidden.",
                                ["On the NMR touchscreen: Setup -> System -> Remote, press Enable.",
                                 "If there is no Enable option, the vendor manual says config/pygui.cfg must contain "
                                 "RPC_API_ENABLED = True, then restart the NMReady software.",
                                 "Run this test again: RpcEnabled must be true."]))
    elif enabled_error:
        findings.append(finding("WARN", "Could not read RpcEnabled.", enabled_error,
                                ["Not used by the experiment; continue if step 5 passes."]))
    out("")
    out("[4/5] Spectrometer state (information)")
    spectrometer, _ = _read(client, client.spectrometer_status)
    _read(client, lambda: client.get("/interfaces/iStatus/OperationalMessages"))
    if isinstance(spectrometer, dict) and spectrometer.get("StandbyMode") is True:
        findings.append(finding("WARN", "The NMR is in standby mode.",
                                "SpectrometerStatus reports StandbyMode=true; it may not acquire until woken.",
                                ["Wake the NMR on its touchscreen, then run this test again."]))
    out("")
    out("[5/5] Acquisition readiness: status reply (06/08 read it before every START) and settings templates")
    _read(client, client.iflow_experiment_status)
    status_record = _last(client, STATUS_PATH)
    if status_record is not None and status_record.get("state") == "unknown" and "error_type" not in status_record:
        findings.append(unrecognised_idle_warning(status_record))
    elif status_record is None or status_record.get("state") != "idle":
        findings += status_findings(status_record, "right now")
    for label, getter, path in (("iFlow/Settings/1D", client.iflow_1d_settings, "/interfaces/iFlow/Settings/1D"),
                                ("iFlow/ExperimentSettings", client.iflow_experiment_settings,
                                 "/interfaces/iFlow/ExperimentSettings")):
        value, problem = _read(client, getter)
        if problem or not isinstance(value, dict):
            record = _last(client, path) or {}
            missing = record.get("http_status") == 404
            findings.append(finding("FAIL", f"The {label} settings template could not be read.",
                                    ("This NMReady version does not offer the iFlow interface the experiment uses. "
                                     if missing else "") + f"Problem: {problem or 'reply is not a JSON object: ' + compact(value)}",
                                    ["The experiment reads this template before START; it must be a JSON object.",
                                     "Bring the debug report back to HOME for review."]))
    return findings


def _finish(output, report, findings, out, *, read_only=False, start_sent=None):
    failed = any(item["level"] == "FAIL" for item in findings)
    report["verdict"] = "FAIL" if failed else "PASS"
    report["findings"] = findings
    path = save_report(output, report, out)
    out("")
    out("=" * 78)
    verdict = report["verdict"] + (" (with warnings)" if not failed and findings else "")
    if read_only:
        out(f"RESULT: {verdict}  (read-only test: no setting changed, no acquisition started)")
    else:
        sent = ("START was sent once" if start_sent else "START was NOT sent") if start_sent is not None else ""
        out(f"RESULT: {verdict}" + (f"  ({sent})" if sent else ""))
    if findings:
        out("What was found, and what to do:")
        print_findings(findings, out)
    elif read_only:
        out("Everything the experiment needs before START answered correctly.")
    out(f"Debug evidence: {path or output}")
    return 1 if failed else 0


# --- Tests 02/03: one live acquisition --------------------------------------

def demo_nmr_config(settings):
    """The resolved settings in the key names ``acquire_nmr`` (10/11) reads."""
    return {"host": settings.host, "port": settings.port, "scheme": settings.scheme,
            "http_timeout_seconds": settings.timeout, "acquisition_timeout_seconds": settings.max_wait_seconds,
            "poll_seconds": settings.poll_seconds, "scans": settings.scans, "gain": settings.receiver_gain,
            "auto_gain": settings.auto_gain, "center_ppm": settings.spectral_center,
            "width_ppm": settings.sweep_width}


def acquisition_test(argv=None, *, scans, input_fn=input, out=print):
    from arduino.python.run_lock import PortProcessLock

    number = {1: "02", 4: "03"}.get(scans, "0x")
    parser = argparse.ArgumentParser(description=f"One live {scans}-scan NMR acquisition with every NMR request "
                                     "recorded. By default it uses the simplified acquisition of 10_stage2only "
                                     "and 11_3InstrumentCheck (no status check before START). No pump, needle, "
                                     "HOME or dose record is used.")
    add_common_arguments(parser)
    parser.add_argument("--yes", action="store_true", help="skip the single y/N question before START")
    parser.add_argument("--experiment-worker", action="store_true",
                        help="use the 06/08 acquisition worker instead (strict idle check before START), "
                             "to compare with the default method")
    args = parser.parse_args(argv)
    worker = args.experiment_worker
    method = ("the 06/08 experiment worker (strict idle check before START)" if worker
              else "the simplified 10/11 method (no status check before START)")
    out(f"NMR TEST {number} - ONE {scans}-SCAN ACQUISITION using {method}")
    out("")
    out("[1/5] Configuration")
    loaded = _load(args, out, scans=scans)
    if loaded is None:
        return 2
    settings, retrieval_wait, sources = loaded
    print_settings(settings, retrieval_wait, sources, out)
    if not worker:
        try:
            # Imported here so test 01 keeps working if the 10/11 module is absent.
            from .stage2_demo_nmr import acquire_nmr
        except ImportError as exc:
            out(f"  FAILED: the simplified method's code is missing: {type(exc).__name__}: {exc}")
            out("  chemyx_lab/workflows/stage2_demo_nmr.py (shared with 10/11) must be copied with these scripts,")
            out("  or add --experiment-worker. No NMR request was sent.")
            return 2
    output = _output(out, f"{scans}scan", args.output_dir)
    if output is None:
        return 2
    report = base_report(f"{number}_acquisition_{scans}_scan", settings, retrieval_wait, sources)
    report["method"] = "experiment_worker" if worker else "simplified_acquire_nmr"
    client = _client(settings, out, show_replies=False)
    report["http_exchanges"] = client.exchanges
    findings, start_sent = [], False
    try:
        out("")
        out(f"[2/5] Network path from this laptop to {settings.host}:{settings.port}")
        net = network_check(settings.host, settings.port, settings.timeout, settings.scheme)
        report["network"] = net
        print_network(net, out)
        findings += network_findings(net)
        if net["tcp"] != "open":
            return _finish(output, report, findings, out, start_sent=False)
        out("")
        out("[3/5] Read-only pre-check (nothing is changed)")
        enabled, _ = _read(client, client.rpc_enabled)
        if isinstance(enabled, dict) and enabled.get("RpcEnabled") is False:
            findings.append(finding("FAIL", "Remote control (RPC API) is disabled on the NMR.",
                                    "RpcEnabled=false: every settings change and START would be refused (HTTP 403).",
                                    ["On the NMR touchscreen: Setup -> System -> Remote, press Enable.",
                                     "Then run the connection test (01) again."]))
            return _finish(output, report, findings, out, start_sent=False)
        _read(client, client.iflow_experiment_status)
        status_record = _last(client, STATUS_PATH)
        strict_idle = status_record is not None and status_record.get("state") == "idle"
        if worker and not strict_idle:
            findings += status_findings(status_record, "before this test")
            return _finish(output, report, findings, out, start_sent=False)
        if not worker and not strict_idle:
            out("  Note: 06/08 and 04 --nmr-check would refuse START with this status reply.")
            out("  The simplified method does not check it; you confirm on the touchscreen that the NMR is idle.")
        out("")
        if worker:
            out(f"[4/5] Acquire: settings PUT, idle re-check, one START, wait up to {settings.max_wait_seconds:g} s, "
                "save the raw FID")
        else:
            out(f"[4/5] Acquire: settings PUT, one START, wait for this run's receipt, {scans} scan(s) and "
                f"Service/Acquire 'done' (limit {settings.max_wait_seconds:g} s), save the raw FID")
        raw_path = output / "nmr_raw" / f"nmr_{scans}scan_0001.dx"
        if not args.yes:
            out(f"  The NMR will run {scans} scan(s) on whatever sample is in it now.")
            if not worker:
                out("  Check the NMR touchscreen shows it is idle (no experiment running).")
            answer = input_fn(f"  Start the {scans}-scan NMR acquisition now? [y/N]: ").strip().lower()
            if answer not in {"y", "yes"}:
                out("  Declined. No setting was changed and no acquisition was started.")
                report.update(verdict="DECLINED", findings=[])
                save_report(output, report, out)
                return 3
        try:
            lock = PortProcessLock(f"fixed-nmr:{settings.host}:{settings.port}").acquire()
        except Exception as exc:
            findings.append(finding("FAIL", "Another program on this laptop is using the NMR.",
                                    f"The NMR ownership lock is held ({type(exc).__name__}). A running 06/08 "
                                    "experiment, a 04 --nmr-check, or another NMR test window owns it.",
                                    ["Let that program finish (or close it), then run this test again.",
                                     "Never run an NMR test during a live experiment."]))
            return _finish(output, report, findings, out, start_sent=False)
        try:
            if worker:
                result = acquire(settings, raw_path, retrieval_wait, client=client)
            else:
                client.view = "simple"
                result = acquire_nmr(demo_nmr_config(settings), raw_path, state={}, client=client,
                                     emit=lambda message: out(f"  {message}"))
        finally:
            lock.release()
        report["worker_result"] = result
        start_sent = bool(result.get("requested"))
        out("")
        out("[5/5] Check the saved raw file")
        if result.get("status") == "NMR_SAVED":
            try:
                facts = inspect_fid(result["path"])
            except OSError as exc:
                findings.append(finding("WARN", f"The raw file was saved but could not be read back: {exc}.",
                                        f"Saved path: {result['path']}.", ["Check the file exists and is not open elsewhere."]))
            else:
                report["saved_file"] = facts
                out(f"  File      : {facts['path']} ({facts['bytes']} bytes)")
                out(f"  Data type : {facts['data_type']}   NMR time (LONG DATE): {facts['long_date']}")
                out(f"  Averages  : {facts['averages']} (requested scans: {scans})")
                findings += fid_findings(facts, scans)
        else:
            detail = result.get("error") or result.get("reason")
            out(f"  Result: {result.get('status')}" + (f" - {detail}" if detail else ""))
            findings += acquisition_findings(result, client) if worker else simple_findings(result, client)
    except KeyboardInterrupt:
        findings.append(finding("FAIL", "Stopped with Ctrl+C.",
                                "If START had already been sent, the NMR may still be acquiring.",
                                ["Check the NMR touchscreen; let the run finish or cancel it there.",
                                 "Run the connection test (01) before trying again."]))
        start_sent = any(r["path"].endswith("/RunExperiment") for r in client.exchanges)
        _finish(output, report, findings, out, start_sent=start_sent)
        return 130
    return _finish(output, report, findings, out, start_sent=start_sent)


def fid_findings(facts, scans):
    findings = []
    if not facts["is_fid"]:
        findings.append(finding("FAIL", "The saved file is not identified as a raw NMR FID.",
                                f"##DATA TYPE is {facts['data_type']!r}.", ["Bring the debug report back to HOME for review."]))
    try:
        averages = int(float(facts["averages"]))
    except (TypeError, ValueError):
        findings.append(finding("WARN", "The file does not state how many scans were averaged.",
                                "No readable ##.AVERAGES header line.", ["Not needed by the experiment."]))
    else:
        if averages != scans:
            findings.append(finding("WARN", f"The NMR averaged {averages} scan(s), not the requested {scans}.",
                                    "The NumberOfScans setting may not have been applied by the NMR.",
                                    ["Bring the debug report back to HOME for review."]))
    return findings


def _pre_start_failure(client):
    """Finding for the last failed request when START was never sent, or None."""
    failed = next((r for r in reversed(client.exchanges) if "error_type" in r), None)
    if failed is None:
        return None
    name = failed["path"].replace("/interfaces/", "")
    if failed.get("http_status") == 403:
        return [finding("FAIL", f"The NMR refused {failed['method']} {name} with HTTP 403 Forbidden.",
                        "Remote control (RPC API) is not enabled for settings changes.",
                        ["On the NMR touchscreen: Setup -> System -> Remote, press Enable.",
                         "Run the connection test (01) again."])]
    return [finding("FAIL", f"{failed['method']} {name} failed before START: {failed['error_type']}: {failed['error']}.",
                    "START was not sent, so nothing ran on the NMR.",
                    ["Run the connection test (01) and fix the first failure it lists."])]


def _start_record(client):
    starts = [r for r in client.exchanges if r["path"].endswith("/RunExperiment")]
    return starts[-1] if starts else None


def _start_failure(start_record):
    return [finding("FAIL", f"Sending START failed: {start_record['error_type']}: {start_record['error']}.",
                    "The START request may or may not have reached the NMR; it is never sent twice.",
                    ["Look at the NMR touchscreen: if it is acquiring, let it finish or cancel it there.",
                     "Run the connection test (01) before trying again."])]


def acquisition_findings(result, client):
    """Findings for the 06/08 worker (``--experiment-worker``)."""
    error_text = result.get("error") or ""
    sent = bool(result.get("requested"))
    start_record = _start_record(client)
    if "idle not confirmed" in error_text:
        return status_findings(_last(client, STATUS_PATH), "at the worker's first check")
    if "busy/unknown during setup" in error_text:
        found = status_findings(_last(client, STATUS_PATH), "after the settings were sent")
        found[0]["meaning"] += (" The first check was idle; the state changed while the settings were being sent "
                                "(someone used the NMR, or sending settings changed the status).")
        return found
    if not sent and _pre_start_failure(client):
        return _pre_start_failure(client)
    if isinstance(start_record, dict) and "error_type" in start_record:
        return _start_failure(start_record)
    start_reply = compact(start_record.get("reply", start_record.get("body"))) if start_record else "none"
    if error_text.startswith("TimeoutError") and "not confirmed complete" in error_text:
        match = re.search(r"after \S+s \((.*)\); last status", error_text, re.S)
        reason = match.group(1) if match else error_text
        saw_busy = any(r.get("state") == "busy" for r in client.exchanges
                       if start_record and r["n"] > start_record["n"])
        meaning = (f"START was sent, but completion could not be proven within the limit. Worker reason: {reason}. "
                   f"START reply: {start_reply}.")
        fix = ["Look at the NMR touchscreen: did a 1D experiment actually run?"]
        if not saw_busy:
            fix.append("The status never reported 'running' after START. If nothing ran, the NMR may have ignored "
                       "or rejected START; check the START reply above and the NMR screen for an error message.")
        fix.append("Bring the debug report back to HOME: it contains every status reply received.")
        return [finding("FAIL", "The acquisition was not confirmed complete.", meaning, fix)]
    if result.get("status") == "NMR_MISSING":
        return [finding("FAIL", "The acquisition finished, but no raw FID was returned or found.",
                        f"{error_text}. The NMR saved its own copy under the JDX_Filename shown.",
                        ["Copy that file from the NMR manually if it is needed.",
                         "Bring the debug report back to HOME for review."])]
    if "changed during raw retrieval" in error_text:
        return [finding("FAIL", "The NMR started something else while the data was being collected.",
                        error_text, ["Make sure nobody uses the NMR touchscreen during a test, then try again."])]
    return [finding("FAIL", f"The acquisition failed: {error_text or result.get('status')}.",
                    "START was sent once." if sent else "START was not sent.",
                    ["Bring the debug report back to HOME: it lists every NMR request and reply."])]


def simple_findings(result, client):
    """Findings for the simplified 10/11 method (``acquire_nmr``)."""
    reason = result.get("reason") or ""
    start_record = _start_record(client)
    if not result.get("requested"):
        return _pre_start_failure(client) or [finding(
            "FAIL", f"The acquisition stopped before START: {reason}.",
            "START was not sent, so nothing ran on the NMR.",
            ["Run the connection test (01) and fix the first failure it lists."])]
    if isinstance(start_record, dict) and "error_type" in start_record:
        return _start_failure(start_record)
    start_reply = compact(result.get("start_reply"), 300)
    final = compact(result.get("final"), 600)
    if "START server response" in reason:
        return [finding("FAIL", "The NMR answered START with an error.", f"START reply: {start_reply}.",
                        ["Read the message on the NMR touchscreen.", "Bring the debug report back to HOME."])]
    if result.get("status") == "NMR_MISSING":
        return [finding("FAIL", "The acquisition finished, but no raw FID was returned or found.",
                        f"{reason}. Last status: {final}.",
                        ["Look for the file on the NMR itself (its JDX filename appears in the last status).",
                         "Bring the debug report back to HOME for review."])]
    if "changed during raw retrieval" in reason:
        return [finding("FAIL", "The NMR started something else while the data was being collected.",
                        reason, ["Make sure nobody uses the NMR touchscreen during a test, then try again."])]
    if "budget exhausted" in reason:
        # The final reason depends on which request the time limit interrupted, so
        # explain from the recorded replies after START instead.
        after = [r for r in client.exchanges if start_record and r["n"] > start_record["n"] and "error_type" not in r]
        acquire_replies = [r for r in after if r["path"].endswith("/Service/Acquire")]
        if acquire_replies:
            reason = (f"Service/Acquire has not confirmed completed acquisition: "
                      f"{compact(acquire_replies[-1].get('reply'), 300)}")
        elif any(r["path"] == STATUS_PATH for r in after):
            reason = "ExperimentStatus does not yet match this START and requested scan count"
    if "does not yet match this START" in reason:
        return [finding("FAIL", "The NMR never reported this acquisition with the requested scans.",
                        f"START was sent once (reply: {start_reply}). Every later status reply showed a different "
                        f"receipt (ExperimentNumber/TimeStamp) or fewer scans run than requested. Last status: {final}.",
                        ["Look at the NMR touchscreen: did a 1D experiment run, and how many scans?",
                         "If it ran, this NMR reports the run's identity differently than expected; bring the debug "
                         "report back to HOME (it holds every status reply).",
                         "If nothing ran, the NMR ignored START; check the touchscreen for an error message."])]
    if "Service/Acquire has not confirmed" in reason:
        return [finding("FAIL", "The scans were reported, but Service/Acquire never said the acquisition was done.",
                        f"{reason}. This method waits for Service/Acquire to report InProgress=false and "
                        f"LastAcquireSuccessful=true (the reply shape in the vendor manual).",
                        ["Bring the debug report back to HOME: its Service/Acquire replies show what this NMR sends.",
                         "For comparison, run the same test again with --experiment-worker."])]
    if "ExperimentStatus:" in reason:
        return [finding("FAIL", "Reading the NMR status kept failing after START.", reason,
                        ["Look at the NMR touchscreen: if it is acquiring, let it finish.",
                         "Run the connection test (01) to check the network, then try again."])]
    return [finding("FAIL", f"The acquisition failed: {reason or result.get('status')}.", "START was sent once.",
                    ["Bring the debug report back to HOME: it lists every NMR request and reply."])]


# --- Tests 04/05: legacy-style simple completion ----------------------------

LEGACY_PROMPT = "Confirm NMR touchscreen shows IDLE and ready to acquire [y/N]: "


def legacy_acquire(settings, path, client, templates, *, baseline_fid=None, out=print):
    """Legacy-style acquisition: the sequence ``run_nmr_acquisition`` used on 2026-08-10.

    PUT Settings/1D, PUT RunExperiment once, then the driver's own ``wait_for_idle``
    on iFlow/ExperimentStatus (``status_indicates_running``: nonzero ResultCode, a
    true running flag or PercentComplete below 100 = still running). Two small
    additions to the legacy saver: the raw TD FID is taken instead of the first
    text field (which could be the processed FD), and data identical to the FID
    present before START is not saved as new.
    """
    path = Path(path)
    result = {"status": "NMR_FAILED", "path": None, "requested": False, "phase": "configuration",
              "method": "legacy_simple"}
    last = {}

    def read_status():
        value = client.iflow_experiment_status()
        last["status"] = value
        code = value.get("ResultCode") if isinstance(value, dict) else None
        scans_run = value.get("NumberOfScansRun") if isinstance(value, dict) else None
        state = "WAITING" if status_indicates_running(value) else "COMPLETE"
        out(f"  [{client.exchanges[-1]['t_s']:6.1f} s] NMR STATUS: {state} (ResultCode={code!r}, "
            f"NumberOfScansRun={scans_run!r})")
        return value

    try:
        one_d = build_iflow_1d_settings(templates[0], receiver_gain=settings.receiver_gain,
                                        auto_gain=settings.auto_gain, export_filename=str(path))
        run = build_iflow_experiment_settings(templates[1], scans=settings.scans,
                                              receiver_gain=settings.receiver_gain,
                                              spectral_center=settings.spectral_center,
                                              sweep_width=settings.sweep_width, export_filename=str(path))
        client.set_iflow_1d_settings(one_d)
        out("  NMR CONFIGURATION: SENT")
        result.update(phase="start", requested=True)  # A lost reply is an uncertain START; never resent.
        result["start_reply"] = bounded(client.run_iflow_experiment(run))
        out("  NMR START: SENT")
        result["phase"] = "completion"
        try:
            final = client.wait_for_idle(status_getter=read_status, poll_seconds=settings.poll_seconds,
                                         max_wait_seconds=settings.max_wait_seconds)
        except NmrRpcError as exc:
            if not str(exc).startswith("Timed out waiting"):
                raise
            result.update(timed_out=True, final=bounded(last.get("status")),
                          error=f"NMR did not report completion within {settings.max_wait_seconds:g} s")
            out(f"  NMR STATUS: TIMEOUT after {settings.max_wait_seconds:g} s. START is not sent again.")
            out(f"  Last status response: {compact(last.get('status'), 1200)}")
            return result
        result.update(phase="retrieval", final=bounded(final))
        text = raw_text(final)
        if not text:
            out("  RAW FID: NOT RETURNED")
            server_name = final.get("JDX_Filename") if isinstance(final, dict) else None
            result.update(status="NMR_MISSING",
                          error=f"Completion was reported but the status reply has no raw FID; "
                                f"server JDX_Filename={server_name!r}")
            return result
        if baseline_fid and text == baseline_fid:
            out("  RAW FID: SAME AS BEFORE START (not new data)")
            result.update(stale=True, error="The raw FID returned is identical to the one present before START")
            return result
        out("  RAW FID: RECEIVED")
        result["phase"] = "save"
        destination = path if not path.exists() else path.with_suffix(".rpc.dx")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("x", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if not destination.stat().st_size:
            raise OSError(f"Saved file is empty: {destination}")
        out(f"  DX FILE: SAVED {destination}")
        result.update(status="NMR_SAVED", path=str(destination))
        return result
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        if result["phase"] == "completion":
            result["final"] = bounded(last.get("status"))
        return result


def legacy_acquisition_test(argv=None, *, scans, input_fn=input, out=print):
    from arduino.python.run_lock import PortProcessLock

    number = {1: "04", 4: "05"}.get(scans, "0x")
    parser = argparse.ArgumentParser(description=f"One live {scans}-scan NMR acquisition with the simple "
                                     "legacy-style completion (ExperimentStatus until ResultCode 0), as in the "
                                     "older three-instrument test. No pump, needle, HOME or dose record is used.")
    add_common_arguments(parser)
    parser.add_argument("--yes", action="store_true", help="skip the single y/N question before START")
    args = parser.parse_args(argv)
    out(f"NMR TEST {number} - ONE {scans}-SCAN ACQUISITION, SIMPLE LEGACY-STYLE COMPLETION")
    out("")
    out("[1/6] Settings")
    loaded = _load(args, out, scans=scans)
    if loaded is None:
        return 2
    settings, retrieval_wait, sources = loaded
    print_settings(settings, retrieval_wait, sources, out)
    out("  NMR SETTINGS: LOADED")
    output = _output(out, f"{scans}scan_simple", args.output_dir)
    if output is None:
        return 2
    report = base_report(f"{number}_acquisition_{scans}_scan_simple", settings, retrieval_wait, sources)
    report["method"] = "legacy_simple"
    client = _client(settings, out, show_replies=False)
    client.quiet_status = True  # Status polls are summarised as NMR STATUS lines; all are still recorded.
    report["http_exchanges"] = client.exchanges
    findings, start_sent = [], False
    try:
        out("")
        out(f"[2/6] Connection to {settings.host}:{settings.port}")
        net = network_check(settings.host, settings.port, settings.timeout, settings.scheme)
        report["network"] = net
        findings += network_findings(net)
        baseline, problem = (None, None) if net["tcp"] != "open" else _read(client, client.iflow_experiment_status)
        if net["tcp"] != "open" or problem:
            print_network(net, out)
            out("  NMR CONNECTION: FAILED" + (f" ({problem})" if problem else ""))
            if problem:
                findings += _pre_start_failure(client) or []
            return _finish(output, report, findings, out, start_sent=False)
        code = baseline.get("ResultCode") if isinstance(baseline, dict) else None
        out(f"  NMR CONNECTION: OK (status ResultCode={code!r}; information only, START does not depend on it)")
        out("")
        out("[3/6] Settings templates")
        templates = []
        for getter in (client.iflow_1d_settings, client.iflow_experiment_settings):
            value, problem = _read(client, getter)
            if problem or not isinstance(value, dict):
                out("  NMR SETTINGS TEMPLATES: FAILED")
                findings += _pre_start_failure(client) or [finding(
                    "FAIL", "A settings template is not a JSON object.", f"Reply: {compact(value)}",
                    ["Bring the debug report back to HOME."])]
                return _finish(output, report, findings, out, start_sent=False)
            templates.append(value)
        out("  NMR SETTINGS TEMPLATES: READ")
        out("")
        out("[4/6] Operator confirmation")
        out(f"  The NMR will run {scans} scan(s) on whatever sample is in it now.")
        if not args.yes and input_fn(f"  {LEGACY_PROMPT}").strip().lower() not in {"y", "yes"}:
            out("  OPERATOR CONFIRMATION: NO. No setting was changed and no acquisition was started.")
            report.update(verdict="DECLINED", findings=[])
            save_report(output, report, out)
            return 3
        out("  OPERATOR CONFIRMATION: YES")
        try:
            lock = PortProcessLock(f"fixed-nmr:{settings.host}:{settings.port}").acquire()
        except Exception as exc:
            findings.append(finding("FAIL", "Another program on this laptop is using the NMR.",
                                    f"The NMR ownership lock is held ({type(exc).__name__}). A running 06/08 "
                                    "experiment, a 04 --nmr-check, or another NMR test window owns it.",
                                    ["Let that program finish (or close it), then run this test again."]))
            return _finish(output, report, findings, out, start_sent=False)
        out("")
        out(f"[5/6] Configure, START once, wait for completion (status every {settings.poll_seconds:g} s, "
            f"limit {settings.max_wait_seconds:g} s)")
        try:
            result = legacy_acquire(settings, output / "nmr_raw" / f"nmr_{scans}scan_simple.dx", client,
                                    templates, baseline_fid=raw_text(baseline), out=out)
        finally:
            lock.release()
        report["worker_result"] = result
        start_sent = bool(result.get("requested"))
        out("")
        out("[6/6] Check the saved raw file")
        if result["status"] == "NMR_SAVED":
            facts = inspect_fid(result["path"])
            report["saved_file"] = facts
            out(f"  File      : {facts['path']} ({facts['bytes']} bytes)")
            out(f"  Data type : {facts['data_type']}   NMR time (LONG DATE): {facts['long_date']}")
            out(f"  Averages  : {facts['averages']} (requested scans: {scans})")
            findings += fid_findings(facts, scans)
        else:
            out(f"  Result: {result['status']} - {result.get('error')}")
            findings += legacy_findings(result, client)
    except KeyboardInterrupt:
        findings.append(finding("FAIL", "Stopped with Ctrl+C.",
                                "If START had already been sent, the NMR may still be acquiring.",
                                ["Check the NMR touchscreen; let the run finish or cancel it there.",
                                 "Do not send another START until the touchscreen shows the NMR idle."]))
        start_sent = any(r["path"].endswith("/RunExperiment") for r in client.exchanges)
        _finish(output, report, findings, out, start_sent=start_sent)
        return 130
    return _finish(output, report, findings, out, start_sent=start_sent)


NO_RESTART = ("Do not run the test again yet: an uncertain completion is not permission to send another START. "
              "Check the NMR touchscreen first.")


def legacy_findings(result, client):
    """Findings for the simple legacy-style method (04/05)."""
    phase, error_text = result.get("phase"), result.get("error") or ""
    start_record = _start_record(client)
    final = compact(result.get("final"), 600)
    if not result.get("requested"):
        return _pre_start_failure(client) or [finding(
            "FAIL", f"Sending the settings failed: {error_text}.", "START was not sent, so nothing ran on the NMR.",
            ["Run the connection test (01) and fix the first failure it lists."])]
    if isinstance(start_record, dict) and "error_type" in start_record:
        return _start_failure(start_record)
    if result.get("timed_out"):
        return [finding("FAIL", f"The NMR did not report completion within the limit ({error_text}).",
                        "START was sent once. This method waits while ExperimentStatus reports running: any "
                        f"nonzero ResultCode, a true running flag, or PercentComplete below 100. Last status: {final}.",
                        [NO_RESTART,
                         "If the touchscreen is still acquiring, let it finish.",
                         "If the touchscreen shows the run finished but the status kept a nonzero ResultCode, that "
                         "is the code this NMR reports after a run: bring the debug report back to HOME.",
                         "When the NMR is idle, test 02 (the other completion method) can be compared."])]
    if phase == "completion":
        return [finding("FAIL", f"Reading ExperimentStatus failed while waiting: {error_text}.",
                        "START was already sent once; the legacy method stops at the first failed status read.",
                        [NO_RESTART, "Run the connection test (01) once the NMR is idle."])]
    if result.get("stale"):
        return [finding("FAIL", "The NMR returned the same raw FID as before START.",
                        "Completion was read before this acquisition produced data (the NMR had not started or "
                        f"finished it). The old data was not saved as new. Last status: {final}.",
                        [NO_RESTART, "Bring the debug report back to HOME."])]
    if result["status"] == "NMR_MISSING":
        return [finding("FAIL", "The NMR reported completion, but its status reply contained no raw FID.",
                        f"{error_text}. If this happened right after START, the NMR may not have started yet: the "
                        "legacy rule reads 'not running' as finished.",
                        [NO_RESTART, "Look for the file on the NMR itself (JDX_Filename above).",
                         "Bring the debug report back to HOME."])]
    if phase == "save":
        return [finding("FAIL", f"The raw FID was received but could not be saved: {error_text}.",
                        "The acquisition itself finished.", ["Check disk space and folder permissions."])]
    return [finding("FAIL", f"The acquisition failed: {error_text or result['status']}.", "START was sent once.",
                    [NO_RESTART, "Bring the debug report back to HOME: it lists every NMR request and reply."])]
