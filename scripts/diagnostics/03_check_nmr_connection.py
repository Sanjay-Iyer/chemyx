"""Check whether the NMReady/Nanalysis RPC API is reachable."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import _bootstrap  # noqa: F401

from chemyx_lab import config
from chemyx_lab.instruments.nmr import NmrRpcClient, NmrRpcConfig, NmrRpcError


def main() -> int:
    parser = argparse.ArgumentParser(description="Check NMReady RPC status.")
    parser.add_argument(
        "--machine-config",
        type=Path,
        default=config.REPO_ROOT / "configs" / "machines" / "00_machine.local.yaml",
        help="machine YAML config path",
    )
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--scheme")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args()

    try:
        machine = config.load_machine_config(args.machine_config)
        settings = config.load_nmr_settings(
            load_local=False,
            host=args.host if args.host is not None else machine.nmr.host,
            port=args.port if args.port is not None else machine.nmr.port,
            scheme=args.scheme if args.scheme is not None else machine.nmr.scheme,
            timeout=args.timeout if args.timeout is not None else machine.nmr.timeout_seconds,
            poll_seconds=machine.nmr.poll_seconds,
            max_wait_seconds=machine.nmr.max_wait_seconds,
        )
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 1

    client = NmrRpcClient(
        NmrRpcConfig(
            host=settings.host,
            port=settings.port,
            scheme=settings.scheme,
            timeout=settings.timeout,
        )
    )
    print(f"RPC base URL: {client.base_url}")

    checks = [
        ("PingSpectrometer", client.ping),
        ("RpcActive", client.rpc_active),
        ("RpcEnabled", client.rpc_enabled),
        ("Experiment/List", client.experiment_list),
    ]
    ok = True
    for label, getter in checks:
        try:
            value = getter()
        except NmrRpcError as exc:
            ok = False
            print(f"{label}: ERROR: {exc}")
            continue
        print(f"{label}: {json.dumps(value, indent=2)}")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
