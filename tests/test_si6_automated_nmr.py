from datetime import datetime
from pathlib import Path

from chemyx_lab.workflows.si6_automated_nmr import (
    build_stages,
    create_run_paths,
    growth_percent,
    load_si6_config,
    plateau_reached,
    scheduled_measurement_offset_seconds,
    validate_syringe_capacity,
)


CONFIG = Path(__file__).resolve().parents[1] / "configs" / "experiments" / "02_si6_automated_nmr.yaml"
PHSI4_CONFIG = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "experiments"
    / "03_081626_phsi4.yaml"
)


def test_committed_si6_config_has_requested_cycle_and_stages():
    raw = load_si6_config(CONFIG)
    pump_events = [
        (item["action"], item.get("volume_ml"))
        for item in raw["workflow"]["cycle"]
        if item["action"] in {"withdraw", "infuse"}
    ]

    assert pump_events == [
        ("withdraw", 8.0), ("withdraw", 5.0), ("infuse", 13.0),
        ("withdraw", 5.0), ("infuse", 5.0),
    ]
    stages = build_stages(raw["workflow"])
    assert stages[0].interval_minutes == 60
    assert stages[0].max_hours == 26
    assert stages[0].max_measurements == 24
    assert stages[0].measure_immediately is False
    assert stages[0].plateau_stopping_enabled is False
    assert [stage.interval_minutes for stage in stages[1:]] == [15, 15, 15]
    assert [stage.max_measurements for stage in stages[1:]] == [6, 6, 6]
    assert all(stage.plateau_stopping_enabled for stage in stages[1:])


def test_081626_phsi4_config_is_immediate_with_only_the_nmr_pause():
    raw = load_si6_config(PHSI4_CONFIG)
    cycle = raw["workflow"]["cycle"]

    assert [event["action"] for event in cycle] == [
        "withdraw", "withdraw", "pause", "nmr", "infuse", "withdraw", "infuse",
    ]
    pauses = [event["seconds"] for event in cycle if event["action"] == "pause"]
    assert pauses == [10]

    stages = build_stages(raw["workflow"])
    assert len(stages) == 1
    assert stages[0].measure_immediately is True
    assert stages[0].max_measurements == 1
    assert scheduled_measurement_offset_seconds(stages[0], 1) == 0.0

    capacity = validate_syringe_capacity(raw)
    assert capacity.maximum_retained_volume_ml == 13.0
    assert capacity.end_retained_volume_ml == 0.0
    assert raw["pump"]["syringe_diameter_mm"] == 20.0
    assert raw["pump"]["rate_ml_min"] == 5.0
    assert raw["nmr"]["scans"] == 8
    assert raw["workflow"]["name"] == "081626_phsi4"
    assert raw["output"]["run_root_dir"] == "results/runs/081626_phsi4"


def test_plateau_requires_three_growth_intervals_and_clear_peak():
    analysis = {
        "plateau_consecutive_intervals": 3,
        "plateau_max_growth_percent": 5.0,
        "plateau_max_decline_percent": 2.0,
        "detection_window_ppm": 0.12,
        "min_peak_snr": 5.0,
        "min_prominence_snr": 3.0,
        "min_peak_area": 1.0,
        "area_epsilon": 1e-12,
    }
    def row(growth):
        return {
            "target_ppm": 6.1,
            "peak_ppm": 6.1,
            "peak_area": 100.0,
            "snr": 10.0,
            "prominence_snr": 5.0,
            "peak_clear": True,
            "growth_percent": growth,
            "error": "",
        }
    rows = [
        row(None),
        row(4.0),
        row(3.0),
    ]
    assert not plateau_reached(rows, analysis)
    rows.append(row(5.0))
    assert plateau_reached(rows, analysis)
    rows[-1]["peak_clear"] = False
    assert not plateau_reached(rows, analysis)


def test_growth_percent_is_relative_to_previous_area():
    assert growth_percent(100.0, 105.0) == 5.0
    assert growth_percent(None, 105.0) is None
    assert growth_percent(0.0, 105.0) is None


def test_timestamped_run_paths_keep_all_outputs_together(tmp_path):
    paths = create_run_paths(tmp_path, datetime(2026, 7, 22, 14, 5, 6))

    assert paths.run_dir.name == "20260722_140506_si6"
    assert paths.raw_dir.parent == paths.run_dir
    assert paths.plots_dir.parent == paths.run_dir
    assert paths.time_series_csv.parent == paths.run_dir
    assert paths.spectra_csv.parent == paths.run_dir
