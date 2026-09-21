"""Read-only NMReady/Nanalysis HTTP RPC communication smoke test."""

from __future__ import annotations

import argparse
import json
from urllib import error, request


READ_ONLY_ENDPOINTS = (
    ("PingSpectrometer", "/interfaces/iStatus/PingSpectrometer"),
    ("RpcEnabled", "/interfaces/iStatus/RpcEnabled"),
    ("SpectrometerStatus", "/interfaces/iStatus/SpectrometerStatus"),
)


def get(base_url: str, path: str, timeout: float):
    url = base_url.rstrip("/") + path
    req = request.Request(
        url,
        headers={"Accept": "application/json, text/plain, */*"},
        method="GET",
    )
    with request.urlopen(req, timeout=timeout) as response:
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
    text = raw.decode("utf-8", errors="replace")
    if text and ("json" in content_type.lower() or text[:1] in "[{"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Call three read-only status endpoints on an NMReady NMR."
    )
    parser.add_argument("--host", default="169.254.30.54")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--scheme", choices=("http", "https"), default="http")
    parser.add_argument("--timeout", type=float, default=10.0)
    args = parser.parse_args()

    base_url = f"{args.scheme}://{args.host}:{args.port}"
    print(f"NMR RPC base URL: {base_url}")
    print("Only read-only GET status requests will be sent.")

    failures = 0
    for label, path in READ_ONLY_ENDPOINTS:
        url = base_url + path
        print(f"\nGET {url}")
        try:
            value = get(base_url, path, args.timeout)
        except error.HTTPError as exc:
            failures += 1
            detail = exc.read().decode("utf-8", errors="replace")
            print(f"FAIL: HTTP {exc.code}: {detail}")
        except (error.URLError, TimeoutError, OSError) as exc:
            failures += 1
            print(f"FAIL: {exc}")
        else:
            print("RX:", json.dumps(value, indent=2) if not isinstance(value, str) else value)

    if failures:
        print(f"\nFAIL: {failures} NMR status request(s) failed.")
        return 1

    print("\nPASS: the laptop communicated with the NMR RPC service.")
    print("No acquisition or settings-change request was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
