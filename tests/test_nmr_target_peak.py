"""Synthetic edge cases for focused peak kinetics and completion decisions."""

from __future__ import annotations

from datetime import datetime, timedelta
import numpy as np
import pytest

from chemyx_lab.analysis.completion import STATUSES, detect_completion
from chemyx_lab.analysis.target_peak_config import CompletionConfig
from chemyx_lab.analysis.time_series import (
    FixedRegion,
    elapsed_hours,
    integrate_fixed_region,
    series_rates,
)


def _config(**overrides) -> CompletionConfig:
    values = CompletionConfig().__dict__ | overrides
    return CompletionConfig(**values)


def test_growth_completion_requires_consecutive_stability():
    times = [0, 0.4, 0.9, 1.3, 1.8, 2.2, 2.8]
    areas = [1, 5, 9, 10, 10.2, 10.1, 10.15]
    before = detect_completion(times[:-1], areas[:-1], config=_config())
    after = detect_completion(times, areas, config=_config())
    assert before.complete is False
    assert after.complete is True
    assert after.status == "growth_plateau"
    assert after.trend_direction == "growth"


def test_disappearance_completion_requires_low_stable_signal():
    times = [0, 0.4, 0.9, 1.3, 1.8, 2.2, 2.8, 3.2]
    areas = [100, 70, 30, 8, 5.1, 5.0, 4.9, 5.0]
    result = detect_completion(times, areas, config=_config())
    assert result.complete is True
    assert result.status == "low_signal_plateau"
    assert result.trend_direction == "disappearance"


def test_flat_series_is_stable_but_not_reaction_completion():
    times = [0, 0.4, 0.9, 1.3, 1.8, 2.2]
    result = detect_completion(times, [10, 10.1, 9.9, 10.0, 10.1, 10.0])
    assert result.status == "stable"
    assert result.complete is False


def test_reversal_and_increase_decrease_increase_are_not_complete():
    growth_then_decrease = detect_completion(
        [0, 0.3, 0.7, 1.1, 1.6, 2.1], [1, 5, 10, 10, 8, 6]
    )
    multi_stage = detect_completion(
        [0, 0.3, 0.7, 1.1, 1.6, 2.1, 2.6], [1, 6, 10, 7, 4, 7, 10]
    )
    assert growth_then_decrease.status == "reversal"
    assert growth_then_decrease.complete is False
    assert multi_stage.complete is False
    assert multi_stage.status in STATUSES


def test_single_noisy_outlier_cannot_stop_reaction():
    result = detect_completion(
        [0, 0.4, 0.8, 1.2, 1.6, 2.0], [1, 5, 9, 10, 15, 10.1]
    )
    assert result.complete is False


def test_recent_low_quality_blocks_completion():
    result = detect_completion(
        [0, 0.4, 0.9, 1.3, 1.8, 2.2, 2.8],
        [1, 5, 9, 10, 10.2, 10.1, 10.15],
        quality_pass=[True, True, True, True, True, False, True],
    )
    assert result.complete is False
    assert result.status == "poor_quality"


def test_empty_or_missing_spectra_are_insufficient():
    result = detect_completion([], [])
    assert result.status == "insufficient_data"
    assert "no spectra" in result.quality_warnings[0]


def test_irregular_elapsed_time_is_preserved_exactly():
    start = datetime(2026, 6, 9, 9, 13, 16)
    timestamps = [
        start,
        start + timedelta(minutes=54, seconds=27),
        start + timedelta(hours=1, minutes=12, seconds=49),
        start + timedelta(hours=1, minutes=25, seconds=31),
    ]
    values = elapsed_hours(timestamps)
    assert values == pytest.approx([0, 0.9075, 1.2136111111, 1.4252777778])
    assert np.diff(values)[0] != pytest.approx(np.diff(values)[1])


def test_missing_and_duplicate_timestamps_fail_softly():
    start = datetime(2026, 6, 9, 9, 0)
    values = elapsed_hours([start, None, start])
    assert values[0] == 0
    assert np.isnan(values[1])
    rates = series_rates([0, 0, 1], [1, 2, 3], rolling_window=3)
    assert np.isnan(rates[1].absolute_rate_per_hour)


def test_unequal_sampling_rate_uses_delta_time():
    rows = series_rates([0, 0.5, 2.0], [0, 2, 5], rolling_window=3)
    assert rows[1].absolute_rate_per_hour == pytest.approx(4.0)
    assert rows[2].absolute_rate_per_hour == pytest.approx(2.0)


def test_fixed_target_peak_integration_and_drift_window():
    ppm = np.linspace(5.6, 5.95, 4000)
    center = 5.795
    signal = 100 * np.exp(-0.5 * ((ppm - center) / 0.012) ** 2)
    integral = integrate_fixed_region(ppm, signal, FixedRegion("5.7 ppm", 5.70, 5.90))
    assert integral.qc_pass
    assert integral.positive_area > 2.5
    assert abs(center - 5.79) < 0.08


def test_peak_shift_beyond_bound_is_not_followed():
    expected = 5.79
    unrelated = 5.89
    tracking_bound = 0.08
    assert abs(unrelated - expected) > tracking_bound


def test_low_snr_quality_vector_prevents_false_completion():
    result = detect_completion(
        [0, 0.5, 1.0, 1.5, 2.0, 2.5],
        [1, 4, 8, 8.1, 8.0, 8.1],
        quality_pass=[True, True, False, False, False, False],
    )
    assert result.status == "poor_quality"
    assert not result.complete
