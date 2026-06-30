"""Run the first-pass SOP-shaped pump/NMR workflow."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

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
from chemyx_lab.workflow import (
    WorkflowSettings,
    build_si6_sampling_steps,
    execute_workflow,
)


def latest_dx(data_dir: Path):
    files = list(data_dir.rglob("*.dx"))
    if not files:
        return None
    return max(files, key=lambda item: item.stat().st_mtime)


def confirm_real_run(args, cfg) -> bool:
    if not args.real:
        return True
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("Refusing real workflow movement without interactive confirmation.")
        return False
    print("This workflow can move the pump multiple times.")
    print(
        f"Port={cfg.port!r}, baud={cfg.baud_rate}, channel={cfg.channel}, "
        f"rate={args.rate} mL/min, volume_scale={args.volume_scale}."
    )
    return input("Type yes to continue: ").strip().lower() == "yes"


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a first SOP workflow pass.")
    parser.add_argument("--real", action="store_true", help="use real pump hardware")
    parser.add_argument("--yes", action="store_true", help="skip real-run prompt")
    parser.add_argument("--cycles", type=int, default=1)
    parser.add_argument("--rate", type=float, default=config.DEFAULT_RATE)
    parser.add_argument("--volume-scale", type=float, default=1.0)
    parser.add_argument(
        "--pause-scale",
        type=float,
        default=None,
        help="0 skips waits; 1 uses full SOP waits",
    )
    parser.add_argument("--port")
    parser.add_argument("--baud", type=int)
    parser.add_argument("--channel", type=int, choices=[0, 1, 2])
    parser.add_argument("--diameter", type=float)
    parser.add_argument("--data-dir", type=Path, help="optional DX directory to ingest")
    parser.add_argument("--target", type=float)
    parser.add_argument("--nmr-rpc", action="store_true", help="run NMR through RPC")
    parser.add_argument("--nmr-config", "--config", dest="nmr_config")
    parser.add_argument("--nmr-host")
    parser.add_argument("--nmr-port", type=int)
    parser.add_argument("--nmr-scheme")
    parser.add_argument("--nmr-timeout", type=float)
    parser.add_argument("--nmr-poll-seconds", type=float)
    parser.add_argument("--nmr-max-wait", type=float)
    parser.add_argument("--nmr-route", choices=["experiment", "iflow"])
    parser.add_argument("--nmr-experiment")
    parser.add_argument("--nmr-scans", type=int)
    parser.add_argument("--nmr-receiver-gain", type=float)
    parser.add_argument("--nmr-auto-gain", dest="nmr_auto_gain", action="store_true", default=None)
    parser.add_argument("--nmr-manual-gain", dest="nmr_auto_gain", action="store_false")
    parser.add_argument("--nmr-solvent")
    parser.add_argument("--nmr-spectral-center", type=float)
    parser.add_argument("--nmr-sweep-width", type=float)
    parser.add_argument("--nmr-result-name")
    parser.add_argument("--nmr-result-type", choices=["fid", "spectrum", "rawfid"])
    parser.add_argument("--nmr-save-dir", type=Path)
    args = parser.parse_args()

    try:
        nmr_settings = config.load_nmr_settings(
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
            save_dir=args.nmr_save_dir,
            target_ppm=args.target,
        )
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 1

    pause_scale = args.pause_scale
    if pause_scale is None:
        pause_scale = 1.0 if args.real else 0.0

    cfg = config.load_pump_config(
        port=args.port,
        baud_rate=args.baud,
        channel=args.channel,
        diameter=args.diameter,
        rate=args.rate,
    )

    print("=" * 72)
    print("Si6 SOP first-pass workflow")
    print("=" * 72)
    print(f"Mode        : {'REAL hardware' if args.real else 'MOCK'}")
    print(f"Cycles      : {args.cycles}")
    print(f"Port        : {cfg.port or '(unset)'} @ {cfg.baud_rate}")
    print(f"Channel     : {cfg.channel}")
    print(f"Rate        : {args.rate} mL/min")
    print(f"Volume scale: {args.volume_scale}")
    print(f"Pause scale : {pause_scale}")
    if args.nmr_rpc:
        print(
            "NMR RPC     : "
            f"{nmr_settings.scheme}://{nmr_settings.host}:{nmr_settings.port}, "
            f"route={nmr_settings.route}, scans={nmr_settings.scans}, "
            f"receiver_gain={nmr_settings.receiver_gain}"
        )

    if not confirm_real_run(args, cfg):
        print("Aborted before connecting.")
        return 1

    nmr_client = None
    if args.nmr_rpc:
        nmr_client = NmrRpcClient(
            NmrRpcConfig(
                host=nmr_settings.host,
                port=nmr_settings.port,
                scheme=nmr_settings.scheme,
                timeout=nmr_settings.timeout,
                poll_seconds=nmr_settings.poll_seconds,
                max_wait_seconds=nmr_settings.max_wait_seconds,
            )
        )

    def nmr_callback(step):
        if nmr_client is not None:
            if nmr_settings.route == "iflow":
                print("     NMR RPC -> fetching iFlow 1D settings")
                one_d_settings = build_iflow_1d_settings(
                    nmr_client.iflow_1d_settings(),
                    receiver_gain=nmr_settings.receiver_gain,
                    auto_gain=nmr_settings.auto_gain,
                )
                run_settings = build_iflow_experiment_settings(
                    nmr_client.iflow_experiment_settings(),
                    scans=nmr_settings.scans,
                    receiver_gain=nmr_settings.receiver_gain,
                    spectral_center=nmr_settings.spectral_center,
                    sweep_width=nmr_settings.sweep_width,
                )
                print("     NMR RPC -> setting iFlow 1D parameters")
                nmr_client.set_iflow_1d_settings(one_d_settings)
                print("     NMR RPC -> starting iFlow 1D experiment")
                nmr_client.run_iflow_experiment(run_settings)
                final_status = nmr_client.wait_for_idle(
                    status_getter=nmr_client.iflow_experiment_status
                )
                payload = final_status
            else:
                print("     NMR RPC -> fetching 1D settings")
                template = nmr_client.experiment_settings(nmr_settings.experiment)
                run_settings = build_1d_experiment_settings(
                    template,
                    scans=nmr_settings.scans,
                    solvent=nmr_settings.solvent,
                    spectral_center=nmr_settings.spectral_center,
                    sweep_width=nmr_settings.sweep_width,
                    receiver_gain=nmr_settings.receiver_gain,
                )
                print("     NMR RPC -> starting 1D experiment")
                nmr_client.start_experiment(run_settings, nmr_settings.experiment)
                final_status = nmr_client.wait_for_idle()
                results = nmr_client.experiment_results()
                result_name = nmr_settings.result_name
                if isinstance(results, list) and results and result_name not in results:
                    result_name = results[0]
                payload = nmr_client.experiment_result(
                    result_name,
                    fmt="jdx",
                    data_type=nmr_settings.result_type,
                )
            print(f"     NMR RPC -> finished: {final_status}")

            text = extract_text_payload(payload)
            nmr_settings.save_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            dx_file = nmr_settings.save_dir / f"{stamp}_{nmr_settings.result_name}.dx"
            dx_file.write_text(text, encoding="utf-8")
            try:
                result = analyze_dx_peak(dx_file, target_ppm=nmr_settings.target_ppm)
            except NmrProcessingError as exc:
                print(f"     NMR RPC -> saved {dx_file}, analysis failed: {exc}")
                return
            print(
                f"     NMR RPC -> saved {dx_file.name}: peak "
                f"{result.peak_ppm:.4f} ppm, SNR {result.snr:.2f}"
            )
            return

        if args.data_dir is None:
            print("     NMR placeholder -> acquire 1H and export DX")
            return
        dx_file = latest_dx(args.data_dir)
        if dx_file is None:
            print(f"     NMR ingest -> no .dx files found in {args.data_dir}")
            return
        try:
            result = analyze_dx_peak(dx_file, target_ppm=nmr_settings.target_ppm)
        except NmrProcessingError as exc:
            print(f"     NMR ingest -> {exc}")
            return
        print(
            f"     NMR ingest -> {dx_file.name}: peak {result.peak_ppm:.4f} ppm, "
            f"SNR {result.snr:.2f}"
        )

    settings = WorkflowSettings(
        cycles=args.cycles,
        rate_ml_min=args.rate,
        volume_scale=args.volume_scale,
        pause_scale=pause_scale,
        move_start_delay=0.0,
    )

    try:
        with Pump(
            port=cfg.port,
            baud_rate=cfg.baud_rate,
            channel=cfg.channel,
            units=cfg.units,
            timeout=cfg.timeout,
            response_delay=cfg.response_delay,
            mock=not args.real,
        ) as pump:
            print("\nConfigure pump")
            print("units    ->", repr(pump.set_units(cfg.units)))
            print("diameter ->", repr(pump.set_diameter(cfg.diameter)))
            steps = build_si6_sampling_steps(args.cycles)
            execute_workflow(steps, pump, settings, nmr_callback=nmr_callback)
    except (PumpConnectionError, EchoMismatchError, ValueError, NmrRpcError) as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print("\nWorkflow complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
