"""Check whether the NMReady/Nanalysis RPC API is reachable."""

from __future__ import annotations

import argparse
import json

import _bootstrap  # noqa: F401

from chemyx_lab import config
from chemyx_lab.nmr_rpc import NmrRpcClient, NmrRpcConfig, NmrRpcError


def main() -> int:
    parser = argparse.ArgumentParser(description="Check NMReady RPC status.")
    parser.add_argument("--nmr-config", "--config", dest="nmr_config")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--scheme")
    parser.add_argument("--timeout", type=float)
    args = parser.parse_args()

    try:
        settings = config.load_nmr_settings(
            args.nmr_config,
            host=args.host,
            port=args.port,
            scheme=args.scheme,
            timeout=args.timeout,
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
