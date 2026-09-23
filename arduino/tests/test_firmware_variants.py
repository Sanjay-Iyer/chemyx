from pathlib import Path


def test_one_active_runtime_configured_firmware():
    firmware_root = Path(__file__).resolve().parents[1] / "firmware"
    sketches = list(firmware_root.rglob("*.ino"))
    assert sketches == [firmware_root / "needle_controller" / "needle_controller.ino"]

    source = sketches[0].read_text(encoding="utf-8")
    assert "COMMERCIAL_RUNTIME_CONFIG" not in source
    assert 'const char *DEVICE_NAME = "needle_controller";' in source
    assert 'const char *FIRMWARE_VERSION = "1.1.0";' in source
    for command in (
        "CONFIG_IO",
        "CONFIG_LIMITS",
        "CONFIG_APPLY",
        "STOP",
        "PING",
        "STATUS",
        "ENABLE",
        "DISABLE",
        "HOME",
        "JOG",
        "MOVE_ABS",
    ):
        assert f'"{command}"' in source
