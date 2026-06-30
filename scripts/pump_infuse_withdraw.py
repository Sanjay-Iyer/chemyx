"""Hardware-proven Chemyx 4000X infuse/withdraw smoke test."""

from __future__ import annotations

import argparse
import sys
import time

import _bootstrap  # noqa: F401

from chemyx_lab import config
from chemyx_lab.pump import EchoMismatchError, Pump, PumpConnectionError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the validated infuse then withdraw pump smoke test."
    )
    parser.add_argument("--mock", action="store_true", help="use the fake pump")
    parser.add_argument("--yes", action="store_true", help="skip movement prompt")
    parser.add_argument("--port", help="serial port, e.g. COM4")
    parser.add_argument("--baud", type=int, help="baud rate shown on pump")
    parser.add_argument("--channel", type=int, choices=[0, 1, 2], help="pump channel")
    parser.add_argument("--diameter", type=float, help="syringe ID in mm")
    parser.add_argument("--rate", type=float, help="flow rate in configured units")
    parser.add_argument("--volume", type=float, help="test volume in mL")
    parser.add_argument("--infuse-seconds", type=float, default=10.0)
    parser.add_argument("--withdraw-seconds", type=float, default=10.0)
    parser.add_argument("--settle-seconds", type=float, default=2.0)
    return parser


def confirm(args, cfg) -> bool:
    if args.mock:
        print("[MOCK] No physical movement.")
        return True
    if args.yes:
        return True
    if not sys.stdin.isatty():
        print("Refusing real movement without an interactive confirmation.")
        print("Run in a terminal or pass --yes after checking the setup.")
        return False
    print("The pump will physically move.")
    print(
        f"Port={cfg.port!r}, baud={cfg.baud_rate}, channel={cfg.channel}, "
        f"volume={cfg.volume} mL, rate={cfg.rate} {config.UNITS[cfg.units]}."
    )
    answer = input("Type yes to continue: ").strip().lower()
    return answer == "yes"


def main() -> int:
    args = build_parser().parse_args()
    cfg = config.load_pump_config(
        port=args.port,
        baud_rate=args.baud,
        channel=args.channel,
        diameter=args.diameter,
        rate=args.rate,
        volume=args.volume,
    )

    print("=" * 72)
    print("Chemyx Fusion 4000X infuse/withdraw smoke test")
    print("=" * 72)
    print(f"Mode     : {'MOCK' if args.mock else 'REAL hardware'}")
    print(f"Port     : {cfg.port or '(unset)'} @ {cfg.baud_rate}")
    print(f"Channel  : {cfg.channel} (0 means no channel prefix)")
    print(f"Syringe  : {cfg.diameter} mm")
    print(f"Move     : {cfg.volume} mL at {cfg.rate} {config.UNITS[cfg.units]}")

    if not confirm(args, cfg):
        print("Aborted before connecting.")
        return 1

    try:
        with Pump(
            port=cfg.port,
            baud_rate=cfg.baud_rate,
            channel=cfg.channel,
            units=cfg.units,
            timeout=cfg.timeout,
            response_delay=cfg.response_delay,
            mock=args.mock,
        ) as pump:
            print("\n[1] Configure channel")
            print("units    ->", repr(pump.set_units(cfg.units)))
            print("diameter ->", repr(pump.set_diameter(cfg.diameter)))
            print("rate     ->", repr(pump.set_rate(cfg.rate)))

            print("\n[2] Infuse")
            print("volume   ->", repr(pump.set_volume(abs(cfg.volume))))
            print("start    ->", repr(pump.start(delay=0)))
            time.sleep(0.0 if args.mock else args.infuse_seconds)
            print("stop     ->", repr(pump.stop()))

            print("\n[3] Settle")
            time.sleep(0.0 if args.mock else args.settle_seconds)

            print("\n[4] Withdraw")
            print("volume   ->", repr(pump.set_volume(-abs(cfg.volume))))
            print("start    ->", repr(pump.start(delay=0)))
            time.sleep(0.0 if args.mock else args.withdraw_seconds)
            print("stop     ->", repr(pump.stop()))

    except (PumpConnectionError, EchoMismatchError, ValueError) as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print("\nSUCCESS: pump sequence completed and port closed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
