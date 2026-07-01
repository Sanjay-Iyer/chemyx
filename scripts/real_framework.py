"""Configurable real Chemyx + NMR workflow framework."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import _bootstrap  # noqa: F401

import first_real_test
from chemyx_lab import config
from chemyx_lab.nmr_rpc import NmrRpcError
from chemyx_lab.pump import EchoMismatchError, Pump, PumpConnectionError


DEFAULT_SAVE_DIR = Path("runs") / "nmr" / "real_framework"
DEFAULT_WORKFLOW_CONFIG = config.REPO_ROOT / "configs" / "real_framework.local.json"


@dataclass(frozen=True)
class FrameworkSettings:
    cycles: int
    sequence: list[str]
    withdraw_volume_ml: float
    infuse_volume_ml: float
    settle_before_nmr_seconds: float
    between_cycles_minutes: float


def build_parser(defaults: dict | None = None) -> argparse.ArgumentParser:
    parser = first_real_test.build_parser(defaults)
    parser.description = (
        "Framework workflow: repeat withdraw -> optional pause -> NMR -> infuse."
    )
    defaults = defaults or first_real_test.default_arg_values(
        DEFAULT_WORKFLOW_CONFIG,
        DEFAULT_SAVE_DIR,
    )
    parser.add_argument("--cycles", type=int, default=defaults["cycles"])
    parser.add_argument(
        "--between-cycles-minutes",
        type=float,
        default=defaults["between_cycles_minutes"],
        help="pause after each cycle except the last",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    config_path = first_real_test.preparse_workflow_config(argv, DEFAULT_WORKFLOW_CONFIG)
    defaults = first_real_test.default_arg_values(
        DEFAULT_WORKFLOW_CONFIG,
        DEFAULT_SAVE_DIR,
    )
    defaults = first_real_test.load_workflow_defaults(config_path, defaults)
    defaults["workflow_config"] = config_path
    return build_parser(defaults).parse_args(argv)


def load_framework_settings(args) -> FrameworkSettings:
    if args.cycles < 1:
        raise ValueError("cycles must be at least 1")
    withdraw_volume, infuse_volume = first_real_test.effective_move_volumes(args)
    return FrameworkSettings(
        cycles=args.cycles,
        sequence=first_real_test.normalize_sequence(args.sequence),
        withdraw_volume_ml=withdraw_volume,
        infuse_volume_ml=infuse_volume,
        settle_before_nmr_seconds=args.settle_before_nmr_seconds,
        between_cycles_minutes=args.between_cycles_minutes,
    )


def print_plan(
    args,
    pump_cfg: config.PumpConfig,
    nmr_settings: config.NmrSettings,
    framework: FrameworkSettings,
) -> None:
    withdraw_seconds = first_real_test.move_seconds(
        framework.withdraw_volume_ml, pump_cfg.rate, pump_cfg.units
    )
    infuse_seconds = first_real_test.move_seconds(
        framework.infuse_volume_ml, pump_cfg.rate, pump_cfg.units
    )
    print("=" * 72)
    print("Real Chemyx + NMR framework")
    print("=" * 72)
    print(f"Config file    : {args.workflow_config}")
    print(f"Cycles         : {framework.cycles}")
    print(f"Sequence       : {first_real_test.format_sequence(framework.sequence)}")
    print(f"Pump port      : {pump_cfg.port} @ {pump_cfg.baud_rate}")
    print(f"Pump channel   : {pump_cfg.channel}")
    print(f"Syringe ID     : {pump_cfg.diameter} mm")
    print(
        f"Pump moves     : withdraw {framework.withdraw_volume_ml} mL, "
        f"infuse {framework.infuse_volume_ml} mL"
    )
    print(f"Pump rate      : {pump_cfg.rate} {config.UNITS[pump_cfg.units]}")
    print(
        "Move estimate  : "
        f"withdraw {first_real_test.format_seconds(withdraw_seconds)}, "
        f"infuse {first_real_test.format_seconds(infuse_seconds)}"
    )
    print(f"Pre-NMR pause  : {first_real_test.format_seconds(framework.settle_before_nmr_seconds)}")
    print(f"Cycle interval : {framework.between_cycles_minutes} min after each completed cycle")
    print(f"NMR RPC        : {nmr_settings.scheme}://{nmr_settings.host}:{nmr_settings.port}")
    print(
        f"NMR settings   : route={nmr_settings.route}, scans={nmr_settings.scans}, "
        f"receiver_gain={nmr_settings.receiver_gain}, auto_gain={nmr_settings.auto_gain}"
    )
    print(f"NMR save dir   : {nmr_settings.save_dir}")
    print("=" * 72)


def confirm_framework(args, pump_cfg, nmr_settings, framework: FrameworkSettings) -> bool:
    args_for_prompt = argparse.Namespace(**vars(args))
    args_for_prompt.volume = max(
        framework.withdraw_volume_ml,
        framework.infuse_volume_ml,
    )
    return first_real_test.confirm_run(args_for_prompt, pump_cfg, nmr_settings)


def main() -> int:
    try:
        args = parse_args()
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 1
    try:
        framework = load_framework_settings(args)
        pump_cfg = first_real_test.load_pump_settings(args)
        nmr_settings = first_real_test.load_nmr_settings(args, save_dir=args.nmr_save_dir)
        print_plan(args, pump_cfg, nmr_settings, framework)
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 1

    if args.dry_run:
        print("Dry run only. No pump movement or NMR acquisition started.")
        return 0

    if not confirm_framework(args, pump_cfg, nmr_settings, framework):
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
            first_real_test.configure_pump(pump, pump_cfg)
            for cycle in range(1, framework.cycles + 1):
                print(f"\n=== Cycle {cycle} of {framework.cycles} ===")
                net_withdrawn = 0.0
                nmr_count = 0
                for index, event in enumerate(framework.sequence, start=1):
                    if event == "W":
                        print(f"[{index}] Withdraw")
                        first_real_test.run_metered_move(
                            pump,
                            pump_cfg,
                            "withdraw",
                            framework.withdraw_volume_ml,
                            extra_seconds=args.pump_extra_seconds,
                            mock=args.mock_pump,
                        )
                        net_withdrawn += framework.withdraw_volume_ml
                    elif event == "I":
                        print(f"[{index}] Infuse")
                        first_real_test.run_metered_move(
                            pump,
                            pump_cfg,
                            "infuse",
                            framework.infuse_volume_ml,
                            extra_seconds=args.pump_extra_seconds,
                            mock=args.mock_pump,
                        )
                        net_withdrawn = max(0.0, net_withdrawn - framework.infuse_volume_ml)
                    elif event == "N":
                        print(f"[{index}] NMR")
                        nmr_count += 1
                        try:
                            first_real_test.sleep_with_progress(
                                "settle before NMR",
                                framework.settle_before_nmr_seconds,
                                mock=args.mock_pump,
                            )
                            first_real_test.run_nmr_acquisition(
                                nmr_settings,
                                nmr_settings.save_dir,
                                label=f"framework_cycle{cycle:03d}_nmr{nmr_count:02d}",
                            )
                        except Exception:
                            if net_withdrawn > 0:
                                print(
                                    "\nNMR step failed; attempting to infuse "
                                    f"{net_withdrawn:.4g} mL back."
                                )
                                first_real_test.run_metered_move(
                                    pump,
                                    pump_cfg,
                                    "infuse",
                                    net_withdrawn,
                                    extra_seconds=args.pump_extra_seconds,
                                    mock=args.mock_pump,
                                )
                            raise
                if cycle < framework.cycles:
                    first_real_test.sleep_with_progress(
                        "between cycles",
                        framework.between_cycles_minutes * 60.0,
                        mock=args.mock_pump,
                    )
    except (PumpConnectionError, EchoMismatchError, ValueError, NmrRpcError) as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print("\nSUCCESS: framework run completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
