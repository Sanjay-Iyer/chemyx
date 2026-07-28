"""Configuration model for the optional NMR statistics pipeline.

Parses and validates the ``statistics:`` section of the shared analysis YAML
(``configs/nmr/analysis.yaml``) into typed dataclasses.  Kept separate from the
scripts so it can be unit-tested and reused, and separate from
``chemyx_lab.config`` (which models instrument/experiment config) to avoid
coupling.

The whole feature is **off by default** (``statistics.enabled = false``): a
config file with no ``statistics`` section, or with it disabled, reproduces the
pre-existing ``process_fid.py`` behaviour exactly.  Validation raises
``ConfigError`` with a specific message rather than failing deep inside a run.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class ConfigError(ValueError):
    """Raised when the statistics configuration is malformed."""


@dataclass(frozen=True)
class BootstrapConfig:
    enabled: bool = True
    iterations: int = 500
    confidence_level: float = 0.95
    random_seed: int = 12345
    model: str = "pseudo_voigt"


@dataclass(frozen=True)
class InternalStandardConfig:
    enabled: bool = False
    expected_ppm: float | None = None
    tolerance_ppm: float = 0.03
    integration_window_ppm: float | None = None
    peak_family_id: str | None = None


@dataclass(frozen=True)
class FixedRegionConfig:
    name: str
    left_ppm: float
    right_ppm: float


@dataclass(frozen=True)
class OutlierConfig:
    enabled: bool = True
    robust_z_threshold: float = 3.5


@dataclass(frozen=True)
class PlateauConfig:
    method: str = "statistical_slope"
    minimum_points: int = 4
    equivalence_percent_per_hour: float | None = 1.0
    equivalence_abs_per_hour: float | None = None
    persistence_points: int = 3
    allow_declining_plateau: bool = False


@dataclass(frozen=True)
class KineticsConfig:
    enabled: bool = True
    models: tuple[str, ...] = (
        "zero_order",
        "first_order_decay",
        "first_order_formation",
        "first_order_formation_lag",
    )


@dataclass(frozen=True)
class MultivariateConfig:
    spectral_similarity: bool = True
    pca: bool = True
    pca_components: int = 3


@dataclass(frozen=True)
class StatisticsConfig:
    enabled: bool = False
    bootstrap: BootstrapConfig = field(default_factory=BootstrapConfig)
    internal_standard: InternalStandardConfig = field(
        default_factory=InternalStandardConfig
    )
    fixed_regions: tuple[FixedRegionConfig, ...] = ()
    outliers: OutlierConfig = field(default_factory=OutlierConfig)
    plateau: PlateauConfig = field(default_factory=PlateauConfig)
    kinetics: KineticsConfig = field(default_factory=KineticsConfig)
    multivariate: MultivariateConfig = field(default_factory=MultivariateConfig)


_VALID_KINETIC_MODELS = {
    "zero_order",
    "first_order_decay",
    "first_order_formation",
    "first_order_formation_lag",
}
_VALID_BOOTSTRAP_MODELS = {"gaussian", "lorentzian", "pseudo_voigt"}


def _mapping(value: Any, label: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"'{label}' must be a mapping, got {type(value).__name__}")
    return value


def _check_keys(section: dict, allowed: set[str], label: str) -> None:
    unknown = sorted(set(section) - allowed)
    if unknown:
        raise ConfigError(
            f"Unknown key(s) in [statistics.{label}]: {', '.join(unknown)}. "
            f"Allowed: {', '.join(sorted(allowed))}"
        )


def _positive_int(value: Any, label: str, minimum: int = 1) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{label}' must be an integer, got {value!r}")
    if result < minimum:
        raise ConfigError(f"'{label}' must be >= {minimum}, got {result}")
    return result


def _fraction(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{label}' must be a number, got {value!r}")
    if not 0.0 < result < 1.0:
        raise ConfigError(f"'{label}' must be strictly between 0 and 1, got {result}")
    return result


def load_statistics_config(raw: dict | None) -> StatisticsConfig:
    """Build a :class:`StatisticsConfig` from a raw ``statistics`` mapping.

    ``raw`` is the value of the ``statistics:`` YAML key (or ``None``/absent).
    Every sub-section is validated; unknown keys and out-of-range values raise
    :class:`ConfigError`.  A missing section yields the disabled default.
    """
    section = _mapping(raw, "statistics")
    _check_keys(
        section,
        {
            "enabled", "bootstrap", "internal_standard", "fixed_regions",
            "outliers", "plateau", "kinetics", "multivariate",
        },
        "",
    )
    enabled = bool(section.get("enabled", False))

    boot = _mapping(section.get("bootstrap"), "statistics.bootstrap")
    _check_keys(
        boot,
        {"enabled", "iterations", "confidence_level", "random_seed", "model"},
        "bootstrap",
    )
    boot_model = str(boot.get("model", "pseudo_voigt"))
    if boot_model not in _VALID_BOOTSTRAP_MODELS:
        raise ConfigError(
            f"statistics.bootstrap.model must be one of {sorted(_VALID_BOOTSTRAP_MODELS)}"
        )
    bootstrap = BootstrapConfig(
        enabled=bool(boot.get("enabled", True)),
        iterations=_positive_int(boot.get("iterations", 500), "statistics.bootstrap.iterations", 1),
        confidence_level=_fraction(boot.get("confidence_level", 0.95), "statistics.bootstrap.confidence_level"),
        random_seed=int(boot.get("random_seed", 12345)),
        model=boot_model,
    )

    std = _mapping(section.get("internal_standard"), "statistics.internal_standard")
    _check_keys(
        std,
        {"enabled", "expected_ppm", "tolerance_ppm", "integration_window_ppm", "peak_family_id"},
        "internal_standard",
    )
    internal_standard = InternalStandardConfig(
        enabled=bool(std.get("enabled", False)),
        expected_ppm=_optional_float(std.get("expected_ppm"), "statistics.internal_standard.expected_ppm"),
        tolerance_ppm=float(std.get("tolerance_ppm", 0.03)),
        integration_window_ppm=_optional_float(
            std.get("integration_window_ppm"), "statistics.internal_standard.integration_window_ppm"
        ),
        peak_family_id=_optional_str(std.get("peak_family_id")),
    )

    regions = _regions(section.get("fixed_regions"))

    out = _mapping(section.get("outliers"), "statistics.outliers")
    _check_keys(out, {"enabled", "robust_z_threshold"}, "outliers")
    threshold = float(out.get("robust_z_threshold", 3.5))
    if threshold <= 0:
        raise ConfigError("statistics.outliers.robust_z_threshold must be positive")
    outliers = OutlierConfig(enabled=bool(out.get("enabled", True)), robust_z_threshold=threshold)

    plat = _mapping(section.get("plateau"), "statistics.plateau")
    _check_keys(
        plat,
        {
            "method", "minimum_points", "equivalence_percent_per_hour",
            "equivalence_abs_per_hour", "persistence_points", "allow_declining_plateau",
        },
        "plateau",
    )
    plateau = PlateauConfig(
        method=str(plat.get("method", "statistical_slope")),
        minimum_points=_positive_int(plat.get("minimum_points", 4), "statistics.plateau.minimum_points", 3),
        equivalence_percent_per_hour=_optional_float(
            plat.get("equivalence_percent_per_hour", 1.0),
            "statistics.plateau.equivalence_percent_per_hour",
        ),
        equivalence_abs_per_hour=_optional_float(
            plat.get("equivalence_abs_per_hour"), "statistics.plateau.equivalence_abs_per_hour"
        ),
        persistence_points=_positive_int(
            plat.get("persistence_points", 3), "statistics.plateau.persistence_points", 1
        ),
        allow_declining_plateau=bool(plat.get("allow_declining_plateau", False)),
    )

    kin = _mapping(section.get("kinetics"), "statistics.kinetics")
    _check_keys(kin, {"enabled", "models"}, "kinetics")
    models = tuple(kin.get("models", tuple(KineticsConfig().models)))
    unknown_models = sorted(set(models) - _VALID_KINETIC_MODELS)
    if unknown_models:
        raise ConfigError(
            f"statistics.kinetics.models has unknown model(s): {', '.join(unknown_models)}. "
            f"Allowed: {', '.join(sorted(_VALID_KINETIC_MODELS))}"
        )
    kinetics = KineticsConfig(enabled=bool(kin.get("enabled", True)), models=models)

    mvsec = _mapping(section.get("multivariate"), "statistics.multivariate")
    _check_keys(mvsec, {"spectral_similarity", "pca", "pca_components"}, "multivariate")
    multivariate = MultivariateConfig(
        spectral_similarity=bool(mvsec.get("spectral_similarity", True)),
        pca=bool(mvsec.get("pca", True)),
        pca_components=_positive_int(mvsec.get("pca_components", 3), "statistics.multivariate.pca_components", 1),
    )

    return StatisticsConfig(
        enabled=enabled,
        bootstrap=bootstrap,
        internal_standard=internal_standard,
        fixed_regions=regions,
        outliers=outliers,
        plateau=plateau,
        kinetics=kinetics,
        multivariate=multivariate,
    )


def _optional_float(value: Any, label: str) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ConfigError(f"'{label}' must be a number or null, got {value!r}")


def _optional_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _regions(raw: Any) -> tuple[FixedRegionConfig, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)):
        raise ConfigError("statistics.fixed_regions must be a list of {name,left_ppm,right_ppm}")
    regions: list[FixedRegionConfig] = []
    names: set[str] = set()
    for index, item in enumerate(raw):
        entry = _mapping(item, f"statistics.fixed_regions[{index}]")
        _check_keys(entry, {"name", "left_ppm", "right_ppm"}, f"fixed_regions[{index}]")
        if "name" not in entry or "left_ppm" not in entry or "right_ppm" not in entry:
            raise ConfigError(
                f"statistics.fixed_regions[{index}] needs name, left_ppm, right_ppm"
            )
        name = str(entry["name"])
        if name in names:
            raise ConfigError(f"duplicate fixed_regions name {name!r}")
        names.add(name)
        left = float(entry["left_ppm"])
        right = float(entry["right_ppm"])
        if left == right:
            raise ConfigError(f"fixed_regions[{index}] left_ppm and right_ppm are equal")
        regions.append(FixedRegionConfig(name=name, left_ppm=left, right_ppm=right))
    return tuple(regions)
