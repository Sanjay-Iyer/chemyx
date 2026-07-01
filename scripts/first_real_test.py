"""Real Chemyx + NMR bench test: withdraw, acquire NMR, infuse."""

from __future__ import annotations

import argparse
import json
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
DEFAULT_WORKFLOW_CONFIG = config.REPO_ROOT / "configs" / "first_real_test.local.json"


PUMP_CONFIG_KEYS = {
    "port": "port",
    "pump_port": "port",
    "baud": "baud",
    "baud_rate": "baud",
    "baudrate": "baud",
    "channel": "channel",
    "pump_channel": "channel",
    "diameter": "diameter",
    "diameter_mm": "diameter",
    "syringe_diameter_mm": "diameter",
    "units": "units",
    "rate": "rate",
    "pump_rate": "rate",
    "rate_ml_min": "rate",
}

NMR_CONFIG_KEYS = {
    "host": "nmr_host",
    "ip": "nmr_host",
    "ip_address": "nmr_host",
    "nmr_host": "nmr_host",
    "port": "nmr_port",
    "nmr_port": "nmr_port",
    "scheme": "nmr_scheme",
    "nmr_scheme": "nmr_scheme",
    "timeout": "nmr_timeout",
    "nmr_timeout": "nmr_timeout",
    "poll_seconds": "nmr_poll_seconds",
    "nmr_poll_seconds": "nmr_poll_seconds",
    "max_wait_seconds": "nmr_max_wait",
    "nmr_max_wait": "nmr_max_wait",
    "route": "nmr_route",
    "nmr_route": "nmr_route",
    "experiment": "nmr_experiment",
    "nmr_experiment": "nmr_experiment",
    "scans": "nmr_scans",
    "nmr_scans": "nmr_scans",
    "receiver_gain": "nmr_receiver_gain",
    "nmr_receiver_gain": "nmr_receiver_gain",
    "auto_gain": "nmr_auto_gain",
    "nmr_auto_gain": "nmr_auto_gain",
    "solvent": "nmr_solvent",
    "nmr_solvent": "nmr_solvent",
    "spectral_center": "nmr_spectral_center",
    "nmr_spectral_center": "nmr_spectral_center",
    "sweep_width": "nmr_sweep_width",
    "nmr_sweep_width": "nmr_sweep_width",
    "result_name": "nmr_result_name",
    "nmr_result_name": "nmr_result_name",
    "result_type": "nmr_result_type",
    "nmr_result_type": "nmr_result_type",
    "save_dir": "nmr_save_dir",
    "nmr_save_dir": "nmr_save_dir",
    "target": "target",
    "target_ppm": "target",
}

WORKFLOW_CONFIG_KEYS = {
    "sequence": "sequence",
    "event_sequence": "sequence",
    "events": "sequence",
    "order": "sequence",
    "volume": "volume",
    "volume_ml": "volume",
    "pump_extra_seconds": "pump_extra_seconds",
    "settle_before_nmr_seconds": "settle_before_nmr_seconds",
    "cycles": "cycles",
    "withdraw_volume": "withdraw_volume",
    "withdraw_volume_ml": "withdraw_volume",
    "infuse_volume": "infuse_volume",
    "infuse_volume_ml": "infuse_volume",
    "between_cycles_minutes": "between_cycles_minutes",
}


def default_arg_values(
    default_config: Path = DEFAULT_WORKFLOW_CONFIG,
    save_dir: Path = DEFAULT_SAVE_DIR,
) -> dict:
    return {
        "workflow_config": Path(default_config),
        "port": DEFAULT_PORT,
        "baud": DEFAULT_BAUD,
        "channel": DEFAULT_CHANNEL,
        "diameter": DEFAULT_DIAMETER_MM,
        "units": "mL/min",
        "rate": DEFAULT_RATE_ML_MIN,
        "volume": DEFAULT_VOLUME_ML,
        "sequence": ["W", "N", "I"],
        "pump_extra_seconds": 2.0,
        "settle_before_nmr_seconds": 0.0,
        "nmr_host": None,
        "nmr_port": None,
        "nmr_scheme": None,
        "nmr_timeout": None,
        "nmr_poll_seconds": None,
        "nmr_max_wait": None,
        "nmr_route": "iflow",
        "nmr_experiment": None,
        "nmr_scans": 2,
        "nmr_receiver_gain": 12.0,
        "nmr_auto_gain": None,
        "nmr_solvent": None,
        "nmr_spectral_center": None,
        "nmr_sweep_width": None,
        "nmr_result_name": None,
        "nmr_result_type": None,
        "nmr_save_dir": Path(save_dir),
        "target": None,
        "cycles": 1,
        "withdraw_volume": None,
        "infuse_volume": None,
        "between_cycles_minutes": 0.0,
    }


def load_workflow_defaults(config_path: Path, defaults: dict | None = None) -> dict:
    values = dict(defaults or default_arg_values())
    path = Path(config_path)
    if not path.exists():
        return values

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse workflow config JSON at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Workflow config at {path} must be a JSON object")

    allowed_top = {
        "pump",
        "nmr",
        "workflow",
        *PUMP_CONFIG_KEYS,
        *NMR_CONFIG_KEYS,
        *WORKFLOW_CONFIG_KEYS,
    }
    unknown = sorted(set(raw) - allowed_top)
    if unknown:
        raise ValueError(f"Unknown workflow config key(s) in {path}: {', '.join(unknown)}")

    flat_raw = {
        key: value
        for key, value in raw.items()
        if key not in {"pump", "nmr", "workflow"}
    }
    _apply_config_section(
        values,
        {key: value for key, value in flat_raw.items() if key in PUMP_CONFIG_KEYS},
        PUMP_CONFIG_KEYS,
        path,
        "top level",
    )
    _apply_config_section(
        values,
        {key: value for key, value in flat_raw.items() if key in NMR_CONFIG_KEYS},
        NMR_CONFIG_KEYS,
        path,
        "top level",
    )
    _apply_config_section(
        values,
        {key: value for key, value in flat_raw.items() if key in WORKFLOW_CONFIG_KEYS},
        WORKFLOW_CONFIG_KEYS,
        path,
        "top level",
    )
    _apply_config_section(values, raw.get("pump", {}), PUMP_CONFIG_KEYS, path, "pump")
    _apply_config_section(values, raw.get("nmr", {}), NMR_CONFIG_KEYS, path, "nmr")
    _apply_config_section(
        values,
        raw.get("workflow", {}),
        WORKFLOW_CONFIG_KEYS,
        path,
        "workflow",
    )
    return values


def _apply_config_section(
    values: dict,
    section: object,
    mapping: dict[str, str],
    path: Path,
    label: str,
) -> None:
    if not section:
        return
    if not isinstance(section, dict):
        raise ValueError(f"Workflow config {label} section in {path} must be an object")

    unknown = sorted(set(section) - set(mapping))
    if unknown:
        raise ValueError(
            f"Unknown workflow config key(s) in {path} [{label}]: {', '.join(unknown)}"
        )

    for key, value in section.items():
        dest = mapping[key]
        if dest == "nmr_save_dir":
            value = Path(value)
        values[dest] = value


def preparse_workflow_config(
    argv: list[str] | None = None,
    default_config: Path = DEFAULT_WORKFLOW_CONFIG,
) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--workflow-config", type=Path, default=default_config)
    args, _ = parser.parse_known_args(argv)
    return Path(args.workflow_config)


def build_parser(defaults: dict | None = None) -> argparse.ArgumentParser:
    defaults = defaults or default_arg_values()
    parser = argparse.ArgumentParser(
        description="Run a real first test: withdraw 5 mL, run NMR, infuse 5 mL."
    )
    parser.add_argument(
        "--workflow-config",
        type=Path,
        default=defaults["workflow_config"],
        help="workflow JSON config path",
    )
    parser.add_argument("--yes", action="store_true", help="skip real-run prompt")
    parser.add_argument("--dry-run", action="store_true", help="print the plan only")
    parser.add_argument("--mock-pump", action="store_true", help="use fake pump responses")

    parser.add_argument("--pump-config", help="Chemyx JSON config path")
    parser.add_argument("--port", default=defaults["port"])
    parser.add_argument("--baud", type=int, default=defaults["baud"])
    parser.add_argument("--channel", type=int, choices=[0, 1, 2], default=defaults["channel"])
    parser.add_argument("--diameter", type=float, default=defaults["diameter"])
    parser.add_argument("--units", default=defaults["units"])
    parser.add_argument("--rate", type=float, default=defaults["rate"])
    parser.add_argument("--volume", type=float, default=defaults["volume"])
    parser.add_argument(
        "--sequence",
        default=defaults["sequence"],
        help="event order using W=withdraw, N=NMR, I=infuse; examples: 'W N I' or 'W I W I N W I'",
    )
    parser.add_argument(
        "--withdraw-volume",
        type=float,
        default=defaults["withdraw_volume"],
        help="volume for each W event; defaults to --volume",
    )
    parser.add_argument(
        "--infuse-volume",
        type=float,
        default=defaults["infuse_volume"],
        help="volume for each I event; defaults to --volume",
    )
    parser.add_argument(
        "--pump-extra-seconds",
        type=float,
        default=defaults["pump_extra_seconds"],
        help="extra wait after calculated pump move time before cleanup stop",
    )
    parser.add_argument(
        "--settle-before-nmr-seconds",
        type=float,
        default=defaults["settle_before_nmr_seconds"],
        help="optional pause after withdraw before starting NMR",
    )

    parser.add_argument("--nmr-config", "--config", dest="nmr_config")
    parser.add_argument("--nmr-host", default=defaults["nmr_host"])
    parser.add_argument("--nmr-port", type=int, default=defaults["nmr_port"])
    parser.add_argument("--nmr-scheme", default=defaults["nmr_scheme"])
    parser.add_argument("--nmr-timeout", type=float, default=defaults["nmr_timeout"])
    parser.add_argument("--nmr-poll-seconds", type=float, default=defaults["nmr_poll_seconds"])
    parser.add_argument("--nmr-max-wait", type=float, default=defaults["nmr_max_wait"])
    parser.add_argument("--nmr-route", choices=["experiment", "iflow"], default=defaults["nmr_route"])
    parser.add_argument("--nmr-experiment", default=defaults["nmr_experiment"])
    parser.add_argument("--nmr-scans", type=int, default=defaults["nmr_scans"])
    parser.add_argument(
        "--nmr-receiver-gain",
        type=float,
        default=defaults["nmr_receiver_gain"],
    )
    parser.add_argument(
        "--nmr-auto-gain",
        dest="nmr_auto_gain",
        action="store_true",
        default=defaults["nmr_auto_gain"],
    )
    parser.add_argument(
        "--nmr-manual-gain",
        dest="nmr_auto_gain",
        action="store_false",
        default=defaults["nmr_auto_gain"],
    )
    parser.add_argument("--nmr-solvent", default=defaults["nmr_solvent"])
    parser.add_argument(
        "--nmr-spectral-center",
        type=float,
        default=defaults["nmr_spectral_center"],
    )
    parser.add_argument("--nmr-sweep-width", type=float, default=defaults["nmr_sweep_width"])
    parser.add_argument("--nmr-result-name", default=defaults["nmr_result_name"])
    parser.add_argument(
        "--nmr-result-type",
        choices=["fid", "spectrum", "rawfid"],
        default=defaults["nmr_result_type"],
    )
    parser.add_argument("--nmr-save-dir", type=Path, default=defaults["nmr_save_dir"])
    parser.add_argument("--target", type=float, default=defaults["target"])
    return parser


def parse_args(
    argv: list[str] | None = None,
    default_config: Path = DEFAULT_WORKFLOW_CONFIG,
    defaults: dict | None = None,
) -> argparse.Namespace:
    config_path = preparse_workflow_config(argv, default_config)
    values = load_workflow_defaults(config_path, defaults)
    values["workflow_config"] = config_path
    return build_parser(values).parse_args(argv)


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


def normalize_sequence(sequence) -> list[str]:
    if sequence in (None, ""):
        return ["W", "N", "I"]
    if isinstance(sequence, str):
        text = sequence.strip().upper().replace(",", " ")
        if any(char.isspace() for char in text):
            tokens = [token for token in text.split() if token]
        else:
            tokens = list(text)
    elif isinstance(sequence, list):
        tokens = [str(item).strip().upper() for item in sequence]
    else:
        raise ValueError("Workflow sequence must be a string or list")

    aliases = {
        "W": "W",
        "WITHDRAW": "W",
        "I": "I",
        "INFUSE": "I",
        "N": "N",
        "NMR": "N",
    }
    events = []
    for token in tokens:
        if token not in aliases:
            raise ValueError(
                f"Unknown workflow event {token!r}; use W, I, or N"
            )
        events.append(aliases[token])
    if not events:
        raise ValueError("Workflow sequence cannot be empty")
    return events


def format_sequence(events: list[str]) -> str:
    names = {"W": "withdraw", "I": "infuse", "N": "NMR"}
    return " -> ".join(f"{event}({names[event]})" for event in events)


def effective_move_volumes(args) -> tuple[float, float]:
    withdraw_volume = args.withdraw_volume
    if withdraw_volume is None:
        withdraw_volume = args.volume
    infuse_volume = args.infuse_volume
    if infuse_volume is None:
        infuse_volume = args.volume
    if withdraw_volume <= 0:
        raise ValueError("Withdraw volume must be positive")
    if infuse_volume <= 0:
        raise ValueError("Infuse volume must be positive")
    return float(withdraw_volume), float(infuse_volume)


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

    events = normalize_sequence(args.sequence)
    withdraw_volume, infuse_volume = effective_move_volumes(args)
    longest_move = max(
        move_seconds(withdraw_volume, pump_cfg.rate, pump_cfg.units),
        move_seconds(infuse_volume, pump_cfg.rate, pump_cfg.units),
    )
    print("This will physically move the Chemyx pump and run the NMR.")
    print(
        f"Pump: {pump_cfg.port} @ {pump_cfg.baud_rate}, channel {pump_cfg.channel}, "
        f"W={withdraw_volume} mL, I={infuse_volume} mL at "
        f"{pump_cfg.rate} {config.UNITS[pump_cfg.units]}."
    )
    print(
        f"Sequence: {format_sequence(events)}. "
        f"Longest pump move is about {format_seconds(longest_move)}."
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
    events = normalize_sequence(args.sequence)
    withdraw_volume, infuse_volume = effective_move_volumes(args)
    withdraw_seconds = move_seconds(withdraw_volume, pump_cfg.rate, pump_cfg.units)
    infuse_seconds = move_seconds(infuse_volume, pump_cfg.rate, pump_cfg.units)
    print("=" * 72)
    print("First real Chemyx + NMR test")
    print("=" * 72)
    print(f"Config file    : {args.workflow_config}")
    print(f"Pump port      : {pump_cfg.port} @ {pump_cfg.baud_rate}")
    print(f"Pump channel   : {pump_cfg.channel}")
    print(f"Syringe ID     : {pump_cfg.diameter} mm")
    print(f"Sequence       : {format_sequence(events)}")
    print(f"Pump W volume  : {withdraw_volume} mL")
    print(f"Pump I volume  : {infuse_volume} mL")
    print(f"Pump rate      : {pump_cfg.rate} {config.UNITS[pump_cfg.units]}")
    print(
        "Move estimate  : "
        f"W {format_seconds(withdraw_seconds)}, I {format_seconds(infuse_seconds)}"
    )
    print(f"NMR RPC        : {nmr_settings.scheme}://{nmr_settings.host}:{nmr_settings.port}")
    print(f"NMR settings   : route={nmr_settings.route}, scans={nmr_settings.scans}")
    print(
        f"NMR gain       : receiver_gain={nmr_settings.receiver_gain}, "
        f"auto_gain={nmr_settings.auto_gain}"
    )
    print(f"NMR save dir   : {nmr_settings.save_dir}")
    print("=" * 72)


def main() -> int:
    try:
        args = parse_args()
    except ValueError as exc:
        print(f"FAILED: {exc}")
        return 1
    try:
        pump_cfg = load_pump_settings(args)
        nmr_settings = load_nmr_settings(args)
        events = normalize_sequence(args.sequence)
        withdraw_volume, infuse_volume = effective_move_volumes(args)
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
            net_withdrawn = 0.0
            nmr_count = 0
            for index, event in enumerate(events, start=1):
                if event == "W":
                    print(f"\n[{index}] Withdraw sample")
                    run_metered_move(
                        pump,
                        pump_cfg,
                        "withdraw",
                        withdraw_volume,
                        extra_seconds=args.pump_extra_seconds,
                        mock=args.mock_pump,
                    )
                    net_withdrawn += withdraw_volume
                elif event == "I":
                    print(f"\n[{index}] Infuse sample")
                    run_metered_move(
                        pump,
                        pump_cfg,
                        "infuse",
                        infuse_volume,
                        extra_seconds=args.pump_extra_seconds,
                        mock=args.mock_pump,
                    )
                    net_withdrawn = max(0.0, net_withdrawn - infuse_volume)
                elif event == "N":
                    print(f"\n[{index}] NMR acquisition")
                    nmr_count += 1
                    try:
                        sleep_with_progress(
                            "settle before NMR",
                            args.settle_before_nmr_seconds,
                            mock=args.mock_pump,
                        )
                        run_nmr_acquisition(
                            nmr_settings,
                            nmr_settings.save_dir,
                            label=f"first_real_test_nmr{nmr_count:02d}",
                        )
                    except Exception:
                        if net_withdrawn > 0:
                            print(
                                "\nNMR step failed; attempting to infuse "
                                f"{net_withdrawn:.4g} mL back."
                            )
                            run_metered_move(
                                pump,
                                pump_cfg,
                                "infuse",
                                net_withdrawn,
                                extra_seconds=args.pump_extra_seconds,
                                mock=args.mock_pump,
                            )
                        raise
    except (PumpConnectionError, EchoMismatchError, ValueError, NmrRpcError) as exc:
        print(f"\nFAILED: {exc}")
        return 1

    print("\nSUCCESS: first real Chemyx + NMR test completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
