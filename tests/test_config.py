import json

from chemyx_lab import config


def test_load_nmr_settings_defaults_to_archived_working_ip():
    settings = config.load_nmr_settings(config_path="missing-nmr-local.json")

    assert settings.host == "169.254.30.54"
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
                "ip_address": "10.10.1.50",
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

    assert settings.host == "10.10.1.50"
    assert settings.scans == 8
    assert settings.receiver_gain == 12.0
    assert settings.auto_gain is True
