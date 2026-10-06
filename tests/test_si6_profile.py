"""Independent admission regression: enough points for EVERY confirmation."""
import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.workflows import three_instrument_si6 as si6


def nominal():
    return yaml.safe_load((config.REPO_ROOT / "config_templates/experiments/si6_two_stage_nominal.yaml").read_text())


def prepare_raw(raw, tmp_path):
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return si6.prepare(path, tmp_path / "absent_machine.yaml",
                       config.REPO_ROOT / "arduino/configs/arduino.example.yaml", mock=True)


@pytest.mark.parametrize("stage_name", ["initial_stage", "first_addition_stage"])
@pytest.mark.parametrize("slots", [8, 9, 10])
def test_each_confirmation_window_requires_minimum_observations(tmp_path, stage_name, slots):
    raw = nominal()
    stage = raw["workflow"][stage_name]
    stage["max_hours"] = slots * stage["interval_minutes"] / 60
    # With min8 and confirmations3, earliest ends are8,9,10. Duration-driven
    # admission must reject8/9 available slots before any transport opens.
    if slots < 10:
        with pytest.raises(ValueError, match="too few measurement slots"):
            prepare_raw(raw, tmp_path)
    else:
        prepared, *_ = prepare_raw(raw, tmp_path)
        stages = si6.base.build_stages(prepared["workflow"])
        selected = next(item for item in stages if item.name == stage["name"])
        assert selected.max_measurements == 10


@pytest.mark.parametrize("slots", [8, 9])
def test_window_size_can_dominate_confirmation_admission(tmp_path, slots):
    raw = nominal()
    stage = raw["workflow"]["first_addition_stage"]
    stage["completion"].update(minimum_points=2, minimum_duration_hours=1,
                               window_points=7, consecutive_confirmations=3)
    stage["max_hours"] = slots * .5
    # First complete7-point window ends7; three overlapping windows end7,8,9.
    if slots < 9:
        with pytest.raises(ValueError, match="too few measurement slots"):
            prepare_raw(raw, tmp_path)
    else:
        prepared, *_ = prepare_raw(raw, tmp_path)
        assert si6.base.build_stages(prepared["workflow"])[1].max_measurements == 9
