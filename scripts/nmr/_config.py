"""Config-file support for the standalone NMR analysis scripts.

Each script reads its parameters from a YAML file (default
``configs/nmr/analysis.yaml``) so settings can be adjusted between runs by
editing one file instead of passing long command-line flags.

The file has a shared ``common`` section plus one section per script
(``analyze_single``, ``compare_timeseries``, ``batch_report``).  A script
reads ``common`` merged with its own section.

Precedence (lowest to highest)::

    built-in default  <  config file  <  command-line flag

so you can drive everything from the YAML::

    python scripts/nmr/compare_timeseries.py results/raw/nmr/my-run/

and still override a single value for a one-off run::

    python scripts/nmr/compare_timeseries.py results/raw/nmr/my-run/ --target 5.8

The YAML is loaded with the repository's own ``read_mapping_config`` helper so
error handling and PyYAML usage stay consistent with the rest of the project.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

import _bootstrap  # noqa: F401  — puts repo root on sys.path

from chemyx_lab.config import REPO_ROOT, ConfigError, read_mapping_config

from _common import (
    DEFAULT_OUTPUT_DIR,
    DEFAULT_PLATEAU_CONSECUTIVE,
    DEFAULT_PLATEAU_THRESHOLD,
    DEFAULT_TARGET_PPM,
    DEFAULT_WINDOW_PPM,
)

# Default plot half-window (ppm) for analyze_single's review plot.
DEFAULT_PLOT_WINDOW_PPM = 0.5

# Defaults for plot_spectra.py (full-spectrum visualisation).
DEFAULT_ZOOM_WINDOW_PPM = 0.5
DEFAULT_NORMALIZE_OVERLAY = True
# Second, wider per-file zoom window (absolute ppm range).
DEFAULT_ZOOM_WIDE_MIN_PPM = 5.0
DEFAULT_ZOOM_WIDE_MAX_PPM = 7.0

# Shared config plus an optional ignored per-machine override. ``process_fid``
# merges the local file automatically when ``--config`` is not given.
DEFAULT_CONFIG_PATH = REPO_ROOT / "configs" / "nmr" / "analysis.yaml"
LOCAL_CONFIG_PATH = REPO_ROOT / "configs" / "nmr" / "analysis.local.yaml"

# Which config keys are valid in the shared section and each script section.
_COMMON_KEYS = {
    "target_ppm", "window_ppm", "output_dir", "run_name", "plots",
    "group", "group_tokens", "line_broadening_hz", "zero_fill_points",
    "min_prominence_snr", "baseline_window_ppm",
    "baseline_polynomial_order",
}
_SCRIPT_KEYS: dict[str, set[str]] = {
    "analyze_single": {"plot_window_ppm"},
    "compare_timeseries": {"plateau_threshold_pct", "plateau_consecutive"},
    "batch_report": set(),
    "plot_spectra": {
        "zoom_window_ppm", "normalize_overlay",
        "zoom_wide_min_ppm", "zoom_wide_max_ppm",
    },
}

# Keys that must be coerced to a Path when read from YAML.
_PATH_KEYS = {"output_dir"}


@dataclass
class AnalysisConfig:
    """Resolved analysis parameters shared by the three scripts.

    Every field carries the built-in default; ``load_config`` overwrites the
    ones present in the YAML, and the scripts overwrite those again with any
    explicit CLI flags via :func:`apply_cli_overrides`.
    """

    # common
    target_ppm: float = DEFAULT_TARGET_PPM
    window_ppm: float = DEFAULT_WINDOW_PPM
    output_dir: Path = DEFAULT_OUTPUT_DIR
    run_name: str | None = None
    plots: bool = True
    # Group runs into a sub-folder of output_dir (e.g. by sample type). ``group``
    # forces a fixed name; otherwise the first token in ``group_tokens`` found in
    # the input file names is used. No match / both empty → no grouping.
    group: str | None = None
    group_tokens: list[str] = field(default_factory=list)
    # Processing defaults reproduce each DX file's metadata when null.
    line_broadening_hz: float | None = None
    zero_fill_points: int | None = None
    # Reject noise wiggles and model the sloped local solvent background.
    min_prominence_snr: float = 3.0
    baseline_window_ppm: float = 0.5
    baseline_polynomial_order: int = 2
    # analyze_single
    plot_window_ppm: float = DEFAULT_PLOT_WINDOW_PPM
    # compare_timeseries
    plateau_threshold_pct: float = DEFAULT_PLATEAU_THRESHOLD
    plateau_consecutive: int = DEFAULT_PLATEAU_CONSECUTIVE
    # plot_spectra
    zoom_window_ppm: float = DEFAULT_ZOOM_WINDOW_PPM
    normalize_overlay: bool = DEFAULT_NORMALIZE_OVERLAY
    zoom_wide_min_ppm: float = DEFAULT_ZOOM_WIDE_MIN_PPM
    zoom_wide_max_ppm: float = DEFAULT_ZOOM_WIDE_MAX_PPM


def _apply_section(
    cfg: AnalysisConfig,
    section: Any,
    allowed: set[str],
    path: Path,
    label: str,
) -> None:
    """Validate one config section and copy its values onto *cfg*."""
    if section is None:
        return
    if not isinstance(section, dict):
        raise ConfigError(
            f"Section '{label}' in {path} must be a mapping, got {type(section).__name__}"
        )
    unknown = sorted(set(section) - allowed)
    if unknown:
        allowed_str = ", ".join(sorted(allowed)) or "(none)"
        raise ConfigError(
            f"Unknown key(s) in [{label}] of {path}: {', '.join(unknown)}. "
            f"Allowed keys: {allowed_str}"
        )
    for key, value in section.items():
        if key in _PATH_KEYS and value is not None:
            value = Path(value)
        elif key == "group_tokens" and value is not None:
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, (list, tuple)):
                raise ConfigError(
                    f"'group_tokens' in {path} must be a list of strings"
                )
            value = [str(v) for v in value]
        setattr(cfg, key, value)


def load_config(
    script: str,
    config_path: str | Path | None = None,
) -> AnalysisConfig:
    """Build an :class:`AnalysisConfig` for *script* from the YAML file.

    *script* must be one of ``analyze_single``, ``compare_timeseries``,
    ``batch_report``.

    If *config_path* is ``None`` the default (``configs/nmr/analysis.yaml``) is
    used, and a missing default file is not an error — built-in defaults apply.
    An explicitly supplied path that does not exist raises ``ConfigError``.
    """
    if script not in _SCRIPT_KEYS:
        raise ValueError(
            f"unknown script '{script}'; expected one of {sorted(_SCRIPT_KEYS)}"
        )

    cfg = AnalysisConfig()

    explicit = config_path is not None
    path = Path(config_path) if explicit else DEFAULT_CONFIG_PATH
    if not path.exists():
        if explicit:
            raise ConfigError(f"NMR analysis config not found: {path}")
        return cfg  # default file absent → use built-in defaults

    raw = read_mapping_config(path, "NMR analysis config")

    # ``processing``, ``regional_analysis``, and ``reference`` configure the
    # newer full-FID regional processor. The fixed-target scripts intentionally
    # ignore them while sharing this repository-level YAML file.
    allowed_sections = {
        "common",
        "processing",
        "regional_analysis",
        "reference",
        "reference_models",
        # Plot-only settings consumed by process_fid.py. Fixed-target scripts
        # share this YAML file and intentionally ignore this section.
        "plots",
        # Consumed only by process_fid.py's regional processor; the fixed-target
        # scripts share this file and must tolerate (ignore) the section.
        "statistics",
        "input",
        "output",
        "peak_qc",
        "simple_table",
    } | set(_SCRIPT_KEYS)
    unknown_sections = sorted(set(raw) - allowed_sections)
    if unknown_sections:
        raise ConfigError(
            f"Unknown section(s) in {path}: {', '.join(unknown_sections)}. "
            f"Allowed sections: {', '.join(sorted(allowed_sections))}"
        )

    _apply_section(cfg, raw.get("common"), _COMMON_KEYS, path, "common")
    _apply_section(cfg, raw.get(script), _SCRIPT_KEYS[script], path, script)
    return cfg


def apply_cli_overrides(cfg: AnalysisConfig, args: argparse.Namespace) -> AnalysisConfig:
    """Overwrite *cfg* fields with any CLI flags the user actually passed.

    The scripts register their flags with ``default=None`` and a ``dest`` that
    matches an :class:`AnalysisConfig` field, so a non-``None`` attribute means
    the user supplied it and it should win over the config file.
    """
    for f in fields(cfg):
        value = getattr(args, f.name, None)
        if value is not None:
            setattr(cfg, f.name, value)
    return cfg


def peak_analysis_kwargs(cfg: AnalysisConfig) -> dict[str, Any]:
    """Build the shared keyword arguments for core target-peak analysis."""
    return {
        "line_broadening_hz": cfg.line_broadening_hz,
        "zero_fill_points": cfg.zero_fill_points,
        "min_prominence_snr": cfg.min_prominence_snr,
        "baseline_window_ppm": cfg.baseline_window_ppm,
        "baseline_polynomial_order": cfg.baseline_polynomial_order,
    }


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--config`` flag to *parser*."""
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "YAML config file with analysis parameters "
            f"(default: {DEFAULT_CONFIG_PATH.relative_to(REPO_ROOT)})"
        ),
    )
