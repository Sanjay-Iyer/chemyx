"""Calibration fitting, residuals, warnings, and result-file writing."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest
import yaml

from calibration_utils import (
    CalibrationError,
    CalibrationPoint,
    build_calibration_document,
    build_calibration_fit,
    build_results_document,
    fit_through_origin,
    fit_with_intercept,
    nonlinearity_warnings,
    parse_trial_degrees,
    validate_measurement,
    write_calibration_document,
    write_results_csv,
    write_results_yaml,
)
from motion_utils import degrees_to_steps


def point(
    trial: int,
    degrees: float,
    measured_mm: float,
    return_error_mm: float | None = 0.0,
) -> CalibrationPoint:
    return CalibrationPoint(
        trial=trial,
        degrees=degrees,
        steps=degrees_to_steps(degrees, 800),
        measured_mm=measured_mm,
        return_error_mm=return_error_mm,
        measured_at="2026-08-06T10:00:00Z",
    )


def perfect_points(mm_per_degree: float = 0.0125) -> list[CalibrationPoint]:
    """Three noise-free round trips at 90, 180, and 360 degrees."""
    return [
        point(1, 90.0, 90.0 * mm_per_degree),
        point(2, 180.0, 180.0 * mm_per_degree),
        point(3, 360.0, 360.0 * mm_per_degree),
    ]


# --- 14. Calibration fit calculations ----------------------------------------


def test_through_origin_slope_recovers_a_noise_free_calibration() -> None:
    slope = fit_through_origin(perfect_points(0.0125))

    assert slope == pytest.approx(0.0125)


def test_through_origin_slope_is_the_least_squares_formula() -> None:
    points = [point(1, 90.0, 1.0), point(2, 180.0, 2.5)]
    expected = (90 * 1.0 + 180 * 2.5) / (90**2 + 180**2)

    assert fit_through_origin(points) == pytest.approx(expected)


def test_fit_derives_all_three_published_factors() -> None:
    fit = build_calibration_fit(perfect_points(0.0125), steps_per_revolution=800)

    assert fit.mm_per_degree == pytest.approx(0.0125)
    assert fit.mm_per_step == pytest.approx(0.0125 * 360 / 800)   # 0.005625
    assert fit.steps_per_mm == pytest.approx(1 / 0.005625)
    assert fit.fit_method == "through_origin"
    assert fit.number_of_measurements == 3


def test_a_noise_free_fit_has_zero_residuals() -> None:
    fit = build_calibration_fit(perfect_points())

    assert fit.predicted_mm == pytest.approx(
        [p.measured_mm for p in fit.points], abs=1e-12
    )
    assert fit.max_absolute_residual_mm == pytest.approx(0.0, abs=1e-12)
    assert fit.r_squared == pytest.approx(1.0)


def test_predicted_and_residual_are_reported_for_every_point() -> None:
    points = perfect_points()
    points[1] = point(2, 180.0, 2.30)   # nudge the middle point off the line
    fit = build_calibration_fit(points)

    assert len(fit.predicted_mm) == 3
    assert len(fit.residuals_mm) == 3
    for measured, predicted, residual in zip(
        [p.measured_mm for p in fit.points], fit.predicted_mm, fit.residuals_mm
    ):
        assert residual == pytest.approx(measured - predicted)


def test_free_fit_recovers_a_deliberate_offset() -> None:
    # measured = 0.01 * degrees + 0.5 mm of constant offset
    points = [point(i, d, 0.01 * d + 0.5) for i, d in enumerate([90, 180, 360], 1)]

    slope, intercept = fit_with_intercept(points)

    assert slope == pytest.approx(0.01)
    assert intercept == pytest.approx(0.5)


def test_free_intercept_mode_is_selectable() -> None:
    points = [point(i, d, 0.01 * d + 0.5) for i, d in enumerate([90, 180, 360], 1)]

    fit = build_calibration_fit(points, through_origin=False)

    assert fit.fit_method == "free_intercept"
    assert fit.intercept_mm == pytest.approx(0.5)
    assert fit.mm_per_degree == pytest.approx(0.01)


def test_a_fit_with_no_measurements_is_refused() -> None:
    with pytest.raises(CalibrationError, match="no measurements"):
        build_calibration_fit([])


def test_a_free_fit_needs_two_distinct_angles() -> None:
    with pytest.raises(CalibrationError, match="at least two"):
        fit_with_intercept([point(1, 90.0, 1.0)])

    with pytest.raises(CalibrationError, match="different commanded rotations"):
        fit_with_intercept([point(1, 90.0, 1.0), point(2, 90.0, 1.1)])


def test_a_non_positive_slope_is_refused() -> None:
    # Larger rotations measured as smaller displacements: something is wrong.
    points = [point(1, 90.0, 4.0), point(2, 180.0, 1.0), point(3, 360.0, 0.1)]

    with pytest.raises(CalibrationError, match="not positive"):
        build_calibration_fit(points, through_origin=False)


def test_a_through_origin_slope_cannot_hide_a_reversed_response() -> None:
    # With positive angles and positive measurements the origin fit is always
    # positive, so the free fit is what catches a reversed or nonsense response.
    points = [point(1, 90.0, 4.0), point(2, 180.0, 1.0), point(3, 360.0, 0.1)]

    fit = build_calibration_fit(points, through_origin=True)

    assert fit.mm_per_degree > 0
    assert nonlinearity_warnings(fit)   # but it is loudly flagged as bad


# --- Measurement validation ---------------------------------------------------


@pytest.mark.parametrize("bad", [0, -1.0, -0.001])
def test_non_positive_measurements_are_refused(bad: float) -> None:
    with pytest.raises(CalibrationError, match="greater than zero"):
        validate_measurement(bad)


def test_implausibly_large_measurements_are_refused() -> None:
    with pytest.raises(CalibrationError, match="plausibility limit"):
        validate_measurement(5000.0)


@pytest.mark.parametrize("bad", ["2.5", None, True])
def test_non_numeric_measurements_are_refused(bad: object) -> None:
    with pytest.raises(CalibrationError):
        validate_measurement(bad)  # type: ignore[arg-type]


def test_a_valid_measurement_is_returned_as_a_float() -> None:
    assert validate_measurement(2) == 2.0


# --- 15. Nonlinearity warnings ------------------------------------------------


def test_a_clean_linear_calibration_raises_no_warnings() -> None:
    fit = build_calibration_fit(perfect_points())

    assert nonlinearity_warnings(fit) == []


def test_an_inconsistent_ratio_is_warned_about() -> None:
    # 90 deg gives 0.0125 mm/deg but 360 deg gives 0.0100 mm/deg: 20 % spread.
    points = [point(1, 90.0, 1.125), point(2, 180.0, 2.16), point(3, 360.0, 3.6)]

    warnings = nonlinearity_warnings(build_calibration_fit(points))

    assert any("mm/degree ratios vary" in warning for warning in warnings)


def test_a_large_residual_is_warned_about() -> None:
    points = perfect_points()
    points[1] = point(2, 180.0, 1.35)   # 40 % low

    warnings = nonlinearity_warnings(build_calibration_fit(points))

    assert any("residual" in warning for warning in warnings)


def test_a_poor_straight_line_fit_lowers_r_squared_and_warns() -> None:
    points = [point(1, 90.0, 3.0), point(2, 180.0, 3.4), point(3, 360.0, 3.9)]

    fit = build_calibration_fit(points)
    warnings = nonlinearity_warnings(fit)

    assert fit.r_squared < 0.999
    assert any("R-squared" in warning for warning in warnings)


def test_a_single_measurement_is_flagged_as_unassessable() -> None:
    warnings = nonlinearity_warnings(build_calibration_fit([point(1, 90.0, 1.125)]))

    assert any("linearity cannot be assessed" in warning for warning in warnings)


def test_repeating_one_angle_is_flagged_as_unassessable() -> None:
    points = [point(1, 90.0, 1.125), point(2, 90.0, 1.126)]

    warnings = nonlinearity_warnings(build_calibration_fit(points))

    assert any("same commanded rotation" in warning for warning in warnings)


def test_a_hidden_intercept_is_warned_about_in_through_origin_mode() -> None:
    # A consistent 0.4 mm offset, the classic backlash signature.
    points = [point(i, d, 0.0125 * d + 0.4) for i, d in enumerate([90, 180, 360], 1)]

    warnings = nonlinearity_warnings(build_calibration_fit(points, through_origin=True))

    assert any("intercept" in warning for warning in warnings)


# --- Result and calibration file writing --------------------------------------


def test_results_yaml_records_every_observation(tmp_path: Path) -> None:
    fit = build_calibration_fit(perfect_points())
    document = build_results_document(
        fit,
        config_path="configs/99_needle_calibration.yaml",
        started_at="2026-08-06T10:00:00Z",
        finished_at="2026-08-06T10:05:00Z",
    )

    path = write_results_yaml(tmp_path / "result.yaml", document)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert len(loaded["observations"]) == 3
    assert loaded["observations"][0]["commanded_degrees"] == 90.0
    assert loaded["observations"][0]["commanded_steps"] == 200
    assert loaded["observations"][0]["return_error_mm"] == 0.0
    assert loaded["observations"][0]["repetition"] == 1
    assert loaded["fit"]["mm_per_step"] == pytest.approx(0.005625)
    assert loaded["run"]["finished_at"] == "2026-08-06T10:05:00Z"
    assert loaded["warnings"] == []


def test_results_csv_has_one_row_per_trial(tmp_path: Path) -> None:
    fit = build_calibration_fit(perfect_points())

    path = write_results_csv(tmp_path / "result.csv", fit)
    rows = list(csv.DictReader(path.read_text(encoding="utf-8").splitlines()))

    assert len(rows) == 3
    assert rows[2]["commanded_degrees"] == "360.0"
    assert rows[2]["commanded_steps"] == "800"
    assert float(rows[2]["residual_mm"]) == pytest.approx(0.0, abs=1e-6)


def test_calibration_document_is_complete_and_reloadable(tmp_path: Path) -> None:
    fit = build_calibration_fit(perfect_points())
    document = build_calibration_document(
        fit,
        source_results_file="calibration_results/needle_calibration_x.yaml",
        created_at="2026-08-06T10:05:00Z",
    )

    path = write_calibration_document(tmp_path / "needle_calibration.yaml", document)
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))["calibration"]

    assert loaded["calibrated"] is True
    assert loaded["steps_per_revolution"] == 800
    assert loaded["mm_per_step"] == pytest.approx(0.005625)
    assert loaded["steps_per_mm"] == pytest.approx(177.777778, abs=1e-4)
    assert loaded["fit_method"] == "through_origin"
    assert loaded["number_of_measurements"] == 3
    assert loaded["source_results_file"].endswith("needle_calibration_x.yaml")

    metadata = yaml.safe_load(path.read_text(encoding="utf-8"))["metadata"]
    assert "no encoder or limit switches" in metadata["warning"]
    assert metadata["direction_definition"] == "Positive steps are defined as forward"


def test_written_calibration_can_be_read_back_by_the_movement_script(
    tmp_path: Path,
) -> None:
    from calibration_utils import load_calibration

    fit = build_calibration_fit(perfect_points())
    path = write_calibration_document(
        tmp_path / "needle_calibration.yaml",
        build_calibration_document(fit, source_results_file="x.yaml"),
    )

    calibration = load_calibration(path).require_calibrated()

    assert calibration.mm_per_step == pytest.approx(fit.mm_per_step)


# --- Calibration plan validation ----------------------------------------------


def test_trial_degrees_are_validated_and_converted() -> None:
    config = {"calibration": {"trial_degrees": [90, 180, 360]}}

    degrees = parse_trial_degrees(
        config, steps_per_revolution=800, maximum_absolute_degrees=360
    )

    assert degrees == [90.0, 180.0, 360.0]
    assert [degrees_to_steps(d, 800) for d in degrees] == [200, 400, 800]


@pytest.mark.parametrize("bad", [[], [0], [-90], ["90"], [None], "90"])
def test_bad_trial_degrees_are_refused(bad: object) -> None:
    with pytest.raises(CalibrationError):
        parse_trial_degrees(
            {"calibration": {"trial_degrees": bad}},
            steps_per_revolution=800,
            maximum_absolute_degrees=360,
        )


def test_a_trial_beyond_the_configured_maximum_is_refused() -> None:
    with pytest.raises(CalibrationError, match="maximum_absolute_degrees"):
        parse_trial_degrees(
            {"calibration": {"trial_degrees": [720]}},
            steps_per_revolution=800,
            maximum_absolute_degrees=360,
        )


def test_a_trial_that_rounds_to_zero_steps_is_refused() -> None:
    with pytest.raises(CalibrationError, match="rounds to 0 steps"):
        parse_trial_degrees(
            {"calibration": {"trial_degrees": [0.05]}},
            steps_per_revolution=800,
            maximum_absolute_degrees=360,
        )


def test_shipped_calibration_config_is_valid(package_root: Path) -> None:
    from motion_utils import load_yaml_mapping

    config = load_yaml_mapping(package_root / "configs" / "99_needle_calibration.yaml")
    degrees = parse_trial_degrees(
        config,
        steps_per_revolution=config["driver"]["steps_per_revolution"],
        maximum_absolute_degrees=config["motion"]["maximum_absolute_degrees"],
    )

    assert degrees == [90.0, 180.0, 360.0]
    assert config["calibration"]["fit_through_origin"] is True
    assert config["motion"]["require_typed_confirmation"] is True
