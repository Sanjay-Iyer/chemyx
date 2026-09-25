from pathlib import Path


def test_one_active_runtime_configured_firmware():
    firmware_root = Path(__file__).resolve().parents[1] / "firmware"
    sketches = list(firmware_root.rglob("*.ino"))
    assert sketches == [firmware_root / "needle_controller" / "needle_controller.ino"]

    source = sketches[0].read_text(encoding="utf-8")
    assert "COMMERCIAL_RUNTIME_CONFIG" not in source
    assert 'const char *DEVICE_NAME = "needle_controller";' in source
    assert 'const char *FIRMWARE_VERSION = "1.2.1";' in source
    for command in (
        "CONFIG_IO",
        "CONFIG_LIMITS",
        "CONFIG_APPLY",
        "STOP",
        "PING",
        "IDENTITY",
        "STATUS",
        "ENABLE",
        "DISABLE",
        "HOME",
        "JOG",
        "MOVE_ABS",
    ):
        assert f'"{command}"' in source


def test_usb_ready_is_connection_edge_triggered_and_nonblocking():
    source = (Path(__file__).resolve().parents[1] / "firmware" / "needle_controller" / "needle_controller.ino").read_text(encoding="utf-8")
    setup = source.split("void setup() {", 1)[1].split("void loop() {", 1)[0]
    loop = source.split("void loop() {", 1)[1]
    assert "Serial.begin(115200);" in setup
    assert "printReadyIdentity()" not in setup
    assert "const unsigned long USB_HOST_SETTLE_MS = 250UL;" in source
    assert "bool connected = (bool)Serial;" in source
    assert "connected && !serialHostConnected" in source
    assert "millis() - serialHostConnectedAtMs >= USB_HOST_SETTLE_MS" in source
    assert "connected && !readyAnnouncedForHost" in source
    assert "serialHostConnected = false;" in source
    assert loop.index("serviceSerialConnection();") < loop.index("serviceSerial();")


def test_motion_pins_and_pulse_caps_are_unchanged():
    source = (Path(__file__).resolve().parents[1] / "firmware" / "needle_controller" / "needle_controller.ino").read_text(encoding="utf-8")
    for line in (
        "const uint8_t STEP_PIN = 3;",
        "const uint8_t DIR_PIN = 4;",
        "const unsigned long ABSOLUTE_MAX_SPEED_STEPS_S = 100UL;",
        "const unsigned long STEP_HIGH_US = 5000UL;",
        "const unsigned long MIN_STEP_INTERVAL_US = 10000UL;",
        "setDriverEnabled(false);",
    ):
        assert line in source
