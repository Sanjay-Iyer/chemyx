"""Real Chemyx + NMR bench test: withdraw, acquire NMR, infuse."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401

from chemyx_lab import config
from chemyx_lab.nmr import NmrProcessingError, analyze_dx_peak
from chemyx_lab.nmr_rpc import (
    NmrRpcClient,
    NmrRpcConfig,
    NmrRpcError,
    build_1d_experiment_settings,
    build_iflow_1d_settings,
    build_iflow_experiment_settings,
    extract_text_payload,
)
from chemyx_lab.pump import EchoMismatchError, Pump, PumpConnectionError


DEFAULT_PORT = "COM6"
DEFAULT_BAUD = 115200
DEFAULT_CHANNEL = 1
DEFAULT_DIAMETER_MM = 28.6
DEFAULT_RATE_ML_MIN = 2.0
DEFAULT_VOLUME_ML = 5.0
DEFAULT_SAVE_DIR = Path("runs") / "nmr" / "first_real_test"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a real first test: withdraw 5 mL, run NMR, infuse 5 mL."
    )
    parser.add_argument("--yes", action="store_true", help="skip real-run prompt")
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    parser.add_argument("--mock-pump", action="store_true", help="use fake pump responses")

    parser.add_argument("--pump-config", help="Chemyx JSON config path")
    parser.add_argument("--port", default=DEFAULT_PORT)
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD)
    parser.add_argument("--channel", type=int, choices=[0, 1, 2], default=DEFAULT_CHANNEL)
    parser.add_argument("--diameter", type=float, default=DEFAULT_DIAMETER_MM)
    parser.add_argument("--units", default="mL/min")
    parser.add_argument("--rate", type=float, default=DEFAULT_RATE_ML_MIN)
    parser.add_argument("--volume", type=float, default=DEFAULT_VOLUME_ML)
    parser.add_argument(
        "--pump-extra-seconds",
        type=float,
        default=2.0,
        help="extra wait after calculated pump move time before cleanup stop",
    )
    parser.add_argument(
        "--settle-before-nmr-seconds",
        type=float,
        default=0.0,
        help="optional pause after withdraw before starting NMR",
    )

    parser.add_argument("--nmr-config", "--config", dest="nmr_config")
    parser.add_argument("--nmr-host")
    parser.add_argument("--nmr-port", type=int)
    parser.add_argument("--nmr-scheme")
    parser.add_argument("--nmr-timeout", type=float)
    parser.add_argument("--nmr-poll-seconds", type=float)
    parser.add_argument("--nmr-max-wait", type=float)
    parser.add_argument("--nmr-route", choices=["experiment", "iflow"], default="iflow")
    parser.add_argument("--nmr-experiment")
    parser.add_argument("--nmr-scans", type=int, default=2)
    parser.add_argument("--nmr-receiver-gain", type=float, default=12.0)
    parser.add_argument("--nmr-auto-gain", dest="nmr_auto_gain", action="store_true", default=None)
    parser.add_argument("--nmr-manual-gain", dest="nmr_auto_gain", action="store_false")
    parser.add_argument("--nmr-solvent")
    parser.add_argument("--nmr-spectral-center", type=float)
    parser.add_argument("--nmr-sweep-width", type=float)
    parser.add_argument("--nmr-result-name")
    parser.add_argument("--nmr-result-type", choices=["fid", "spectrum", "rawfid"])
    parser.add_argument("--nmr-save-dir", type=Path, default=DEFAULT_SAVE_DIR)
    parser.add_argument("--target", type=float)
    return parser


def load_pump_settings(args) -> config.PumpConfig:
    return config.load_pump_config(
        args.pump_config,
        port=args.port,
        baud_rate=args.baud,
        channel=args.channel,
        units=args.units,
        diameter=args.diameter,
        rate=args.rate,
        volume=args.volume,
    )


def load_nmr_settings(args, save_dir: Path | None = None) -> config.NmrSettings:
    return config.load_nmr_settings(
        args.nmr_config,
        host=args.nmr_host,
        port=args.nmr_port,
        scheme=args.nmr_scheme,
        timeout=args.nmr_timeout,
        poll_seconds=args.nmr_poll_seconds,
        max_wait_seconds=args.nmr_max_wait,
        route=args.nmr_route,
        experiment=args.nmr_experiment,
        scans=args.nmr_scans,
        receiver_gain=args.nmr_receiver_gain,
        auto_gain=args.nmr_auto_gain,
        solvent=args.nmr_solvent,
        spectral_center=args.nmr_spectral_center,
        sweep_width=args.nmr_sweep_width,
        result_name=args.nmr_result_name,
        result_type=args.nmr_result_type,
        save_dir=save_dir or args.nmr_save_dir,
        target_ppm=args.target,
    )


def move_seconds(volume_ml: float, rate: float, units: int) -> float:
    """Estimate pump travel time for a volume expressed in mL."""
    if rate <= 0:
        raise ValueError("Pump rate must be positive")
    if units == 0:
        return abs(volume_ml) / rate * 60.0
    if units == 1:
        return abs(volume_ml) / rate * 3600.0
    if units == 2:
        return abs(volume_ml) * 1000.0 / rate * 60.0
    if units == 3:
        return abs(volume_ml) * 1000.0 / rate * 3600.0
    raise ValueError(f"Unsupported Chemyx units code: {units}")


def format_seconds(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, rem = divmod(seconds, 60)
    if minutes:
        return f"{minutes} min {rem} s"
    return f"{rem} s"


def sleep_with_progress(label: str, seconds: float, mock: bool = False) -> None:
    if mock or seconds <= 0:
        return
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return
        print(f"     {label}: {format_seconds(remaining)} remaining")
        time.sleep(min(30.0, remaining))


def confirm_run(args, pump_cfg: config.PumpConfig, nmr_settings: config.NmrSettings) -> bool:
    if args.dry_run or args.mock_pump or args.yes:
        return True
    if not sys.stdin.isatty():
        print("Refusing real pump movement without interactive confirmation.")
        return False

    one_move = move_seconds(args.volume, pump_cfg.rate, pump_cfg.units)
    print("This will physically move the Chemyx pump and run the NMR.")
    print(
        f"Pump: {pump_cfg.port} @ {pump_cfg.baud_rate}, channel {pump_cfg.channel}, "
        f"{args.volume} mL withdraw then {args.volume} mL infuse at "
        f"{pump_cfg.rate} {config.UNITS[pump_cfg.units]}."
    )
    print(
        f"Pump timing: about {format_seconds(one_move)} per move "
        f"({format_seconds(one_move * 2)} total pump motion)."
    )
    print(
        f"NMR: {nmr_settings.scheme}://{nmr_settings.host}:{nmr_settings.port}, "
        f"route={nmr_settings.route}, scans={nmr_settings.scans}, "
        f"receiver_gain={nmr_settings.receiver_gain}, auto_gain={nmr_settings.auto_gain}."
    )
    return input("Type yes to continue: ").strip().lower() == "yes"


def configure_pump(pump: Pump, pump_cfg: config.PumpConfig) -> None:
    print("\n[1] Configure Chemyx pump")
    print("units    ->", repr(pump.set_units(pump_cfg.units)))
    print("diameter ->", repr(pump.set_diameter(pump_cfg.diameter)))
    print("rate     ->", repr(pump.set_rate(pump_cfg.rate)))


def run_metered_move(
    pump: Pump,
    pump_cfg: config.PumpConfig,
    direction: str,
    volume_ml: float,
    extra_seconds: float = 2.0,
    mock: bool = False,
) -> None:
    wait_seconds = move_seconds(volume_ml, pump_cfg.rate, pump_cfg.units) + max(
        0.0, extra_seconds
    )
    signed_volume = abs(volume_ml) if direction == "infuse" else -abs(volume_ml)
    print(
        f"     {direction} {abs(volume_ml):.4g} mL at "
        f"{pump_cfg.rate:.4g} {config.UNITS[pump_cfg.units]}"
    )
    print("     volume ->", repr(pump.set_volume(signed_volume)))
    print("     start  ->", repr(pump.start(delay=0)))
    sleep_with_progress(direction, wait_seconds, mock=mock)
    print("     stop   ->", repr(pump.stop()))


def make_dx_path(save_dir: Path, label: str, nmr_settings: config.NmrSettings) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    gain = "auto" if nmr_settings.receiver_gain is None else f"gain{nmr_settings.receiver_gain:g}"
    gain = gain.replace(".", "p")
    return save_dir / f"{stamp}_{label}_{nmr_settings.scans}scan_{gain}.dx"


def summarize_nmr_status(status: Any) -> None:
    if not isinstance(status, dict):
        print(f"     final status -> {status!r}")
        return
    receipt = status.get("OriginalReceipt") or {}
    settings = receipt.get("Settings") or {}
    print(
        "     final status -> "
        f"ResultCode={status.get('ResultCode')}, "
        f"NumberOfScansRun={status.get('NumberOfScansRun')}, "
        f"TimeStamp={receipt.get('TimeStamp')}"
    )
    print(
        "     actual settings -> "
        f"NumberOfScans={settings.get('NumberOfScans')}, "
        f"ReceiverGain={settings.get('ReceiverGain')}, "
        f"SpectralCentreInPpm={settings.get('SpectralCentreInPpm')}, "
        f"SpectralWidthInPpm={settings.get('SpectralWidthInPpm')}"
    )


def run_nmr_acquisition(
    nmr_settings: config.NmrSettings,
    save_dir: Path,
    label: str = "first_real_test",
) -> Path:
    save_dir.mkdir(parents=True, exist_ok=True)
    dx_path = make_dx_path(save_dir, label, nmr_settings)
    client = NmrRpcClient(
        NmrRpcConfig(
            host=nmr_settings.host,
            port=nmr_settings.port,
            scheme=nmr_settings.scheme,
            timeout=nmr_settings.timeout,
            poll_seconds=nmr_settings.poll_seconds,
            max_wait_seconds=nmr_settings.max_wait_seconds,
        )
    )

    print("\n[3] Run NMR")
    print(
        "     settings -> "
        f"{client.base_url}, route={nmr_settings.route}, scans={nmr_settings.scans}, "
        f"receiver_gain={nmr_settings.receiver_gain}, auto_gain={nmr_settings.auto_gain}"
    )
    if nmr_settings.route == "iflow":
        one_d_settings = build_iflow_1d_settings(
            client.iflow_1d_settings(),
            receiver_gain=nmr_settings.receiver_gain,
            auto_gain=nmr_settings.auto_gain,
            export_filename=str(dx_path),
        )
        run_settings = build_iflow_experiment_settings(
            client.iflow_experiment_settings(),
            scans=nmr_settings.scans,
            receiver_gain=nmr_settings.receiver_gain,
            spectral_center=nmr_settings.spectral_center,
            sweep_width=nmr_settings.sweep_width,
            export_filename=str(dx_path),
        )
        print("     set iFlow 1D parameters")
        client.set_iflow_1d_settings(one_d_settings)
        print("     start iFlow 1D experiment")
        client.run_iflow_experiment(run_settings)
        final_status = client.wait_for_idle(status_getter=client.iflow_experiment_status)
        payload = final_status
    else:
        template = client.experiment_settings(nmr_settings.experiment)
        run_settings = build_1d_experiment_settings(
            template,
            scans=nmr_settings.scans,
            solvent=nmr_settings.solvent,
            spectral_center=nmr_settings.spectral_center,
            sweep_width=nmr_settings.sweep_width,
            receiver_gain=nmr_settings.receiver_gain,
            export_filename=str(dx_path),
        )
        print(f"     start {nmr_settings.experiment} experiment")
        client.start_experiment(run_settings, nmr_settings.experiment)
        final_status = client.wait_for_idle()
        results = client.experiment_results()
        result_name = nmr_settings.result_name
        if isinstance(results, list) and results and result_name not in results:
            result_name = results[0]
        payload = client.experiment_result(
            result_name,
            fmt="jdx",
            data_type=nmr_settings.result_type,
        )

    summarize_nmr_status(final_status)
    text = extract_text_payload(payload)
    dx_path.write_text(text, encoding="utf-8")
    print(f"     saved -> {dx_path}")

    if nmr_settings.result_type == "fid":
        try:
            peak = analyze_dx_peak(dx_path, target_ppm=nmr_settings.target_ppm)
        except NmrProcessingError as exc:
            print(f"     analysis warning -> {exc}")
        else:
            print(
                f"     peak near {nmr_settings.target_ppm:g} ppm -> "
                f"{peak.peak_ppm:.4f} ppm, SNR {peak.snr:.2f}"
            )
    return dx_path


def print_plan(args, pump_cfg: config.PumpConfig, nmr_settings: config.NmrSettings) -> None:
    one_move = move_seconds(args.volume, pump_cfg.rate, pump_cfg.units)
    print("=" * 72)
    print("First real Chemyx + NMR test")
    print("=" * 72)
    print(f"Pump port      : {pump_cfg.port} @ {pump_cfg.baud_rate}")
    print(f"Pump channel   : {pump_cfg.channel}")
    print(f"Syringe ID     : {pump_cfg.diameter} mm")
    print(f"Pump move      : withdraw {args.volume} mL, then infuse {args.volume} mL")
    print(f"Pump rate      : {pump_cfg.rate} {config.UNITS[pump_cfg.units]}")
    print(f"Move estimate  : {format_seconds(one_move)} per pump move")
    print(f"NMR RPC        : {nmr_settings.scheme}://{nmr_settings.host}:{nmr_settings.port}")
    print(f"NMR settings   : route={nmr_settings.route}, scans={nmr_settings.scans}")
    print(
        f"NMR gain       : receiver_gain={nmr_settings.receiver_gain}, "
        f"auto_gain={nmr_settings.auto_gain}"
    )
    print(f"NMR save dir   : {nmr_settings.save_dir}")
    print("=" * 72)


def main() -> int:
    args = build_parser().parse_args()
    try:
        pump_cfg = load_pump_settings(args)
        nmr_settings = load_nmr_settings(args)
        print_plan(args, pump_cfg, nmr_settings)
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 1

    if args.dry_run:
        print("Dry run only. No pump movement or NMR acquisition started.")
        return 0

    if not confirm_run(args, pump_cfg, nmr_settings):
        print("Aborted before connecting.")
        return 1

    try:
        with Pump(
            port=pump_cfg.port,
            baud_rate=pump_cfg.baud_rate,
            channel=pump_cfg.channel,
            units=pump_cfg.units,
            timeout=pump_cfg.timeout,
            response_delay=pump_cfg.response_delay,
            mock=args.mock_pump,
        ) as pump:
            configure_pump(pump, pump_cfg)
            print("\n[2] Withdraw sample")
            run_metered_move(
                pump,
                pump_cfg,
                "withdraw",
                args.volume,
                extra_seconds=args.pump_extra_seconds,
                mock=args.mock_pump,
            )
            nmr_error = None
            try:
                sleep_with_progress(
                    "settle before NMR",
                    args.settle_before_nmr_seconds,
                    mock=args.mock_pump,
                )
                run_nmr_acquisition(nmr_settings, nmr_settings.save_dir)
            except Exception as exc:
                nmr_error = exc
                print("\nNMR step failed; attempting to infuse the withdrawn volume back.")
            print("\n[4] Infuse sample back")
            run_metered_move(
                pump,
                pump_cfg,
                "infuse",
                args.volume,
                extra_seconds=args.pump_extra_seconds,
                mock=args.mock_pump,
            )
            if nmr_error is not None:
                raise nmr_error
    except (PumpConnectionError, EchoMismatchError, ValueError, NmrRpcError) as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print("\nSUCCESS: first real Chemyx + NMR test completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
