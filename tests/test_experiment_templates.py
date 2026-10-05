"""Configuration-only checks for copyable recipes; no hardware sessions."""
from copy import deepcopy

import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.workflows import si6_automated_nmr as base
from chemyx_lab.workflows import three_instrument_si6 as integrated


TEMPLATES = config.REPO_ROOT / "config_templates/experiments"


@pytest.mark.parametrize("path", sorted(TEMPLATES.glob("*.yaml")), ids=lambda p: p.stem)
def test_recipe_validates_and_preserves_sop(path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Template validation attempted to construct hardware services")
    monkeypatch.setattr(integrated, "Pump", forbidden)
    monkeypatch.setattr(integrated, "NeedleController", forbidden)
    monkeypatch.setattr(integrated, "NmrRpcClient", forbidden)
    raw, _, _, nmr = integrated.prepare(
        path, config.REPO_ROOT / "configs/machines/00_machine.local.yaml",
        config.REPO_ROOT / "arduino/configs/arduino.local.yaml", mock=True,
    )
    assert integrated.cycle_values(raw) == {
        "initial_withdraw_ml": 8, "sample_withdraw_ml": 5,
        "settle_seconds": 300, "return_infuse_ml": 13,
        "cleanup_withdraw_ml": 5, "cleanup_infuse_ml": 5,
    }
    assert nmr.route == "iflow" and nmr.result_type == "fid" and not nmr.auto_gain


def test_standard_recipe_is_the_existing_default():
    assert base.load_si6_config(TEMPLATES / "standard_si6.yaml") == base.load_si6_config(base.DEFAULT_CONFIG)


@pytest.mark.parametrize("name,intervals", [
    ("reaction_30min", [30, 30, 30, 30]),
    ("reaction_60min", [60, 60, 60, 60]),
    ("reaction_mixed_intervals", [60, 30, 60, 60]),
])
def test_stage_offsets_follow_each_template(name, intervals):
    raw = base.load_si6_config(TEMPLATES / f"{name}.yaml")
    stages = base.build_stages(raw["workflow"])
    assert [s.interval_minutes for s in stages] == intervals
    for stage, interval in zip(stages, intervals):
        assert base.scheduled_measurement_offset_seconds(stage, 1) == interval * 60
        assert base.scheduled_measurement_offset_seconds(stage, 2) == interval * 120


def test_short_run_really_has_one_stage_one_cycle():
    raw = base.load_si6_config(TEMPLATES / "short_attended.yaml")
    raw["workflow"]["initial_stage"]["plateau_stopping_enabled"] = raw["three_instrument"]["initial_plateau_stopping_enabled"]
    stages = base.build_stages(raw["workflow"])
    assert len(stages) == 1
    assert stages[0].measure_immediately and stages[0].max_measurements == 1
    assert not stages[0].plateau_stopping_enabled


def test_scans_are_one_global_resolved_setting_for_all_stages(tmp_path, monkeypatch):
    monkeypatch.delenv("NMR_DEFAULT_SCANS", raising=False)
    raw = deepcopy(base.load_si6_config(TEMPLATES / "reaction_mixed_intervals.yaml"))
    raw["nmr"]["scans"] = 4
    path = tmp_path / "four_scans.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    raw, _, _, nmr = integrated.prepare(
        path, config.REPO_ROOT / "configs/machines/00_machine.local.yaml",
        config.REPO_ROOT / "arduino/configs/arduino.local.yaml", mock=True,
    )
    received = []
    services = integrated.Services.__new__(integrated.Services)
    services.nmr_cfg = nmr
    # Every stage dispatches through the same service-held acquisition settings.
    services.acquire = lambda settings, directory, *, label: received.append(settings.scans)
    for stage in base.build_stages(raw["workflow"]):
        services.acquire(services.nmr_cfg, tmp_path, label=stage.name)
    assert received == [4, 4, 4, 4]
    assert all("nmr" not in stage for stage in raw["workflow"]["repeating_stages"])

    raw["workflow"]["initial_stage"]["nmr"] = {"scans": 8}
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="Unknown .*nmr"):
        base.load_si6_config(path)
