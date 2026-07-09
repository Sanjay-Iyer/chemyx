"""Portable configuration for Chemyx/NMR workflow scripts.

Committed defaults are safe starting points from known working setups. Real
hardware settings should come from environment variables, untracked
``configs/chemyx.local.json`` or ``configs/nmr.local.json`` files, or a legacy
untracked ``config_local.py`` file.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def _env(name: str, default, cast=str):
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw.strip())
    except (TypeError, ValueError):
        return default


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    key = str(value).strip().lower()
    if key in {"1", "true", "yes", "y", "on"}:
        return True
    if key in {"0", "false", "no", "n", "off"}:
        return False
    raise ValueError(f"Cannot parse {value!r} as a boolean")


# Serial port: leave unset in committed code. Set CHEMYX_PORT,
# configs/chemyx.local.json, or config_local.py.
PORT = _env("CHEMYX_PORT", "")

# Your successful work-laptop 4000X script used 115200. Still verify this
# against the pump screen, because baud rate is an instrument setting.
BAUD_RATE = _env("CHEMYX_BAUD", 115200, int)

# 0 = no prefix/default, 1 = channel 1, 2 = channel 2.
CHANNEL = _env("CHEMYX_CHANNEL", 0, int)

# Syringe/flow defaults are safe-ish demo values. Override per rig.
DIAMETER = _env("CHEMYX_DIAMETER", 4.5, float)
DEFAULT_RATE = _env("CHEMYX_RATE", 1.0, float)
DEFAULT_VOLUME = _env("CHEMYX_VOLUME", 0.5, float)
FIRST_LIGHT_VOLUME = _env("CHEMYX_FIRST_LIGHT_VOLUME", 0.05, float)
FIRST_LIGHT_RATE = _env("CHEMYX_FIRST_LIGHT_RATE", 0.5, float)

# The proven deploy script waited briefly after each write before reading.
RESPONSE_DELAY = _env("CHEMYX_RESPONSE_DELAY", 0.2, float)

UNITS = {
    0: "mL/min",
    1: "mL/hr",
    2: "uL/min",
    3: "uL/hr",
}

UNITS_BY_NAME = {
    "ml/min": 0,
    "mlmin": 0,
    "ml/hr": 1,
    "ml/h": 1,
    "mlhr": 1,
    "ul/min": 2,
    "ulmin": 2,
    "ul/hr": 3,
    "ul/h": 3,
    "ulhr": 3,
}

DEFAULT_UNITS = _env("CHEMYX_UNITS", 0, int)

DIAMETER_MIN = 0.103
DIAMETER_MAX = 40.000

RATE_LIMITS = {
    0: (1e-7, 170.5),
    1: (6e-6, 10230.0),
    2: (0.0001, 170500.0),
    3: (0.006, 10230000.0),
}

SAFE_MAX_RATE = _env("CHEMYX_SAFE_MAX_RATE", 50.0, float)
SAFE_MAX_VOLUME = _env("CHEMYX_SAFE_MAX_VOLUME", 5.0, float)

BYTESIZE = 8
PARITY = "N"
STOPBITS = 1
TIMEOUT = _env("CHEMYX_TIMEOUT", 2.0, float)
COMMAND_TERMINATOR = "\r"
CHEMYX_CONFIG_FILE = _env(
    "CHEMYX_CONFIG_FILE", str(REPO_ROOT / "configs" / "chemyx.local.json")
)

# IDEX/Rheodyne MX Series II valve (USB-B -> FTDI FT232R virtual COM port).
# The physical valve is an MXX777-601: 6 fluidic ports but only TWO selectable
# positions (1 and 2). "positions" here means selectable positions, not
# plumbing ports. Leave the port unset in committed code: set MXVALVE_PORT,
# configs/valve.local.json, or pass --port to the scripts.
VALVE_PORT = _env("MXVALVE_PORT", "")

# Manufacturer default for MX II modules is 19200 (8N1, no handshaking).
VALVE_BAUD = _env("MXVALVE_BAUD", 19200, int)

# Number of selectable positions. The MX II board silently ignores position
# commands beyond what the attached valve supports, so this default keeps
# every committed code path honest for the 2-position MXX777-601.
VALVE_POSITIONS = _env("MXVALVE_POSITIONS", 2, int)

VALVE_TIMEOUT = _env("MXVALVE_TIMEOUT", 1.0, float)
VALVE_MOTION_TIMEOUT = _env("MXVALVE_MOTION_TIMEOUT", 10.0, float)
VALVE_CONFIG_FILE = _env(
    "MXVALVE_CONFIG_FILE", str(REPO_ROOT / "configs" / "valve.local.json")
)

# NMR defaults. The archived working NMR scripts used direct ethernet at
# 169.254.30.54 and the Nanalysis/NMReady RPC server on port 5000.
NMR_RPC_SCHEME = _env("NMR_RPC_SCHEME", "http")
NMR_HOST = _env("NMR_HOST", "169.254.30.54")
NMR_PORT = _env("NMR_PORT", 5000, int)
NMR_RPC_TIMEOUT = _env("NMR_RPC_TIMEOUT", 10.0, float)
NMR_RPC_POLL_SECONDS = _env("NMR_RPC_POLL_SECONDS", 2.0, float)
NMR_RPC_MAX_WAIT_SECONDS = _env("NMR_RPC_MAX_WAIT_SECONDS", 300.0, float)
NMR_DEFAULT_ROUTE = _env("NMR_ROUTE", "iflow")
NMR_DEFAULT_EXPERIMENT = _env("NMR_DEFAULT_EXPERIMENT", "1D")
NMR_DEFAULT_SCANS = _env("NMR_DEFAULT_SCANS", 2, int)
NMR_DEFAULT_RECEIVER_GAIN = _env("NMR_RECEIVER_GAIN", 12.0, float)
NMR_DEFAULT_AUTO_GAIN = _env("NMR_AUTO_GAIN", False, _to_bool)
NMR_DEFAULT_SOLVENT = _env("NMR_DEFAULT_SOLVENT", "Toluene")
NMR_DEFAULT_SPECTRAL_CENTER = _env("NMR_DEFAULT_SPECTRAL_CENTER", 5.0, float)
NMR_DEFAULT_SWEEP_WIDTH = _env("NMR_DEFAULT_SWEEP_WIDTH", 20.0, float)
NMR_DEFAULT_RESULT_NAME = _env("NMR_RESULT_NAME", "DataSet")
NMR_DEFAULT_RESULT_TYPE = _env("NMR_RESULT_TYPE", "fid")
NMR_DEFAULT_SAVE_DIR = _env("NMR_SAVE_DIR", "runs/nmr")
NMR_CONFIG_FILE = _env("NMR_CONFIG_FILE", str(REPO_ROOT / "configs" / "nmr.local.json"))
NMR_TARGET_PPM = _env("NMR_TARGET_PPM", 6.1, float)
NMR_PEAK_WINDOW_PPM = _env("NMR_PEAK_WINDOW_PPM", 0.12, float)


def resolve_units(units) -> int:
    """Accept a units code or human string and return the Chemyx units code."""
    if isinstance(units, int):
        if units not in UNITS:
            raise ValueError(f"Unknown units code {units}; valid codes: {list(UNITS)}")
        return units
    key = str(units).strip().lower()
    if key in UNITS_BY_NAME:
        return UNITS_BY_NAME[key]
    raise ValueError(f"Unknown units {units!r}; try one of {sorted(UNITS.values())}")


def rate_limits(units) -> tuple[float, float]:
    """Return the valid flow-rate range for a units code/name."""
    return RATE_LIMITS[resolve_units(units)]


try:
    from config_local import *  # noqa: F401,F403
except ImportError:
    pass


@dataclass(frozen=True)
class PumpConfig:
    port: str
    baud_rate: int
    channel: int
    units: int
    diameter: float
    rate: float
    volume: float
    timeout: float
    response_delay: float


_PUMP_CONFIG_ALIASES = {
    "serial_port": "port",
    "com_port": "port",
    "pump_port": "port",
    "chemyx_port": "port",
    "baud": "baud_rate",
    "baudrate": "baud_rate",
    "baudRate": "baud_rate",
    "pump_baud": "baud_rate",
    "chemyx_baud": "baud_rate",
    "pump_channel": "channel",
    "chemyx_channel": "channel",
    "default_units": "units",
    "flow_units": "units",
    "units_code": "units",
    "syringe_diameter": "diameter",
    "syringe_diameter_mm": "diameter",
    "diameter_mm": "diameter",
    "default_rate": "rate",
    "flow_rate": "rate",
    "rate_ml_min": "rate",
    "default_volume": "volume",
    "test_volume": "volume",
    "volume_ml": "volume",
    "serial_timeout": "timeout",
    "read_timeout": "timeout",
    "command_timeout": "timeout",
    "read_delay": "response_delay",
    "command_delay": "response_delay",
}


def load_pump_config(
    config_path: str | os.PathLike | None = None, **overrides
) -> PumpConfig:
    """Load pump defaults, optional local JSON config, then CLI overrides."""
    values = {
        "port": PORT,
        "baud_rate": BAUD_RATE,
        "channel": CHANNEL,
        "units": DEFAULT_UNITS,
        "diameter": DIAMETER,
        "rate": DEFAULT_RATE,
        "volume": DEFAULT_VOLUME,
        "timeout": TIMEOUT,
        "response_delay": RESPONSE_DELAY,
    }

    path = Path(config_path) if config_path else Path(CHEMYX_CONFIG_FILE)
    if path.exists():
        values.update(_read_pump_json_config(path, set(values)))

    for key, value in overrides.items():
        if value is not None:
            values[key] = value
    return _coerce_pump_config(values)


def _read_pump_json_config(path: Path, known_keys: set[str]) -> dict:
    return _read_local_json_config(
        path=path,
        known_keys=known_keys,
        aliases=_PUMP_CONFIG_ALIASES,
        label="Chemyx config",
    )


def _coerce_pump_config(values: dict) -> PumpConfig:
    channel = int(values["channel"])
    if channel not in (0, 1, 2):
        raise ValueError("Chemyx channel must be 0 (default), 1, or 2")

    return PumpConfig(
        port=str(values["port"]).strip(),
        baud_rate=int(values["baud_rate"]),
        channel=channel,
        units=resolve_units(values["units"]),
        diameter=float(values["diameter"]),
        rate=float(values["rate"]),
        volume=float(values["volume"]),
        timeout=float(values["timeout"]),
        response_delay=float(values["response_delay"]),
    )


@dataclass(frozen=True)
class ValveConfig:
    port: str
    baud: int
    positions: int
    timeout: float
    motion_timeout: float


_VALVE_CONFIG_ALIASES = {
    "serial_port": "port",
    "com_port": "port",
    "valve_port": "port",
    "mxvalve_port": "port",
    "baud_rate": "baud",
    "baudrate": "baud",
    "valve_baud": "baud",
    # linnarsson-lab MXII_valve calls selectable positions "ports"
    "ports": "positions",
    "n_positions": "positions",
    "num_positions": "positions",
    "valve_positions": "positions",
    "serial_timeout": "timeout",
    "read_timeout": "timeout",
    "move_timeout": "motion_timeout",
}

VALVE_SUPPORTED_BAUDS = (9600, 19200, 38400, 57600)


def load_valve_config(
    config_path: str | os.PathLike | None = None, **overrides
) -> ValveConfig:
    """Load MX valve defaults, optional local JSON config, then CLI overrides."""
    values = {
        "port": VALVE_PORT,
        "baud": VALVE_BAUD,
        "positions": VALVE_POSITIONS,
        "timeout": VALVE_TIMEOUT,
        "motion_timeout": VALVE_MOTION_TIMEOUT,
    }

    path = Path(config_path) if config_path else Path(VALVE_CONFIG_FILE)
    if path.exists():
        values.update(
            _read_local_json_config(
                path=path,
                known_keys=set(values),
                aliases=_VALVE_CONFIG_ALIASES,
                label="valve config",
            )
        )

    for key, value in overrides.items():
        if value is not None:
            values[key] = value
    return _coerce_valve_config(values)


def _coerce_valve_config(values: dict) -> ValveConfig:
    positions = int(values["positions"])
    if not 1 <= positions <= 12:
        raise ValueError("Valve positions must be between 1 and 12")

    baud = int(values["baud"])
    if baud not in VALVE_SUPPORTED_BAUDS:
        raise ValueError(
            f"Valve baud {baud} is not one of the MX II supported rates "
            f"{VALVE_SUPPORTED_BAUDS}"
        )

    return ValveConfig(
        port=str(values["port"]).strip(),
        baud=baud,
        positions=positions,
        timeout=float(values["timeout"]),
        motion_timeout=float(values["motion_timeout"]),
    )


@dataclass(frozen=True)
class NmrSettings:
    host: str
    port: int
    scheme: str
    timeout: float
    poll_seconds: float
    max_wait_seconds: float
    route: str
    experiment: str
    scans: int
    receiver_gain: float | None
    auto_gain: bool
    solvent: str
    spectral_center: float
    sweep_width: float
    result_name: str
    result_type: str
    save_dir: Path
    target_ppm: float
    peak_window_ppm: float


_NMR_CONFIG_ALIASES = {
    "ip": "host",
    "ip_address": "host",
    "nmr_ip": "host",
    "nmr_host": "host",
    "rpc_host": "host",
    "nmr_port": "port",
    "rpc_port": "port",
    "rpc_scheme": "scheme",
    "rpc_timeout": "timeout",
    "nmr_timeout": "timeout",
    "nmr_route": "route",
    "nmr_experiment": "experiment",
    "nmr_scans": "scans",
    "number_of_scans": "scans",
    "num_scans": "scans",
    "numscans": "scans",
    "NumberOfScans": "scans",
    "nmr_receiver_gain": "receiver_gain",
    "receiverGain": "receiver_gain",
    "ReceiverGain": "receiver_gain",
    "nmr_auto_gain": "auto_gain",
    "autoGain": "auto_gain",
    "AutoGain": "auto_gain",
    "nmr_solvent": "solvent",
    "nmr_spectral_center": "spectral_center",
    "spectralCenter": "spectral_center",
    "nmr_sweep_width": "sweep_width",
    "sweepWidth": "sweep_width",
    "nmr_save_dir": "save_dir",
    "nmr_result_name": "result_name",
    "nmr_result_type": "result_type",
    "target": "target_ppm",
    "nmr_target_ppm": "target_ppm",
}


def load_nmr_settings(config_path: str | os.PathLike | None = None, **overrides) -> NmrSettings:
    """Load NMR defaults, optional local JSON config, then CLI overrides.

    The default JSON path is ``configs/nmr.local.json``. Missing local config is
    intentional and harmless, so a fresh clone still works from committed
    defaults.
    """
    values = {
        "host": NMR_HOST,
        "port": NMR_PORT,
        "scheme": NMR_RPC_SCHEME,
        "timeout": NMR_RPC_TIMEOUT,
        "poll_seconds": NMR_RPC_POLL_SECONDS,
        "max_wait_seconds": NMR_RPC_MAX_WAIT_SECONDS,
        "route": NMR_DEFAULT_ROUTE,
        "experiment": NMR_DEFAULT_EXPERIMENT,
        "scans": NMR_DEFAULT_SCANS,
        "receiver_gain": NMR_DEFAULT_RECEIVER_GAIN,
        "auto_gain": NMR_DEFAULT_AUTO_GAIN,
        "solvent": NMR_DEFAULT_SOLVENT,
        "spectral_center": NMR_DEFAULT_SPECTRAL_CENTER,
        "sweep_width": NMR_DEFAULT_SWEEP_WIDTH,
        "result_name": NMR_DEFAULT_RESULT_NAME,
        "result_type": NMR_DEFAULT_RESULT_TYPE,
        "save_dir": Path(NMR_DEFAULT_SAVE_DIR),
        "target_ppm": NMR_TARGET_PPM,
        "peak_window_ppm": NMR_PEAK_WINDOW_PPM,
    }

    path = Path(config_path) if config_path else Path(NMR_CONFIG_FILE)
    if path.exists():
        values.update(_read_nmr_json_config(path, set(values)))

    for key, value in overrides.items():
        if value is not None:
            values[key] = value

    return _coerce_nmr_settings(values)


def _read_nmr_json_config(path: Path, known_keys: set[str]) -> dict:
    return _read_local_json_config(
        path=path,
        known_keys=known_keys,
        aliases=_NMR_CONFIG_ALIASES,
        label="NMR config",
    )


def _read_local_json_config(
    path: Path,
    known_keys: set[str],
    aliases: dict[str, str],
    label: str,
) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not parse {label} JSON at {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} at {path} must be a JSON object")

    normalized = {}
    for key, value in raw.items():
        normalized_key = aliases.get(key, key)
        if normalized_key not in known_keys:
            valid = ", ".join(sorted(known_keys | set(aliases)))
            raise ValueError(
                f"Unknown {label} key {key!r} in {path}. Valid keys: {valid}"
            )
        normalized[normalized_key] = value
    return normalized


def _coerce_nmr_settings(values: dict) -> NmrSettings:
    route = str(values["route"]).strip().lower()
    if route not in {"experiment", "iflow"}:
        raise ValueError("NMR route must be 'experiment' or 'iflow'")

    result_type = str(values["result_type"]).strip().lower()
    if result_type not in {"fid", "spectrum", "rawfid"}:
        raise ValueError("NMR result_type must be 'fid', 'spectrum', or 'rawfid'")

    receiver_gain = values.get("receiver_gain")
    if receiver_gain in ("", None):
        receiver_gain = None
    else:
        receiver_gain = float(receiver_gain)

    return NmrSettings(
        host=str(values["host"]).strip(),
        port=int(values["port"]),
        scheme=str(values["scheme"]).strip(),
        timeout=float(values["timeout"]),
        poll_seconds=float(values["poll_seconds"]),
        max_wait_seconds=float(values["max_wait_seconds"]),
        route=route,
        experiment=str(values["experiment"]).strip(),
        scans=int(values["scans"]),
        receiver_gain=receiver_gain,
        auto_gain=_to_bool(values["auto_gain"]),
        solvent=str(values["solvent"]).strip(),
        spectral_center=float(values["spectral_center"]),
        sweep_width=float(values["sweep_width"]),
        result_name=str(values["result_name"]).strip(),
        result_type=result_type,
        save_dir=Path(values["save_dir"]),
        target_ppm=float(values["target_ppm"]),
        peak_window_ppm=float(values["peak_window_ppm"]),
    )
