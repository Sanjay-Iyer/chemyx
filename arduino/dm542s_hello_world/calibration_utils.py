"""Calibration file I/O, straight-line fitting, and consistency warnings.

The calibration this module produces is a *software* calibration: it relates
degrees the driver was told to turn to millimetres a human measured. It assumes
no step was missed during the calibration run. Nothing here proves the mechanism
is rigid, backlash-free, or repeatable; only repeated measurement shows that.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import yaml

from motion_utils import (
    DEFAULT_STEPS_PER_REVOLUTION,
    FIRMWARE_MAX_ABSOLUTE_STEPS,
    MotionConfigError,
    atomic_write_text,
    degrees_to_steps,
    derive_calibration_factors,
    iso_timestamp,
    load_yaml_mapping,
    require_section,
    steps_to_degrees,
)


# Thresholds used only to decide whether to print a warning. They never block a
# calibration; the operator decides what to do about a flagged fit.
RATIO_SPREAD_WARNING = 0.05        # 5 % spread in per-point mm/degree
RELATIVE_RESIDUAL_WARNING = 0.05   # 5 % of the measured displacement
R_SQUARED_WARNING = 0.999
INTERCEPT_WARNING_FRACTION = 0.10  # 10 % of the smallest measurement

# Mean absolute physical return error above which lost motion is called out.
RETURN_ERROR_WARNING_MM = 0.05

# A displacement larger than this from one calibration trial is almost certainly
# a typo or a wrong unit rather than a real needle movement.
MAX_PLAUSIBLE_MEASUREMENT_MM = 500.0


class CalibrationError(MotionConfigError):
    """Raised when calibration data is missing, unusable, or not yet performed."""


@dataclass(frozen=True)
class Calibration:
    """The contents of ``configs/needle_calibration.yaml``."""

    calibrated: bool
    steps_per_revolution: int
    mm_per_degree: float | None = None
    mm_per_step: float | None = None
    steps_per_mm: float | None = None
    fit_method: str | None = None
    source_results_file: str | None = None
    number_of_measurements: int | None = None
    created_at: str | None = None
    source_path: Path | None = None

    def require_calibrated(self) -> "Calibration":
        """Return self, or explain exactly why millimetre mode cannot run."""
        where = self.source_path or "the calibration file"
        if not self.calibrated:
            raise CalibrationError(
                f"{where} reports calibration.calibrated: false.\n"
                "Millimetre mode needs a completed calibration. Either run\n"
                "  python .\\99_needle_calibration.py "
                "--config .\\configs\\99_needle_calibration.yaml\n"
                "or set execution.use_mm_calibration: false to work in degrees."
            )
        if not self.mm_per_step or self.mm_per_step <= 0:
            raise CalibrationError(
                f"{where} claims calibrated: true but has no usable "
                f"mm_per_step (found {self.mm_per_step!r}). Re-run the "
                "calibration script."
            )
        return self


def load_calibration(path: str | Path) -> Calibration:
    """Read the authoritative calibration file, or say why it cannot be used."""
    calibration_path = Path(path)
    if not calibration_path.is_file():
        raise CalibrationError(
            f"Calibration file not found: {calibration_path}\n"
            "Run 99_needle_calibration.py first, or set "
            "execution.use_mm_calibration: false to work in degrees."
        )

    document = load_yaml_mapping(calibration_path)
    section = require_section(document, "calibration")

    calibrated = section.get("calibrated")
    if not isinstance(calibrated, bool):
        raise CalibrationError(
            f"{calibration_path}: calibration.calibrated must be true or false, "
            f"received {calibrated!r}"
        )

    def optional_number(key: str) -> float | None:
        value = section.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CalibrationError(
                f"{calibration_path}: calibration.{key} must be a number, "
                f"received {value!r}"
            )
        return float(value)

    steps_per_revolution = section.get(
        "steps_per_revolution", DEFAULT_STEPS_PER_REVOLUTION
    )
    if isinstance(steps_per_revolution, bool) or not isinstance(
        steps_per_revolution, int
    ):
        raise CalibrationError(
            f"{calibration_path}: calibration.steps_per_revolution must be a "
            f"whole number, received {steps_per_revolution!r}"
        )

    number_of_measurements = section.get("number_of_measurements")
    if number_of_measurements is not None and (
        isinstance(number_of_measurements, bool)
        or not isinstance(number_of_measurements, int)
    ):
        raise CalibrationError(
            f"{calibration_path}: calibration.number_of_measurements must be a "
            f"whole number, received {number_of_measurements!r}"
        )

    return Calibration(
        calibrated=calibrated,
        steps_per_revolution=steps_per_revolution,
        mm_per_degree=optional_number("mm_per_degree"),
        mm_per_step=optional_number("mm_per_step"),
        steps_per_mm=optional_number("steps_per_mm"),
        fit_method=section.get("fit_method"),
        source_results_file=section.get("source_results_file"),
        number_of_measurements=number_of_measurements,
        created_at=section.get("created_at"),
        source_path=calibration_path,
    )


# ---------------------------------------------------------------------------
# Observations and fitting
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CalibrationPoint:
    """One measured round trip: commanded degrees in, measured millimetres out.

    ``return_error_mm`` is the *physically measured* offset from the starting
    mark after the return move, signed (+ = past the mark in the forward
    direction). It is the only direct evidence in this whole package that the
    needle actually goes back where it started; everything else is inference.
    ``None`` means the operator skipped that measurement.
    """

    trial: int
    degrees: float
    steps: int
    measured_mm: float
    repetition: int = 1
    return_error_mm: float | None = None
    measured_at: str = ""


def validate_return_error(value: float) -> float:
    """Accept a SIGNED return offset, including exactly zero.

    Deliberately not :func:`validate_measurement`: a perfect return is 0.0 and
    a return that overshoots is negative, both of which that function rejects.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationError(f"Return error must be a number, received {value!r}")
    number = float(value)
    if number != number:
        raise CalibrationError("Return error must be a real number, received NaN")
    if abs(number) > MAX_PLAUSIBLE_MEASUREMENT_MM:
        raise CalibrationError(
            f"Return error of {number} mm exceeds the "
            f"{MAX_PLAUSIBLE_MEASUREMENT_MM} mm plausibility limit. Check the units."
        )
    return number


def validate_measurement(measured_mm: float) -> float:
    """Accept only a positive, physically plausible displacement."""
    if isinstance(measured_mm, bool) or not isinstance(measured_mm, (int, float)):
        raise CalibrationError(f"Measurement must be a number, received {measured_mm!r}")
    value = float(measured_mm)
    if value != value:
        raise CalibrationError("Measurement must be a real number, received NaN")
    if value <= 0:
        raise CalibrationError(
            f"Measurement must be greater than zero, received {value!r}. Measure "
            "the magnitude of the displacement; direction is already known."
        )
    if value > MAX_PLAUSIBLE_MEASUREMENT_MM:
        raise CalibrationError(
            f"Measurement of {value} mm exceeds the {MAX_PLAUSIBLE_MEASUREMENT_MM} mm "
            "plausibility limit. Check the units and re-enter."
        )
    return value


@dataclass(frozen=True)
class CalibrationFit:
    """A fitted calibration plus everything needed to judge it."""

    points: tuple[CalibrationPoint, ...]
    steps_per_revolution: int
    fit_method: str
    mm_per_degree: float
    mm_per_step: float
    steps_per_mm: float
    intercept_mm: float
    free_fit_intercept_mm: float
    r_squared: float
    predicted_mm: tuple[float, ...] = field(default=())
    residuals_mm: tuple[float, ...] = field(default=())

    @property
    def number_of_measurements(self) -> int:
        return len(self.points)

    @property
    def max_absolute_residual_mm(self) -> float:
        return max((abs(value) for value in self.residuals_mm), default=0.0)


def fit_through_origin(points: Sequence[CalibrationPoint]) -> float:
    """Least-squares slope of a line forced through (0, 0): sum(xy)/sum(x^2)."""
    if not points:
        raise CalibrationError("Cannot fit a calibration with no measurements")
    sum_xx = sum(point.degrees**2 for point in points)
    if sum_xx <= 0:
        raise CalibrationError(
            "Cannot fit a calibration: every commanded rotation was zero degrees"
        )
    sum_xy = sum(point.degrees * point.measured_mm for point in points)
    return sum_xy / sum_xx


def fit_with_intercept(points: Sequence[CalibrationPoint]) -> tuple[float, float]:
    """Ordinary least-squares ``(slope, intercept)`` for mm against degrees."""
    count = len(points)
    if count < 2:
        raise CalibrationError(
            "A free (non-origin) fit needs at least two measurements"
        )
    mean_x = sum(point.degrees for point in points) / count
    mean_y = sum(point.measured_mm for point in points) / count
    variance = sum((point.degrees - mean_x) ** 2 for point in points)
    if variance <= 0:
        raise CalibrationError(
            "A free fit needs at least two different commanded rotations"
        )
    covariance = sum(
        (point.degrees - mean_x) * (point.measured_mm - mean_y) for point in points
    )
    slope = covariance / variance
    return slope, mean_y - slope * mean_x


def build_calibration_fit(
    points: Sequence[CalibrationPoint],
    *,
    steps_per_revolution: int = DEFAULT_STEPS_PER_REVOLUTION,
    through_origin: bool = True,
) -> CalibrationFit:
    """Fit the measurements and compute predictions plus residuals."""
    if not points:
        raise CalibrationError("Cannot build a calibration with no measurements")
    for point in points:
        validate_measurement(point.measured_mm)

    if through_origin:
        slope = fit_through_origin(points)
        intercept = 0.0
        method = "through_origin"
    else:
        slope, intercept = fit_with_intercept(points)
        method = "free_intercept"

    if slope <= 0:
        raise CalibrationError(
            f"The fitted slope is {slope:.6g} mm/degree, which is not positive. "
            "Positive steps must produce positive measured displacement. Check "
            "that every measurement was entered as a positive magnitude and that "
            "the needle actually moved."
        )

    predicted = tuple(slope * point.degrees + intercept for point in points)
    residuals = tuple(
        point.measured_mm - prediction
        for point, prediction in zip(points, predicted)
    )

    # A free fit is computed even in through-origin mode: a large intercept is a
    # useful hint about backlash or a measurement offset.
    try:
        _, free_intercept = fit_with_intercept(points)
    except CalibrationError:
        free_intercept = 0.0

    factors = derive_calibration_factors(slope, steps_per_revolution)

    mean_y = sum(point.measured_mm for point in points) / len(points)
    ss_total = sum((point.measured_mm - mean_y) ** 2 for point in points)
    ss_residual = sum(value**2 for value in residuals)
    r_squared = 1.0 if ss_total <= 0 else 1.0 - ss_residual / ss_total

    return CalibrationFit(
        points=tuple(points),
        steps_per_revolution=steps_per_revolution,
        fit_method=method,
        mm_per_degree=factors["mm_per_degree"],
        mm_per_step=factors["mm_per_step"],
        steps_per_mm=factors["steps_per_mm"],
        intercept_mm=intercept,
        free_fit_intercept_mm=free_intercept,
        r_squared=r_squared,
        predicted_mm=predicted,
        residuals_mm=residuals,
    )


def nonlinearity_warnings(fit: CalibrationFit) -> list[str]:
    """Describe every reason to distrust this fit. Empty means nothing flagged."""
    warnings: list[str] = []

    distinct_degrees = {point.degrees for point in fit.points}
    if len(fit.points) < 2:
        warnings.append(
            "Only one measurement was taken, so linearity cannot be assessed at "
            "all. This calibration is a single-point scale factor."
        )
    elif len(distinct_degrees) < 2:
        warnings.append(
            "Every trial used the same commanded rotation, so linearity cannot "
            "be assessed. Add trials at different angles."
        )

    ratios = [
        point.measured_mm / point.degrees for point in fit.points if point.degrees
    ]
    if len(ratios) >= 2:
        mean_ratio = sum(ratios) / len(ratios)
        if mean_ratio > 0:
            spread = (max(ratios) - min(ratios)) / mean_ratio
            if spread > RATIO_SPREAD_WARNING:
                warnings.append(
                    f"Per-point mm/degree ratios vary by {spread * 100:.1f} % "
                    f"(min {min(ratios):.6g}, max {max(ratios):.6g}). Above "
                    f"{RATIO_SPREAD_WARNING * 100:.0f} % the response does not "
                    "look linear; suspect backlash, slip, or measurement error."
                )

    for point, residual in zip(fit.points, fit.residuals_mm):
        if point.measured_mm <= 0:
            continue
        relative = abs(residual) / point.measured_mm
        if relative > RELATIVE_RESIDUAL_WARNING:
            warnings.append(
                f"Trial {point.trial} ({point.degrees:g} deg): residual "
                f"{residual:+.4f} mm is {relative * 100:.1f} % of the measured "
                f"{point.measured_mm:.4f} mm."
            )

    if len(fit.points) >= 2 and fit.r_squared < R_SQUARED_WARNING:
        warnings.append(
            f"R-squared is {fit.r_squared:.6f}, below {R_SQUARED_WARNING}. The "
            "straight-line fit does not describe these measurements well."
        )

    smallest = min((point.measured_mm for point in fit.points), default=0.0)
    if (
        fit.fit_method == "through_origin"
        and smallest > 0
        and abs(fit.free_fit_intercept_mm) > INTERCEPT_WARNING_FRACTION * smallest
    ):
        warnings.append(
            f"An unconstrained fit of the same data has a "
            f"{fit.free_fit_intercept_mm:+.4f} mm intercept, more than "
            f"{INTERCEPT_WARNING_FRACTION * 100:.0f} % of the smallest measurement "
            f"({smallest:.4f} mm). A non-zero intercept usually means backlash or "
            "a consistent measurement offset, which a through-origin fit hides."
        )

    warnings += return_error_warnings(fit.points)
    return warnings


def measured_return_errors(points: Sequence[CalibrationPoint]) -> list[float]:
    """The return offsets the operator actually measured, skipping any omitted."""
    return [
        point.return_error_mm
        for point in points
        if point.return_error_mm is not None
    ]


def return_error_warnings(points: Sequence[CalibrationPoint]) -> list[str]:
    """Report directly measured lost motion, as distinct from a fitted intercept."""
    errors = measured_return_errors(points)
    if not errors:
        return [
            "Physical return-to-zero was not measured, so lost motion and "
            "backlash can only be inferred from the fit, never observed. Set "
            "calibration.measure_return_error: true to collect it."
        ]

    mean_absolute = sum(abs(value) for value in errors) / len(errors)
    if mean_absolute <= RETURN_ERROR_WARNING_MM:
        return []
    biased = all(value > 0 for value in errors) or all(value < 0 for value in errors)
    detail = (
        " Every trial missed in the same direction, which is the signature of "
        "backlash or lost motion rather than random measurement scatter."
        if biased and len(errors) > 1
        else ""
    )
    return [
        f"MEASURED return error averages {mean_absolute:.4f} mm over "
        f"{len(errors)} trial(s), above the {RETURN_ERROR_WARNING_MM} mm "
        f"threshold (values: "
        f"{', '.join(f'{value:+.4f}' for value in errors)}).{detail} "
        "Commanded zero is not physical zero on this mechanism."
    ]


# ---------------------------------------------------------------------------
# Result and calibration file writing
# ---------------------------------------------------------------------------


def build_results_document(
    fit: CalibrationFit,
    *,
    config_path: str | Path,
    started_at: str,
    finished_at: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the raw-observation record saved for every calibration run."""
    return {
        "run": {
            "started_at": started_at,
            "finished_at": finished_at,
            "config_file": str(config_path),
            "steps_per_revolution": fit.steps_per_revolution,
            "fit_method": fit.fit_method,
        },
        "observations": [
            {
                "trial": point.trial,
                "repetition": point.repetition,
                "measured_at": point.measured_at,
                "commanded_degrees": point.degrees,
                "commanded_steps": point.steps,
                "measured_mm": point.measured_mm,
                # Physically measured offset after the return move, or null.
                "return_error_mm": point.return_error_mm,
                "predicted_mm": round(prediction, 6),
                "residual_mm": round(residual, 6),
            }
            for point, prediction, residual in zip(
                fit.points, fit.predicted_mm, fit.residuals_mm
            )
        ],
        "fit": {
            "mm_per_degree": fit.mm_per_degree,
            "mm_per_step": fit.mm_per_step,
            "steps_per_mm": fit.steps_per_mm,
            "intercept_mm": fit.intercept_mm,
            "free_fit_intercept_mm": fit.free_fit_intercept_mm,
            "r_squared": fit.r_squared,
            "max_absolute_residual_mm": fit.max_absolute_residual_mm,
            "number_of_measurements": fit.number_of_measurements,
        },
        "warnings": nonlinearity_warnings(fit),
        "metadata": metadata or {},
    }


def write_results_yaml(path: str | Path, document: dict[str, Any]) -> Path:
    """Save the timestamped raw observations as YAML."""
    return atomic_write_text(
        path, yaml.safe_dump(document, sort_keys=False, default_flow_style=False)
    )


def write_results_csv(path: str | Path, fit: CalibrationFit) -> Path:
    """Save the same observations as a spreadsheet-friendly CSV."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(
        [
            "trial",
            "repetition",
            "measured_at",
            "commanded_degrees",
            "commanded_steps",
            "measured_mm",
            "return_error_mm",
            "predicted_mm",
            "residual_mm",
        ]
    )
    for point, prediction, residual in zip(
        fit.points, fit.predicted_mm, fit.residuals_mm
    ):
        writer.writerow(
            [
                point.trial,
                point.repetition,
                point.measured_at,
                point.degrees,
                point.steps,
                point.measured_mm,
                "" if point.return_error_mm is None else f"{point.return_error_mm:.6f}",
                f"{prediction:.6f}",
                f"{residual:.6f}",
            ]
        )
    return atomic_write_text(path, buffer.getvalue())


def build_calibration_document(
    fit: CalibrationFit,
    *,
    source_results_file: str,
    created_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the authoritative ``configs/needle_calibration.yaml`` contents."""
    default_metadata = {
        "motor_model": "NEMA 17, 1.8 degree full step, about 1.5 A per phase",
        "driver_model": "Cloudray DM542S",
        "direction_definition": "Positive steps are defined as forward",
        "warning": (
            "Software calibration only; no encoder or limit switches installed"
        ),
    }
    default_metadata.update(metadata or {})

    return {
        "calibration": {
            "calibrated": True,
            "created_at": created_at or iso_timestamp(),
            "steps_per_revolution": fit.steps_per_revolution,
            "mm_per_degree": float(f"{fit.mm_per_degree:.9g}"),
            "mm_per_step": float(f"{fit.mm_per_step:.9g}"),
            "steps_per_mm": float(f"{fit.steps_per_mm:.9g}"),
            "fit_method": fit.fit_method,
            "source_results_file": source_results_file,
            "number_of_measurements": fit.number_of_measurements,
        },
        "metadata": default_metadata,
    }


def write_calibration_document(path: str | Path, document: dict[str, Any]) -> Path:
    """Overwrite the authoritative calibration file. Callers must confirm first.

    Written atomically with a ``.bak`` of the previous version. A half-written
    calibration that still parses would silently produce wrong millimetres
    forever, so this file in particular must never be updated in place.
    """
    header = (
        "# Authoritative needle calibration.\n"
        "# Generated by 99_needle_calibration.py -- do not hand-edit the values\n"
        "# below unless you know exactly why. Re-run the calibration instead.\n"
        "# Software calibration only: no encoder, no limit switches, no homing.\n"
    )
    return atomic_write_text(
        path,
        header + yaml.safe_dump(document, sort_keys=False, default_flow_style=False),
        keep_backup=True,
    )


def parse_trial_degrees(
    config: dict[str, Any], *, steps_per_revolution: int, maximum_absolute_degrees: float
) -> list[float]:
    """Validate ``calibration.trial_degrees`` and check each converts to steps."""
    section = require_section(config, "calibration")
    values = section.get("trial_degrees")
    if not isinstance(values, list) or not values:
        raise CalibrationError(
            "calibration.trial_degrees must be a non-empty YAML list of angles"
        )

    degrees: list[float] = []
    for index, value in enumerate(values, start=1):
        where = f"calibration.trial_degrees[{index}]"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise CalibrationError(f"{where} must be a number, received {value!r}")
        angle = float(value)
        if angle <= 0:
            raise CalibrationError(
                f"{where} must be greater than zero, received {angle!r}. Each trial "
                "moves forward and then back by the same amount."
            )
        if angle > maximum_absolute_degrees:
            raise CalibrationError(
                f"{where} is {angle:g} degrees, above "
                f"motion.maximum_absolute_degrees ({maximum_absolute_degrees:g})."
            )
        steps = degrees_to_steps(angle, steps_per_revolution)
        if steps <= 0:
            raise CalibrationError(
                f"{where} is {angle:g} degrees, which rounds to {steps} steps at "
                f"{steps_per_revolution} steps/revolution. Use a larger angle."
            )
        # Catch this here, before the port is opened. Without it the script
        # would open the port, print the checklist, take a typed RUN, and only
        # then discover the firmware cannot accept the move.
        if steps > FIRMWARE_MAX_ABSOLUTE_STEPS:
            raise CalibrationError(
                f"{where} is {angle:g} degrees = {steps} steps at "
                f"{steps_per_revolution} steps/revolution, above the firmware "
                f"maximum of {FIRMWARE_MAX_ABSOLUTE_STEPS} steps per MOVE "
                "command. Use a smaller angle."
            )
        degrees.append(angle)
    return degrees


def describe_trial(degrees: float, steps: int, steps_per_revolution: int) -> str:
    """One line explaining what a planned trial will actually command."""
    actual = steps_to_degrees(steps, steps_per_revolution)
    return (
        f"{degrees:g} deg requested -> {steps} steps -> {actual:g} deg commanded "
        f"(forward), then {-steps:+d} steps back to commanded zero"
    )
