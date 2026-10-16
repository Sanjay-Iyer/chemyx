"""Sequential bench checks using existing drivers and the shared HOME ledger.

No experiment scheduler, chemical completion decisions, or dose reservations.
Position restoration means confirmed software tracking, not physical metrology.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import sys
import time
from contextlib import redirect_stdout
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from arduino.python.config import load_arduino_config
from arduino.python.needle_state import NeedleState, NeedleStateStore, TrackedNeedle
from arduino.python.protocol import bool_field
from arduino.scripts._common import controller_session
from chemyx_lab import config
from chemyx_lab.analysis.nmr import read_jcamp_fid
from chemyx_lab.instruments.nmr import NmrRpcClient, NmrRpcConfig
from chemyx_lab.runtime_journal import OperationJournal, RunRecorder, discover_git_commit
from . import chemyx_manual as manual, chemyx_position as position
from . import si6_automated_nmr as base
from .instrument_operations import make_dx_path

DEFAULT_CONFIG = config.REPO_ROOT / "configs/checkouts/si6_simple_instrument_check.yaml"
MOCK_FID = config.REPO_ROOT / "chemyx_lab/testing/fixtures/tracked_resonance_phsi4_20260810.dx"


def load_settings(path=DEFAULT_CONFIG, machine_config=None):
    """Resolve existing rig configuration; validate quantities without any I/O to devices."""
    raw = config.read_mapping_config(config.resolve_repo_path(path), "simple checkout")
    unknown = set(raw) - {"arduino_config", "manual_config", "machine_config", "chemyx", "nmr"}
    if unknown:
        raise ValueError(f"Unknown checkout fields: {sorted(unknown)}")
    machine_path = config.resolve_repo_path(machine_config or raw["machine_config"])
    if not machine_path.is_file():
        raise ValueError(f"Machine config not found: {machine_path}")
    machine = config.load_machine_config(machine_path)
    settings = manual.load_manual_config(config.resolve_repo_path(raw["manual_config"]))
    # Same endpoint precedence as the production/manual tools; do not change HOME identity.
    pump_values = dict(port=machine.chemyx.serial_port, baud_rate=machine.chemyx.baud_rate,
                       timeout=machine.chemyx.timeout_seconds,
                       response_delay=machine.chemyx.response_delay_seconds)
    pump_values.update(config._explicit_env_pump_overrides())
    connection = config.load_pump_config(load_local=False, **pump_values)
    channels = {}
    if set(raw["chemyx"]) != {"channel_1", "channel_2"}:
        raise ValueError("Both Chemyx channels are required")
    for ch in (1, 2):
        move = raw["chemyx"][f"channel_{ch}"]
        if set(move) != {"withdraw_ml", "infuse_ml", "rate_ml_min"}:
            raise ValueError(f"Channel {ch}: use withdraw_ml, infuse_ml, rate_ml_min")
        quantities = [float(move[k]) for k in ("withdraw_ml", "infuse_ml", "rate_ml_min")]
        if any(isinstance(v, bool) for v in move.values()) or not all(math.isfinite(v) and v > 0 for v in quantities):
            raise ValueError(f"Channel {ch}: quantities must be finite and positive")
        if position.to_nl(quantities[0]) != position.to_nl(quantities[1]):
            raise ValueError(f"Channel {ch}: equal withdrawal and infusion are required to restore position")
        if position.to_nl(quantities[0]) <= 0:
            raise ValueError(f"Channel {ch}: volume rounds to zero in the existing ledger")
        current = settings.channels[ch]
        if current.diameter_mm is None or current.capacity_ml is None or current.syringe_volume_at_home_ml is None:
            raise ValueError(f"Channel {ch}: existing syringe geometry and volume at HOME are required")
        if (current.recipe_diameter_mm is not None and current.diameter_mm != current.recipe_diameter_mm
                or current.recipe_capacity_ml is not None and current.capacity_ml != current.recipe_capacity_ml):
            raise ValueError(f"Channel {ch}: manual geometry must match its syringe recipe")
        channels[ch] = replace(current, transfer_volume_ml=quantities[0], rate_ml_min=quantities[2])
        candidate = replace(settings, port=connection.port, baud_rate=connection.baud_rate,
                            timeout=connection.timeout, response_delay=connection.response_delay)
        # Driver validation of diameter, units and rate; no transport is opened.
        cfg = candidate.pump_config(channels[ch], quantities[0])
        if not config.DIAMETER_MIN <= cfg.diameter <= config.DIAMETER_MAX:
            raise ValueError(f"Channel {ch}: diameter outside Chemyx limits")
        if not config.rate_limits(0)[0] <= cfg.rate <= config.rate_limits(0)[1]:
            raise ValueError(f"Channel {ch}: rate outside Chemyx mL/min limits")
    settings = replace(candidate, machine_config=machine_path, channels=channels)
    arduino = load_arduino_config(config.resolve_repo_path(raw["arduino_config"]))
    if (arduino["needle"]["home_position"], arduino["needle"]["down_position"], arduino["needle"]["up_position"]) != (0, 0, 1):
        raise ValueError("Checkout requires Arduino HOME=DOWN=0, UP=1")
    if arduino["needle"]["steps_per_unit"] is None or arduino["needle"]["up_step_sign"] is None:
        raise ValueError("Existing Arduino motion calibration is required")
    nmr_section = raw["nmr"]
    if set(nmr_section) != {"settings_config", "scans", "process_spectrum"}:
        raise ValueError("NMR checkout uses settings_config, scans, process_spectrum")
    if type(nmr_section["scans"]) is not int or nmr_section["scans"] != 2:
        raise ValueError("This bench checkout requires exactly 2 NMR scans")
    if type(nmr_section["process_spectrum"]) is not bool:
        raise ValueError("nmr.process_spectrum must be true or false")
    recipe = config.read_mapping_config(config.resolve_repo_path(nmr_section["settings_config"]), "NMR settings source")
    _, nmr = base.build_instrument_settings(recipe, machine_path)
    nmr = replace(nmr, route="iflow", result_type="fid", scans=2)
    if not settings.port or not nmr.host or not arduino["arduino"]["port"]:
        raise ValueError("All three existing instrument endpoints must be configured")
    return raw, arduino, settings, nmr


def dose_snapshot(root):
    """Read-only byte identity for every production-dose record, including history."""
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file()}


def check_doses(settings):
    refusal = manual.channel2_refusal("infuse", manual.dose_records(settings.dose_ledger_dir), True)
    if refusal:
        raise manual.ManualControlError(refusal)


def verify_needle(needle, target):
    status = needle.status()
    if (bool_field(status, "moving") or status.get("fault") != "NONE"
            or not bool_field(status, "position_valid") or status.get("logical_position") != str(target)
            or needle.store.load() != needle.state):
        raise RuntimeError(f"Arduino completion/idle/persistence verification failed: {status}")
    return status


def acquire_nmr(nmr, raw_dir, *, mock):
    if mock:
        # Existing real-FID fixture: simulated acquisition, never a live RPC.
        path = make_dx_path(raw_dir, "instrument_check_mock", nmr)
        shutil.copyfile(MOCK_FID, path)
        return path
    return base.run_nmr_acquisition(nmr, raw_dir, label="instrument_check")


def process_nmr(dx, output, dataset):
    paths = SimpleNamespace(run_dir=output, processed_dir=output / "nmr_processed",
                            metadata_dir=output, layout=None)
    # Existing full-spectrum FFT/ACME path, with no target-window endpoint gate.
    def logged_runner(command, **kwargs):
        print("Processing command: " + subprocess.list2cmdline(command))
        completed = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, **kwargs)
        print(completed.stdout)
        return completed
    return base.run_process_fid_postprocessing(dx, paths, dataset, phase_method="acme", runner=logged_runner)


class _Tee:
    """Keep the terminal and one checkout transcript in sync."""
    def __init__(self, terminal, log):
        self.terminal, self.log = terminal, log

    def write(self, text):
        self.terminal.write(text)
        self.log.write(text)
        self.log.flush()
        return len(text)

    def flush(self):
        self.terminal.flush()
        self.log.flush()


def run_checkout(raw, arduino, settings, nmr, *, mock, output, input_fn=input):
    output.mkdir(parents=True, exist_ok=False)
    for name in ("nmr_raw", "nmr_processed"):
        (output / name).mkdir()
    checks = ["Arduino UP", "Arduino DOWN", "Arduino HOME", "Arduino restore",
              *[f"Chemyx Channel {ch} {action}" for ch in (1, 2) for action in ("withdraw", "infuse", "restored", "stopped")],
              "NMR connection", "NMR two-scan acquisition", "NMR raw saved", "NMR processing",
              "HOME references preserved", "Production dose history unchanged"]
    report = {"mode": "MOCK" if mock else "LIVE", "overall": "INCOMPLETE", "checks": dict.fromkeys(checks, "NOT RUN"),
              "positions": {}, "attention": [], "result_directory": str(output),
              "chemical_analysis": {"required": False, "status": "NOT EVALUATED",
                                    "reason": "No liquid enters the NMR; no peak, solvent, integration or reaction criteria"},
              "position_evidence": "Confirmed software tracking; no independent physical position or volume measurement",
              "nmr_settings": {k: getattr(nmr, k) for k in ("route", "result_type", "scans", "receiver_gain", "auto_gain", "spectral_center", "sweep_width")}}
    recorder = RunRecorder(OperationJournal(output / "operation_journal.jsonl", output.name,
                                            software_version=discover_git_commit(config.REPO_ROOT)), output / "run_state.json")
    begun = time.monotonic()
    with (output / "instrument_commands.log").open("w", encoding="utf-8") as log, redirect_stdout(_Tee(sys.stdout, log)):
        def passed(name, **evidence):
            # The existing movement driver has already journaled pump dispatch and STOP.
            recorder.record("instrument_checkout_check", check=name, result="PASS", **evidence)
            report["checks"][name] = "PASS"
            print(f"{name}: PASS")

        active = "Startup"
        try:
            print("THREE-INSTRUMENT CHECKOUT" + (" [MOCK — no hardware]" if mock else ""))
            print(f"Arduino: {arduino['arduino']['port']}; Chemyx: {settings.port}")
            for ch, values in settings.channels.items():
                print(f"Channel {ch}: withdraw {values.transfer_volume_ml:g} mL, infuse {values.transfer_volume_ml:g} mL at {values.rate_ml_min:g} mL/min; syringe {values.diameter_mm:g} mm / {values.capacity_ml:g} mL")
            print(f"NMR: {nmr.host}:{nmr.port}; iflow FID; 2 scans; FFT/ACME processing {raw['nmr']['process_spectrum']}")
            print("Restore original known starting positions. Pump test lines isolated from every reaction; Channel 2 infusion routed to waste.")
            print("No liquid is routed into the NMR. Leave its flow cell as-is; blank/background spectra are acceptable.")
            if not mock and input_fn("Confirm isolated test-fluid paths and instruments ready [y/N]: ").strip().lower() not in ("y", "yes"):
                report["attention"].append("Startup declined; no instrument contacted and no position reset")
                return report
            check_doses(settings)
            doses_before = dose_snapshot(settings.dose_ledger_dir)
            # Mock records are local to this unique output, never WORK/HOME physical state.
            root = output / "mock_state/chemyx_state" if mock else position.state_root(False)
            needle_path = output / "mock_state/needle_state.json" if mock else Path(arduino["needle"]["state_path"])
            if mock:
                NeedleStateStore(needle_path).save(NeedleState(logical_position=1, position_valid=True,
                    reason="motion_completed", last_updated=datetime.now(timezone.utc).isoformat(),
                    steps_per_unit=arduino["needle"]["steps_per_unit"], up_step_sign=arduino["needle"]["up_step_sign"]))
            # Acquire the same shared ledger lock used by manual tools and production.
            with position.PositionLedger(root, owner=f"simple checkout {output.name}").acquire() as ledger:
                if mock:
                    for ch in (1, 2):
                        ledger.set_home(settings.channels[ch].identity(settings.pump_identity(True)),
                                        receipt_id=f"mock_fixture_{ch}", established_by="mock fixture only")
                active = "Starting position validation"
                starting = {ch: ledger.state(ch) for ch in (1, 2)}
                for ch in (1, 2):
                    # Validate both complete pairs before moving any instrument.
                    ident = settings.channels[ch].identity(settings.pump_identity(mock))
                    plan = position.plan_transfer(starting[ch], ident, "withdraw", settings.channels[ch].transfer_volume_ml)
                    projected = dict(starting[ch], current_displacement_nl=plan.displacement_after_nl)
                    position.plan_transfer(projected, ident, "infuse", settings.channels[ch].transfer_volume_ml)
                cfg = dict(arduino, firmware=dict(arduino["firmware"], motion_enabled=True))
                saved = NeedleStateStore(needle_path).load()
                # TrackedNeedle enforces calibration and pending-recovery markers too.
                if not saved.position_valid or saved.logical_position is None:
                    raise RuntimeError("Arduino starting position UNKNOWN: physically verify HOME, run needle_control.py home, then retry")
                active = "Arduino"
                with controller_session(cfg, "mock" if mock else "live", allow_motion=True, apply_runtime_config=True) as controller:
                    needle = TrackedNeedle(controller, cfg, state_path=needle_path)
                    start = needle.state.logical_position
                    verify_needle(needle, start)
                    report["positions"]["arduino_start"] = start
                    try:
                        for name, target in (("UP", 1), ("DOWN", 0), ("HOME", 0), ("restore", start)):
                            print(f"Arduino {name}: command absolute logical position {target}")
                            recorder.record("instrument_checkout_needle_request", action=name, target=target,
                                            starting_position=needle.state.logical_position)
                            if name == "HOME":
                                needle.home()
                            else:
                                needle.move_to(target)
                            passed(f"Arduino {name}", status=verify_needle(needle, target))
                        report["positions"]["arduino_end"] = needle.state.logical_position
                    except BaseException:
                        if not needle.stop_best_effort():
                            report["attention"].append("Arduino STOP unconfirmed")
                        raise
                active = "Chemyx communication"
                with manual.pump_session(settings, mock) as pump:
                    for ch in (1, 2):
                        active = f"Chemyx Channel {ch}"
                        if ch == 2:
                            check_doses(settings)
                        manual.confirm_idle(pump, ch, recorder, phase="instrument_checkout")
                        values = settings.channels[ch]
                        ident = values.identity(settings.pump_identity(mock))
                        home_fill = position.to_nl(values.syringe_volume_at_home_ml)
                        report["positions"][f"channel_{ch}"] = {"start_displacement_ml": starting[ch]["current_displacement_ml"],
                            "retained_volume_ml": [position.to_ml(home_fill + starting[ch]["current_displacement_nl"])]}
                        for direction in ("withdraw", "infuse"):
                            if ch == 2:
                                check_doses(settings)
                            plan = position.plan_transfer(ledger.state(ch), ident, direction, values.transfer_volume_ml)
                            after = manual.execute_move(pump, ledger, settings, values, plan, recorder,
                                                        mock=mock, command=f"instrument_check_{direction}")
                            report["positions"][f"channel_{ch}"]["retained_volume_ml"].append(position.to_ml(home_fill + after["current_displacement_nl"]))
                            passed(f"Chemyx Channel {ch} {direction}", displacement_ml=after["current_displacement_ml"])
                        end = ledger.state(ch)
                        if end["home"] != starting[ch]["home"] or end["configuration_identity"] != starting[ch]["configuration_identity"] or end["current_displacement_nl"] != starting[ch]["current_displacement_nl"]:
                            raise RuntimeError(f"Channel {ch}: starting displacement/HOME not restored")
                        report["positions"][f"channel_{ch}"]["end_displacement_ml"] = end["current_displacement_ml"]
                        passed(f"Chemyx Channel {ch} restored")
                    for ch in (1, 2):
                        manual.confirm_idle(pump, ch, recorder, phase="instrument_checkout_final")
                        passed(f"Chemyx Channel {ch} stopped")
                active = "NMR communication"
                if not mock:
                    client = NmrRpcClient(NmrRpcConfig(host=nmr.host, port=nmr.port, scheme=nmr.scheme, timeout=nmr.timeout))
                    reply = client.ping()
                    if not reply or isinstance(reply, dict) and any(reply.get(k) is False for k in ("connected", "Connected", "ready", "Ready", "ok", "Ok")):
                        raise RuntimeError(f"NMR PING unsuccessful: {reply!r}")
                passed("NMR connection", simulated=mock)
                active = "NMR acquisition"
                recorder.record("instrument_checkout_nmr_request", scans=nmr.scans, route=nmr.route, result_type=nmr.result_type, simulated=mock)
                dx = acquire_nmr(nmr, output / "nmr_raw", mock=mock)
                if not dx.is_file() or dx.stat().st_size == 0:
                    raise RuntimeError("NMR acquisition did not save nonempty raw data")
                # Decode the returned FID, accepting all-zero signals. A saved RPC
                # error/JSON page is not evidence that raw NMR data was retrieved.
                read_jcamp_fid(dx)
                report["raw_fid"] = str(dx)
                passed("NMR two-scan acquisition", simulated=mock, raw_fid=str(dx))
                passed("NMR raw saved")
                active = "NMR processing"
                if raw["nmr"]["process_spectrum"]:
                    try:
                        report["processed_directory"] = str(process_nmr(dx, output, output.name))
                        passed("NMR processing")
                    except base.AnalysisInconclusiveError as exc:
                        report["checks"]["NMR processing"] = "UNAVAILABLE"
                        report["processing_warning"] = str(exc)
                        recorder.record("instrument_checkout_processing_unavailable", error=str(exc))
                        print(f"NMR processing: UNAVAILABLE ({exc}); acquisition succeeded")
                else:
                    report["checks"]["NMR processing"] = "UNAVAILABLE"
                    report["processing_warning"] = "Disabled in checkout YAML"
                active = "Final reference verification"
                final_needle = NeedleStateStore(needle_path).load()
                if (not final_needle.position_valid or final_needle.logical_position != saved.logical_position
                        or (final_needle.home_position, final_needle.steps_per_unit, final_needle.up_step_sign) != (saved.home_position, saved.steps_per_unit, saved.up_step_sign)):
                    raise RuntimeError("Arduino final starting position/HOME reference verification failed")
                for ch in (1, 2):
                    final = ledger.state(ch)
                    if final["position_state"] != "KNOWN" or final["home"] != starting[ch]["home"] or final["current_displacement_nl"] != starting[ch]["current_displacement_nl"]:
                        raise RuntimeError(f"Channel {ch}: final position/HOME verification failed")
                if dose_snapshot(settings.dose_ledger_dir) != doses_before:
                    raise RuntimeError("Production dose records changed during checkout; inspect concurrent activity")
                passed("HOME references preserved")
                passed("Production dose history unchanged")
                report["overall"] = "PASS"
        except (Exception, KeyboardInterrupt) as exc:
            report["overall"] = "FAILED"
            report["checks"][active] = "FAILED"
            report["attention"].append(f"{active}: {type(exc).__name__}: {exc}")
            print(report["attention"][-1])
            print("No compensating movement or position reset was attempted. Inspect any uncertain instrument before retrying.")
        finally:
            report["wall_seconds"] = round(time.monotonic() - begun, 3)
            # Timed metered-move convention: each live 1 mL/10 move is 6 s + 2 s margin.
            report["pump_timed_wait_seconds"] = sum(2 * (60 * c.transfer_volume_ml / c.rate_ml_min + manual.EXTRA_SECONDS) for c in settings.channels.values())
            (output / "checkout_summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            summary = "\n".join([f"SI6 SIMPLE INSTRUMENT CHECK [{report['mode']}]",
                                  *[f"{k}: {v}" for k, v in report["checks"].items()],
                                  *report["attention"], f"Overall: {report['overall']}",
                                  "Chemical analysis: NOT EVALUATED (no peak/integration requirement)",
                                  f"Results: {output}", report["position_evidence"]]) + "\n"
            (output / "checkout_summary.txt").write_text(summary, encoding="utf-8")
            print("\n" + summary)
    return report


def main(argv=None, *, input_fn=input):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--live", action="store_true")
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--validate-config", action="store_true", help="No hardware or state changes")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--machine-config", type=Path, help="Existing COM6 machine config override")
    args = parser.parse_args(argv)
    try:
        raw, arduino, settings, nmr = load_settings(args.config, args.machine_config)
        if not (args.live or args.mock):
            print(f"Configuration valid. No hardware contacted. Arduino {arduino['arduino']['port']}; Chemyx {settings.port}; NMR {nmr.host}:{nmr.port}, {nmr.scans} scans")
            return 0
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = config.REPO_ROOT / "results/instrument_checks" / f"{stamp}_{'mock' if args.mock else 'live'}_{uuid4().hex[:6]}"
        report = run_checkout(raw, arduino, settings, nmr, mock=args.mock, output=output, input_fn=input_fn)
        return 0 if report["overall"] == "PASS" else 1
    except (Exception, KeyboardInterrupt) as exc:
        print(f"Checkout FAILED: {type(exc).__name__}: {exc}")
        return 1
