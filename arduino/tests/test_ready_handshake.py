"""Hardware-free checks of the USB connection edge and strict host handshake."""

import pytest

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.python.controller import NeedleController
from arduino.python.errors import IdentityMismatch
from arduino.python.protocol import parse_response
from arduino.python.workflows import HardDeadline, run_test_01


def drain(transport: FakeArduinoTransport) -> list[str]:
    lines = []
    while (line := transport.read_line(0)) is not None:
        lines.append(line)
    return lines


def test_host_present_when_firmware_starts_announces_ready_once():
    fake = FakeArduinoTransport()
    fake.open()
    first = drain(fake)
    assert first == [
        "READY device=needle_controller board=uno_r4_minima version=1.2.1",
        "EVENT HOST_CONNECTED",
    ]
    for _ in range(100):
        fake.service_serial_connection(True)
    assert drain(fake) == []


def test_running_firmware_announces_again_after_host_reconnect():
    fake = FakeArduinoTransport()
    fake.service_serial_connection(False)  # firmware running with no host
    fake.open()
    assert sum(line.startswith("READY ") for line in drain(fake)) == 1
    fake.close()
    fake.open()
    second = drain(fake)
    assert sum(line.startswith("READY ") for line in second) == 1
    assert parse_response(second[1]).kind == "EVENT"
    assert not fake.enabled and not fake.moving


class DelayedReadyTransport(FakeArduinoTransport):
    """The host is open before the firmware notices its CDC connection."""

    def open(self) -> None:
        super().open()
        self.service_serial_connection(False)
        self._rx.clear()
        self._announce_on_read = True

    def read_line(self, timeout_s: float) -> str | None:
        if self._announce_on_read:
            self._announce_on_read = False
            self.service_serial_connection(True)
        return super().read_line(timeout_s)


def test_controller_accepts_ready_after_delayed_host_connection():
    fake = DelayedReadyTransport()
    with NeedleController(fake, expected_version="1.2.1") as controller:
        assert controller.identity["version"] == "1.2.1"
        controller.ping()
        assert controller.status()["enabled"] == "false"
        assert [event.detail for event in controller.events] == ["HOST_CONNECTED"]


def test_identity_query_is_read_only_and_matches_ready():
    fake = FakeArduinoTransport(runtime_configurable=True)
    with NeedleController(fake, expected_version="1.2.1") as controller:
        before = (fake.enabled, fake.moving, fake.runtime_configured, fake.position_steps)
        identity = controller.identify()
        assert identity == {
            "device": "needle_controller",
            "board": "uno_r4_minima",
            "version": "1.2.1",
            "driver": "DM542S",
        }
        assert before == (fake.enabled, fake.moving, fake.runtime_configured, fake.position_steps)
        assert fake.tx_log == ["1 IDENTITY"]


def test_identity_query_rejects_device_change_after_ready():
    fake = FakeArduinoTransport()
    with NeedleController(fake, expected_version="1.2.1") as controller:
        fake.device = "other_device"
        with pytest.raises(IdentityMismatch):
            controller.identify()


def test_test1_works_uncommissioned_without_any_motor_command():
    fake = FakeArduinoTransport(runtime_configurable=True)
    with NeedleController(fake, expected_version="1.2.1") as controller:
        final = run_test_01(controller, HardDeadline(3))
    assert final["led"] == "off"
    assert not fake.enabled and not fake.moving and not fake.motion_commissioned
    assert {line.split()[1] for line in fake.tx_log} <= {"PING", "STATUS", "LED", "BLINK"}
