import json
import pytest

from chemyx_lab import config


def test_load_pump_config_defaults_without_local_json():
    settings = config.load_pump_config(config_path="missing-chemyx-local.json")

    assert settings.port == ""
    assert settings.baud_rate == 115200
    assert settings.channel == 0
    assert settings.units == 0
    assert settings.diameter == 4.5
    assert settings.rate == 1.0
    assert settings.volume == 0.5


def test_load_pump_config_merges_local_json_then_overrides(tmp_path):
    cfg = tmp_path / "chemyx.local.json"
    cfg.write_text(
        json.dumps(
            {
                "com_port": "FAKE_CHEMYX_PORT",
                "baud": 9600,
                "pump_channel": 2,
                "flow_units": "uL/min",
                "syringe_diameter_mm": 10.3,
                "default_rate": 4.5,
                "default_volume": 0.25,
                "read_delay": 0.4,
            }
        ),
        encoding="utf-8",
    )

    settings = config.load_pump_config(
        cfg,
        channel=1,
        rate=0.5,
    )

    assert settings.port == "FAKE_CHEMYX_PORT"
    assert settings.baud_rate == 9600
    assert settings.channel == 1
    assert settings.units == 2
    assert settings.diameter == 10.3
    assert settings.rate == 0.5
    assert settings.volume == 0.25
    assert settings.response_delay == 0.4


def test_load_nmr_settings_defaults_to_unset_host():
    settings = config.load_nmr_settings(config_path="missing-nmr-local.json")

    assert settings.host == ""
    assert settings.port == 5000
    assert settings.route == "iflow"
    assert settings.scans == 2
    assert settings.receiver_gain == 12.0
    assert settings.auto_gain is False


def test_load_nmr_settings_merges_local_json_then_overrides(tmp_path):
    cfg = tmp_path / "nmr.local.json"
    cfg.write_text(
        json.dumps(
            {
                "ip_address": "nmr-one.example",
                "NumberOfScans": 16,
                "ReceiverGain": 14,
                "autoGain": True,
            }
        ),
        encoding="utf-8",
    )

    settings = config.load_nmr_settings(
        cfg,
        scans=8,
        receiver_gain=12,
    )

    assert settings.host == "nmr-one.example"
    assert settings.scans == 8
    assert settings.receiver_gain == 12.0
    assert settings.auto_gain is True


def test_load_machine_config_reads_yaml(tmp_path):
    cfg = tmp_path / "machine.yaml"
    cfg.write_text(
        "\n".join(
            [
                "chemyx:",
                "  serial_port: FAKE_MACHINE_PORT",
                "  baud_rate: 115200",
                "nmr:",
                "  host: nmr-machine.example",
                "  port: 5000",
                "valve:",
                "  serial_port: FAKE_VALVE_PORT",
                "  baud_rate: 19200",
                "  positions: 2",
            ]
        ),
        encoding="utf-8",
    )

    settings = config.load_machine_config(cfg)

    assert settings.chemyx.serial_port == "FAKE_MACHINE_PORT"
    assert settings.nmr.host == "nmr-machine.example"
    assert settings.valve.serial_port == "FAKE_VALVE_PORT"
    assert settings.valve.baud_rate == 19200
    assert settings.valve.positions == 2


def test_machine_config_rejects_unknown_nested_key(tmp_path):
    cfg = tmp_path / "machine.yaml"
    cfg.write_text(
        "\n".join(
            [
                "chemyx:",
                "  serial_port: FAKE_MACHINE_PORT",
                "  mystery: true",
            ]
        ),
        encoding="utf-8",
    )

    with pytest.raises(config.ConfigError, match="mystery"):
        config.load_machine_config(cfg)
