"""Start a documented NMReady 1D experiment through the RPC API."""

from __future__ import annotations

import argparse
import json
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
    example_iflow_1d_settings,
    example_iflow_experiment_settings,
    example_1d_experiment_settings,
    extract_text_payload,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a 1D NMR acquisition over RPC.")
    parser.add_argument("--nmr-config", "--config", dest="nmr_config")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--scheme")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--poll-seconds", type=float)
    parser.add_argument("--max-wait", type=float)
    parser.add_argument(
        "--route",
        choices=["experiment", "iflow"],
        help="RPC route to use for acquisition",
    )
    parser.add_argument("--experiment")
    parser.add_argument("--scans", type=int)
    parser.add_argument("--solvent")
    parser.add_argument("--spectral-center", type=float)
    parser.add_argument("--sweep-width", type=float)
    parser.add_argument("--receiver-gain", type=float)
    parser.add_argument(
        "--auto-gain",
        dest="auto_gain",
        action="store_true",
        default=None,
        help="leave iFlow 1D AutoGain enabled",
    )
    parser.add_argument(
        "--manual-gain",
        dest="auto_gain",
        action="store_false",
        help="disable iFlow 1D AutoGain so receiver-gain is used",
    )
    parser.add_argument("--result-name")
    parser.add_argument("--result-type", choices=["fid", "spectrum", "rawfid"])
    parser.add_argument("--save-dx", type=Path, help="write retrieved JCAMP-DX here")
    parser.add_argument("--target", type=float)
    parser.add_argument("--mock-settings", action="store_true", help="use documented example settings")
    parser.add_argument("--dry-run", action="store_true", help="print settings only")
    args = parser.parse_args()

    try:
        nmr_settings = config.load_nmr_settings(
            args.nmr_config,
            host=args.host,
            port=args.port,
            scheme=args.scheme,
            timeout=args.timeout,
            poll_seconds=args.poll_seconds,
            max_wait_seconds=args.max_wait,
            route=args.route,
            experiment=args.experiment,
            scans=args.scans,
            solvent=args.solvent,
            spectral_center=args.spectral_center,
            sweep_width=args.sweep_width,
            receiver_gain=args.receiver_gain,
            auto_gain=args.auto_gain,
            result_name=args.result_name,
            result_type=args.result_type,
            target_ppm=args.target,
        )
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 1

    rpc_cfg = NmrRpcConfig(
        host=nmr_settings.host,
        port=nmr_settings.port,
        scheme=nmr_settings.scheme,
        timeout=nmr_settings.timeout,
        poll_seconds=nmr_settings.poll_seconds,
        max_wait_seconds=nmr_settings.max_wait_seconds,
    )
    client = NmrRpcClient(rpc_cfg)

    try:
        print(
            "NMR settings: "
            f"{client.base_url}, route={nmr_settings.route}, scans={nmr_settings.scans}, "
            f"receiver_gain={nmr_settings.receiver_gain}, auto_gain={nmr_settings.auto_gain}"
        )
        if nmr_settings.route == "iflow":
            one_d_template = (
                example_iflow_1d_settings()
                if args.mock_settings
                else client.iflow_1d_settings()
            )
            exp_template = (
                example_iflow_experiment_settings()
                if args.mock_settings
                else client.iflow_experiment_settings()
            )
            one_d_settings = build_iflow_1d_settings(
                one_d_template,
                receiver_gain=nmr_settings.receiver_gain,
                auto_gain=nmr_settings.auto_gain,
                export_filename=str(args.save_dx) if args.save_dx else None,
            )
            run_settings = build_iflow_experiment_settings(
                exp_template,
                scans=nmr_settings.scans,
                receiver_gain=nmr_settings.receiver_gain,
                spectral_center=nmr_settings.spectral_center,
                sweep_width=nmr_settings.sweep_width,
                export_filename=str(args.save_dx) if args.save_dx else None,
            )
            if args.dry_run:
                print(
                    json.dumps(
                        {
                            "PUT /interfaces/iFlow/Settings/1D": one_d_settings,
                            "PUT /interfaces/iFlow/RunExperiment": run_settings,
                        },
                        indent=2,
                    )
                )
                return 0

            print(f"Setting iFlow 1D parameters on {client.base_url}")
            print(client.set_iflow_1d_settings(one_d_settings))
            print("Starting iFlow 1D experiment")
            print(client.run_iflow_experiment(run_settings))
            final_status = client.wait_for_idle(status_getter=client.iflow_experiment_status)
            print(f"Final status: {final_status}")
            payload = final_status
        else:
            template = (
                example_1d_experiment_settings()
                if args.mock_settings
                else client.experiment_settings(nmr_settings.experiment)
            )
            run_settings = build_1d_experiment_settings(
                template,
                scans=nmr_settings.scans,
                solvent=nmr_settings.solvent,
                spectral_center=nmr_settings.spectral_center,
                sweep_width=nmr_settings.sweep_width,
                receiver_gain=nmr_settings.receiver_gain,
                export_filename=str(args.save_dx) if args.save_dx else None,
            )
            if args.dry_run:
                print(json.dumps(run_settings, indent=2))
                return 0

            print(f"Starting {nmr_settings.experiment} experiment on {client.base_url}")
            print(client.start_experiment(run_settings, nmr_settings.experiment))
            final_status = client.wait_for_idle()
            print(f"Final status: {final_status}")

            results = client.experiment_results()
            print(f"Available results: {results}")
            result_name = nmr_settings.result_name
            if isinstance(results, list) and results and result_name not in results:
                result_name = results[0]
            payload = client.experiment_result(result_name, fmt="jdx", data_type=nmr_settings.result_type)

        text = extract_text_payload(payload)
        if args.save_dx:
            args.save_dx.parent.mkdir(parents=True, exist_ok=True)
            args.save_dx.write_text(text, encoding="utf-8")
            print(f"Saved result to {args.save_dx}")
            if nmr_settings.result_type == "fid":
                try:
                    peak = analyze_dx_peak(args.save_dx, target_ppm=nmr_settings.target_ppm)
                except NmrProcessingError as exc:
                    print(f"Saved file could not be analyzed as JCAMP-DX yet: {exc}")
                else:
                    print(
                        f"Peak near {nmr_settings.target_ppm} ppm: "
                        f"{peak.peak_ppm:.4f} ppm, SNR {peak.snr:.2f}"
                    )
        else:
            print(text[:1000])
            if len(text) > 1000:
                print("... output truncated; use --save-dx to write the full result")
    except NmrRpcError as exc:
        print(f"FAILED: {exc}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
