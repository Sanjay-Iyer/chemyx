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


def test_checked_in_yaml_templates_have_no_duplicate_keys():
    # PyYAML keeps the last duplicate silently, so a commissioned value typed
    # into an earlier copy of a key would be discarded without warning.
    import yaml

    class StrictLoader(yaml.SafeLoader):
        pass

    def unique_mapping(loader, node, deep=False):
        keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
        duplicates = sorted({str(key) for key in keys if keys.count(key) > 1})
        if duplicates:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key(s) {duplicates}", node.start_mark
            )
        return loader.construct_mapping(node, deep)

    StrictLoader.add_constructor(
        yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
    )
    root = config.REPO_ROOT
    templates = [
        path
        for path in (*root.glob("configs/**/*.yaml"), *root.glob("arduino/configs/*.example.yaml"))
        if ".local." not in path.name
    ]
    templates.extend(root.glob("config_templates/*.yaml"))
    assert templates
    for path in templates:
        yaml.load(path.read_text(encoding="utf-8"), Loader=StrictLoader)


def test_new_laptop_yaml_templates_load_with_their_runtime_parsers(tmp_path):
    from shutil import copyfile

    from arduino.python.config import load_arduino_config
    from arduino.python.discovery import PortInfo, resolve_arduino_port

    root = config.REPO_ROOT / "config_templates"
    copied_arduino = tmp_path / "arduino.local.yaml"
    copyfile(root / "arduino.local.template.yaml", copied_arduino)
    arduino = load_arduino_config(copied_arduino)
    integrated = load_arduino_config(root / "integrated_hello_world.local.template.yaml")
    machine = config.load_machine_config(root / "00_machine.local.template.yaml")
    analysis = config.read_mapping_config(root / "analysis.local.template.yaml", "NMR local template")

    assert arduino["arduino"]["port"] == "COM3"
    assert arduino["arduino"]["fingerprint"] == {
        "vid": 0x2341,
        "pid": 0x0069,
        "serial_number": None,
        "manufacturer": None,
    }
    selected = resolve_arduino_port(
        arduino["arduino"]["port"],
        arduino["arduino"]["fingerprint"],
        ports=[PortInfo(device="COM3", vid=0x2341, pid=0x0069)],
    )
    assert selected.device == "COM3"
    assert arduino["needle"]["steps_per_unit"] is None
    assert arduino["needle"]["up_step_sign"] is None
    assert integrated["integrated"]["machine_config_path"] == "configs/machines/00_machine.local.yaml"
    assert integrated["arduino"]["fingerprint"] == arduino["arduino"]["fingerprint"]
    assert machine.chemyx.serial_port == "COM6"
    assert machine.nmr.host == "169.254.30.54"
    assert analysis["input"]["paths"] == ["results/raw/nmr/06-09-26"]
