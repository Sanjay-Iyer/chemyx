"""Typed configuration for one-peak NMR kinetics and completion reporting."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class TargetPeakConfigError(ValueError):
    """Raised when the ``target_peak`` YAML section is invalid."""


@dataclass(frozen=True)
class CompletionConfig:
    """Decision thresholds for growth and disappearance completion."""

    recent_window: int = 4
    minimum_observations: int = 6
    minimum_elapsed_hours: float = 1.0
    absolute_slope_threshold_per_hour: float = 5.0
    relative_slope_threshold_percent_per_hour: float = 10.0
    percent_change_threshold: float = 5.0
    consecutive_stable_measurements: int = 3
    low_area_absolute: float | None = None
    low_area_fraction_of_max: float = 0.10
    meaningful_trend_fraction: float = 0.25
    reversal_consecutive_measurements: int = 2
    percent_denominator_floor_fraction: float = 0.05
    confidence_level: float = 0.95


@dataclass(frozen=True)
class FigureConfig:
    """Reproducible figure sizes and export settings."""

    formats: tuple[str, ...] = ("png", "svg", "pdf")
    dpi: int = 300
    slide_size_inches: tuple[float, float] = (10.0, 5.625)
    paper_single_column_inches: tuple[float, float] = (3.5, 3.0)
    paper_double_column_inches: tuple[float, float] = (7.2, 5.2)
    show_uncertainty: bool = True
    show_trend: bool = True


@dataclass(frozen=True)
class StageConfig:
    """Optional explicit stage annotation; boundaries are never inferred."""

    label: str
    start_hours: float
    end_hours: float
    expected_direction: str = "unresolved"


@dataclass(frozen=True)
class TargetPeakConfig:
    """All settings for the focused one-peak workflow."""

    enabled: bool = False
    dataset_display_name: str | None = None
    peak_label: str = "target peak"
    expected_center_ppm: float = 5.7
    search_window_ppm: tuple[float, float] = (5.6, 5.8)
    integration_window_ppm: tuple[float, float] = (5.6, 5.8)
    plot_window_ppm: tuple[float, float] = (5.6, 5.8)
    tracking_max_drift_ppm: float = 0.08
    integration_edge_margin_ppm: float = 0.01
    minimum_snr: float = 8.0
    minimum_prominence_snr: float = 5.0
    rolling_window: int = 4
    normalization_modes: tuple[str, ...] = (
        "fraction_of_max",
        "relative_to_first",
        "zero_to_one",
    )
    completion: CompletionConfig = field(default_factory=CompletionConfig)
    figures: FigureConfig = field(default_factory=FigureConfig)
    stages: tuple[StageConfig, ...] = ()


_NORMALIZATIONS = {"fraction_of_max", "relative_to_first", "zero_to_one"}
_FORMATS = {"png", "svg", "pdf"}
_DIRECTIONS = {"growth", "disappearance", "unresolved"}


def _mapping(value: Any, label: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise TargetPeakConfigError(f"{label} must be a mapping")
    return value


def _keys(section: dict, allowed: set[str], label: str) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise TargetPeakConfigError(
            f"Unknown key(s) in {label}: {', '.join(unknown)}"
        )


def _window(value: Any, label: str, default: tuple[float, float]) -> tuple[float, float]:
    value = default if value is None else value
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise TargetPeakConfigError(f"{label} must contain exactly two ppm values")
    lo, hi = sorted((float(value[0]), float(value[1])))
    if lo == hi:
        raise TargetPeakConfigError(f"{label} bounds must differ")
    return lo, hi


def _size(value: Any, label: str, default: tuple[float, float]) -> tuple[float, float]:
    value = default if value is None else value
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise TargetPeakConfigError(f"{label} must contain width and height")
    width, height = float(value[0]), float(value[1])
    if width <= 0 or height <= 0:
        raise TargetPeakConfigError(f"{label} dimensions must be positive")
    return width, height


def _positive(value: Any, label: str, *, allow_zero: bool = False) -> float:
    result = float(value)
    if result < 0 or (result == 0 and not allow_zero):
        raise TargetPeakConfigError(f"{label} must be {'non-negative' if allow_zero else 'positive'}")
    return result


def _positive_int(value: Any, label: str, minimum: int = 1) -> int:
    result = int(value)
    if result < minimum:
        raise TargetPeakConfigError(f"{label} must be >= {minimum}")
    return result


def load_target_peak_config(raw: dict | None) -> TargetPeakConfig:
    """Validate and load the top-level ``target_peak`` YAML mapping."""

    section = _mapping(raw, "target_peak")
    _keys(
        section,
        {
            "enabled", "dataset_display_name", "peak_label", "expected_center_ppm",
            "search_window_ppm", "integration_window_ppm", "plot_window_ppm",
            "tracking_max_drift_ppm", "integration_edge_margin_ppm", "minimum_snr",
            "minimum_prominence_snr", "rolling_window", "normalization_modes",
            "completion", "figures", "stages",
        },
        "target_peak",
    )

    expected = float(section.get("expected_center_ppm", 5.7))
    search = _window(section.get("search_window_ppm"), "target_peak.search_window_ppm", (expected - 0.1, expected + 0.1))
    integration = _window(section.get("integration_window_ppm"), "target_peak.integration_window_ppm", search)
    plot_window = _window(section.get("plot_window_ppm"), "target_peak.plot_window_ppm", search)
    if not (search[0] <= expected <= search[1]):
        raise TargetPeakConfigError("expected_center_ppm must lie inside search_window_ppm")

    completion_raw = _mapping(section.get("completion"), "target_peak.completion")
    _keys(
        completion_raw,
        {
            "recent_window", "minimum_observations", "minimum_elapsed_hours",
            "absolute_slope_threshold_per_hour",
            "relative_slope_threshold_percent_per_hour", "percent_change_threshold",
            "consecutive_stable_measurements", "low_area_absolute",
            "low_area_fraction_of_max", "meaningful_trend_fraction",
            "reversal_consecutive_measurements", "percent_denominator_floor_fraction",
            "confidence_level",
        },
        "target_peak.completion",
    )
    cc = CompletionConfig(
        recent_window=_positive_int(completion_raw.get("recent_window", 4), "recent_window", 3),
        minimum_observations=_positive_int(completion_raw.get("minimum_observations", 6), "minimum_observations", 3),
        minimum_elapsed_hours=_positive(completion_raw.get("minimum_elapsed_hours", 1.0), "minimum_elapsed_hours", allow_zero=True),
        absolute_slope_threshold_per_hour=_positive(completion_raw.get("absolute_slope_threshold_per_hour", 5.0), "absolute_slope_threshold_per_hour", allow_zero=True),
        relative_slope_threshold_percent_per_hour=_positive(completion_raw.get("relative_slope_threshold_percent_per_hour", 10.0), "relative_slope_threshold_percent_per_hour", allow_zero=True),
        percent_change_threshold=_positive(completion_raw.get("percent_change_threshold", 5.0), "percent_change_threshold", allow_zero=True),
        consecutive_stable_measurements=_positive_int(completion_raw.get("consecutive_stable_measurements", 3), "consecutive_stable_measurements"),
        low_area_absolute=(None if completion_raw.get("low_area_absolute") is None else _positive(completion_raw["low_area_absolute"], "low_area_absolute", allow_zero=True)),
        low_area_fraction_of_max=_positive(completion_raw.get("low_area_fraction_of_max", 0.10), "low_area_fraction_of_max", allow_zero=True),
        meaningful_trend_fraction=_positive(completion_raw.get("meaningful_trend_fraction", 0.25), "meaningful_trend_fraction", allow_zero=True),
        reversal_consecutive_measurements=_positive_int(completion_raw.get("reversal_consecutive_measurements", 2), "reversal_consecutive_measurements"),
        percent_denominator_floor_fraction=_positive(completion_raw.get("percent_denominator_floor_fraction", 0.05), "percent_denominator_floor_fraction", allow_zero=True),
        confidence_level=float(completion_raw.get("confidence_level", 0.95)),
    )
    if not 0 < cc.confidence_level < 1:
        raise TargetPeakConfigError("confidence_level must be between 0 and 1")

    fig_raw = _mapping(section.get("figures"), "target_peak.figures")
    _keys(fig_raw, {"formats", "dpi", "slide_size_inches", "paper_single_column_inches", "paper_double_column_inches", "show_uncertainty", "show_trend"}, "target_peak.figures")
    formats = tuple(str(v).lower() for v in fig_raw.get("formats", ("png", "svg", "pdf")))
    if not formats or set(formats) - _FORMATS:
        raise TargetPeakConfigError(f"formats must be chosen from {sorted(_FORMATS)}")
    figures = FigureConfig(
        formats=formats,
        dpi=_positive_int(fig_raw.get("dpi", 300), "dpi", 72),
        slide_size_inches=_size(fig_raw.get("slide_size_inches"), "slide_size_inches", (10.0, 5.625)),
        paper_single_column_inches=_size(fig_raw.get("paper_single_column_inches"), "paper_single_column_inches", (3.5, 3.0)),
        paper_double_column_inches=_size(fig_raw.get("paper_double_column_inches"), "paper_double_column_inches", (7.2, 5.2)),
        show_uncertainty=bool(fig_raw.get("show_uncertainty", True)),
        show_trend=bool(fig_raw.get("show_trend", True)),
    )

    normalizations = tuple(section.get("normalization_modes", tuple(TargetPeakConfig().normalization_modes)))
    if set(normalizations) - _NORMALIZATIONS:
        raise TargetPeakConfigError(f"normalization_modes must be chosen from {sorted(_NORMALIZATIONS)}")

    stages: list[StageConfig] = []
    for index, item in enumerate(section.get("stages", ())):
        stage = _mapping(item, f"target_peak.stages[{index}]")
        _keys(stage, {"label", "start_hours", "end_hours", "expected_direction"}, f"target_peak.stages[{index}]")
        direction = str(stage.get("expected_direction", "unresolved"))
        if direction not in _DIRECTIONS:
            raise TargetPeakConfigError(f"invalid stage direction {direction!r}")
        start, end = float(stage["start_hours"]), float(stage["end_hours"])
        if end <= start:
            raise TargetPeakConfigError("stage end_hours must be greater than start_hours")
        stages.append(StageConfig(str(stage["label"]), start, end, direction))

    return TargetPeakConfig(
        enabled=bool(section.get("enabled", False)),
        dataset_display_name=(None if section.get("dataset_display_name") is None else str(section["dataset_display_name"])),
        peak_label=str(section.get("peak_label", "target peak")),
        expected_center_ppm=expected,
        search_window_ppm=search,
        integration_window_ppm=integration,
        plot_window_ppm=plot_window,
        tracking_max_drift_ppm=_positive(section.get("tracking_max_drift_ppm", 0.08), "tracking_max_drift_ppm"),
        integration_edge_margin_ppm=_positive(section.get("integration_edge_margin_ppm", 0.01), "integration_edge_margin_ppm", allow_zero=True),
        minimum_snr=_positive(section.get("minimum_snr", 8.0), "minimum_snr", allow_zero=True),
        minimum_prominence_snr=_positive(section.get("minimum_prominence_snr", 5.0), "minimum_prominence_snr", allow_zero=True),
        rolling_window=_positive_int(section.get("rolling_window", 4), "rolling_window", 3),
        normalization_modes=normalizations,
        completion=cc,
        figures=figures,
        stages=tuple(stages),
    )
