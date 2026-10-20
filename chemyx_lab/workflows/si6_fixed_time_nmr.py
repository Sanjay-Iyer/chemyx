"""Acquisition/retrieval only. A killable child bounds all optional NMR I/O.

The controller never retries a START. Each observation first proves idle;
unknown/busy status skips START, including after a timeout in an earlier child.
No numerical spectral processing, filename timing, or acquisition-time gate is used.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys
import time

from ..config import NmrSettings, REPO_ROOT
from ..instruments.nmr import (NmrRpcClient, NmrRpcConfig, build_iflow_1d_settings,
                               build_iflow_experiment_settings)


def running_state(status):
    """Positive idle evidence only; empty/unknown/error replies are not idle."""
    if not isinstance(status, dict):
        return None
    flags = [status[key] for key in ("InProgress", "Running", "running", "IsRunning")
             if type(status.get(key)) is bool]
    # A contradictory idle flag cannot overrule any positive busy evidence.
    if any(flags):
        return True
    percent = status.get("PercentComplete")
    if isinstance(percent, (int, float)) and not isinstance(percent, bool) and 0 <= percent < 100:
        return True
    code = status.get("ResultCode")
    if type(code) is int and code == 1:
        return True
    if type(code) is int and code != 0:
        return None  # Unknown/error result codes never authorize START.
    if flags or type(code) is int and code == 0:
        return False
    return None


def receipt_identity(receipt):
    """Protocol identity only; no spectral or acquisition-metadata analysis."""
    if not isinstance(receipt, dict):
        return None
    values = tuple(receipt.get(key) for key in ("ExperimentNumber", "TimeStamp"))
    return values if any(value is not None for value in values) else None


def receipts_conflict(expected, actual):
    """Only identity fields present in both replies can disagree.

    The START reply shape has never been captured from the real instrument; a
    field it omits is not evidence that the completion belongs to another run.
    """
    if expected is None or actual is None:
        return False
    return any(e is not None and a is not None and e != a for e, a in zip(expected, actual))


def busy_evidence(status, state):
    """Positive busy, or any nonzero ResultCode (the hardware-proven legacy parser's
    running rule; the vendor's ExperimentStatus example reports 2 at 0 scans)."""
    return state is True or (isinstance(status, dict) and type(status.get("ResultCode")) is int
                             and status["ResultCode"] != 0)


def status_evidence(status):
    """Keep protocol evidence without duplicating raw/processed spectra."""
    if not isinstance(status, dict):
        return status
    fields = {"InProgress", "Running", "running", "IsRunning", "PercentComplete",
              "ResultCode", "OriginalReceipt", "NumberOfScansRun", "JDX_Filename"}
    return {key: value for key, value in status.items() if key in fields}


def is_jcamp(text):
    return isinstance(text, str) and text.lstrip().upper().startswith("##") and "##END" in text.upper()


def declared_type(text):
    """The JCAMP ##DATA TYPE value, or None; format identification only."""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.upper().replace(" ", "") == "##DATATYPE":
            return value.upper()
    return None


def is_fid_type(kind):
    return kind is not None and ("FID" in kind or "FREE INDUCTION DECAY" in kind)


def raw_text(payload):
    """Raw TD JCAMP from a reply. iFlow can return FD and TD together.

    The TD field is the requested raw FID unless its own header declares
    something else. Any other text field (including an FD-named field, which
    the legacy saver read first) counts only when its header declares an FID.
    """
    if isinstance(payload, str):
        return payload if is_jcamp(payload) else None
    if not isinstance(payload, dict):
        return None
    td = payload.get("JDX_FileContents_TD")
    if is_jcamp(td) and (declared_type(td) is None or is_fid_type(declared_type(td))):
        return td
    for key in ("Jcamp", "JCAMP", "DataString", "Value", "Data", "JDX_FileContents_FD", "JDX_FileContents"):
        value = payload.get(key)
        value = raw_text(value) if isinstance(value, dict) else value
        if is_jcamp(value) and is_fid_type(declared_type(value)):
            return value
    return None


def local_fid(text):
    """A server export can be FD: identify FID format without processing it."""
    return is_jcamp(text) and is_fid_type(declared_type(text))


def confirm_idle(client, result, label, poll_seconds, sleep, refusal):
    """Positive idle or no START. Only a failed or unrecognized reply is re-read
    (at most twice, bounded); a positively busy reply is never waited on."""
    read_error = None
    for attempt in range(3):
        try:
            status, read_error = client.iflow_experiment_status(), None
        except Exception as exc:
            status, read_error = None, f"{type(exc).__name__}: {exc}"
        result[label] = status_evidence(status)
        if running_state(status) is not None or attempt == 2:
            break
        sleep(poll_seconds)
    if running_state(status) is not False:
        raise RuntimeError(f"{refusal}: {status_evidence(status)!r}"
                           + (f"; status read failed: {read_error}" if read_error else ""))
    return status


def acquire(settings, path, retrieval_wait, *, client=None, now=time.monotonic, sleep=time.sleep):
    """One START maximum; bounded polling and bounded late export retrieval."""
    result = {"status": "NMR_FAILED", "path": None, "requested": False}
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            raise FileExistsError(f"Observation export already exists; preserve it and skip START: {path}")
        client = client or NmrRpcClient(NmrRpcConfig(host=settings.host, port=settings.port,
            scheme=settings.scheme, timeout=settings.timeout, poll_seconds=settings.poll_seconds,
            max_wait_seconds=settings.max_wait_seconds))
        # Positive idle is still required before START; busy is never waited on.
        before = confirm_idle(client, result, "before", settings.poll_seconds, sleep,
                              "NMR idle not confirmed; START skipped to avoid overlap")
        one_d = build_iflow_1d_settings(client.iflow_1d_settings(), receiver_gain=settings.receiver_gain,
            auto_gain=settings.auto_gain, export_filename=str(path))
        run = build_iflow_experiment_settings(client.iflow_experiment_settings(), scans=settings.scans,
            receiver_gain=settings.receiver_gain, spectral_center=settings.spectral_center,
            sweep_width=settings.sweep_width, export_filename=str(path))
        client.set_iflow_1d_settings(one_d)
        # Recheck immediately before dispatch, after template/configuration I/O.
        confirm_idle(client, result, "before_start", settings.poll_seconds, sleep,
                     "NMR became busy/unknown during setup; START skipped")
        result["requested"] = True  # A lost reply is an uncertain START, never retried.
        start_reply = client.run_iflow_experiment(run)
        expected_receipt = receipt_identity(start_reply)
        before_receipt = receipt_identity(before.get("OriginalReceipt"))
        deadline = now() + settings.max_wait_seconds
        saw_busy = False
        reason, read_error = "no status read after START", None
        while True:
            # Completion needs positive evidence; anything short of it (a lagging,
            # unrecognized, empty or failed status read) is polled again until
            # the bounded deadline. The legacy saver proven on 2026-08-10 also
            # kept polling through every nonzero ResultCode.
            try:
                status, read_error = client.iflow_experiment_status(), None
            except Exception as exc:
                status, read_error = None, f"{type(exc).__name__}: {exc}"
            state = running_state(status)
            if status is not None:
                result["final"] = status_evidence(status)
            if read_error is not None:
                pass  # A failed read is no evidence either way.
            elif busy_evidence(status, state):
                saw_busy, reason = True, "acquisition running"
            elif state is False:
                # Never save an old receipt merely because START returned.
                completed_receipt = receipt_identity(status.get("OriginalReceipt"))
                if receipts_conflict(expected_receipt, completed_receipt):
                    reason = "completion receipt does not match this START"
                elif completed_receipt is not None and completed_receipt == before_receipt:
                    reason = "status still carries the previous acquisition receipt"
                elif not saw_busy and completed_receipt is None:
                    reason = "idle reply has no fresh receipt or observed running-to-idle transition"
                else:
                    break
            else:
                reason = f"completion state unknown: {status_evidence(status)!r}"
            remaining = deadline - now()
            if remaining <= 0:
                detail = reason + (f"; last status read failed: {read_error}" if read_error else "")
                raise TimeoutError(f"NMR acquisition not confirmed complete after {settings.max_wait_seconds:g}s "
                                   f"({detail}); last status {result.get('final')!r}")
            sleep(min(settings.poll_seconds, remaining))
        # No spectral validity/peak/timestamp requirements: preserve raw data.
        until = now() + retrieval_wait
        read_error = None
        while True:
            eligible = False
            if isinstance(status, dict):
                current = receipt_identity(status.get("OriginalReceipt"))
                if running_state(status) is True or (current is not None and current != completed_receipt):
                    raise RuntimeError("NMR state/receipt changed during raw retrieval; unrelated data was not saved")
                # A reply that cannot prove the same completed run is skipped.
                eligible = running_state(status) is False and current == completed_receipt
            # The instrument may have exported this unique file directly while
            # also returning inline TD. Preserve and accept that same export.
            if path.is_file() and path.stat().st_size:
                if local_fid(path.read_text(encoding="utf-8", errors="replace")):
                    result.update(status="NMR_SAVED", path=str(path))
                    return result
                result["local_export_issue"] = "Existing local export is not identifiable as raw NMR FID; preserved without claiming SAVED"
            text = raw_text(status) if eligible else None
            if text:
                # Never overwrite an incomplete/foreign export. Save valid
                # returned TD beside it instead. newline="" keeps the
                # instrument's own line endings (no doubled CR on Windows).
                destination = path if not path.exists() else path.with_suffix(".rpc.dx")
                with destination.open("x", encoding="utf-8", newline="") as handle:
                    handle.write(text)
                result.update(status="NMR_SAVED", path=str(destination))
                return result
            # ExportFilename is interpreted on the NMR server, not necessarily
            # this PC. Accept only this observation's unique local file.
            remaining = until - now()
            if remaining <= 0:
                if read_error is not None:  # Retrieval itself failed, not merely empty.
                    raise RuntimeError(f"Raw TD retrieval failed after acquisition completed: {read_error}")
                server_name = status.get("JDX_Filename") if isinstance(status, dict) else None
                result.update(status="NMR_MISSING", error=f"Raw TD JCAMP absent in iFlow response and local export {path}; server JDX_Filename={server_name!r}")
                return result
            sleep(min(settings.poll_seconds, remaining))
            try:  # Retrieval only; never another START.
                status, read_error = client.iflow_experiment_status(), None
            except Exception as exc:
                status, read_error = None, f"{type(exc).__name__}: {exc}"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        return result


def acquire_bounded(settings, path, retrieval_wait):
    """Child timeout covers network reads, slow/trickling replies and filesystem I/O."""
    path = Path(path)
    request_path = path.with_suffix(".request.json")
    reply_path = path.with_suffix(".result.json")
    try:
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps({"settings": asdict(settings), "path": str(path),
                                            "retrieval_wait": retrieval_wait}, default=str), encoding="utf-8")
        # Sequential child only; subprocess.run kills and reaps it on timeout.
        completed = subprocess.run([sys.executable, "-m", __name__, str(request_path), str(reply_path)],
            cwd=REPO_ROOT, timeout=settings.max_wait_seconds + retrieval_wait + settings.timeout * 10 + 10,
            capture_output=True, text=True)
        if completed.returncode:
            raise RuntimeError(f"NMR worker exit {completed.returncode}: {completed.stderr[-2000:]}")
        result = json.loads(reply_path.read_text(encoding="utf-8"))
        if not isinstance(result, dict) or result.get("status") not in {"NMR_SAVED", "NMR_MISSING", "NMR_FAILED"}:
            raise ValueError(f"Invalid NMR worker result: {result!r}")
        if result["status"] == "NMR_SAVED":
            saved = Path(result.get("path") or "").resolve()
            if saved not in {path.resolve(), path.with_suffix(".rpc.dx").resolve()} or not saved.is_file() or not saved.stat().st_size:
                raise ValueError("NMR worker claimed SAVED without this observation's nonempty raw file")
        return result
    except Exception as exc:
        return {"status": "NMR_FAILED", "path": None, "error": f"NMR worker: {type(exc).__name__}: {exc}"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("reply", type=Path)
    args = parser.parse_args()
    values = json.loads(args.request.read_text(encoding="utf-8"))
    values["settings"]["save_dir"] = Path(values["settings"]["save_dir"])
    result = acquire(NmrSettings(**values["settings"]), values["path"], values["retrieval_wait"])
    args.reply.write_text(json.dumps(result, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
