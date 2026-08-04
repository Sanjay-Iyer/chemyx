from pathlib import Path

import pytest

from arduino.mock.fake_arduino import FakeArduinoTransport
from arduino.mock.fake_instruments import FakeNmrClient
from arduino.python.controller import NeedleController
from arduino.python.errors import PositionUncertainError
from arduino.python.workflows import HardDeadline, run_test_02, run_test_04a
from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.testing.mock_serial import MockChemyxSerial


def test_motion_error_attempts_stop_and_leaves_position_uncertain(base_config):
    cfg = base_config
    cfg["motion"].update({"test_02_steps": 10, "test_02_speed_steps_s": 100})
    cfg["firmware"]["motion_enabled"] = True
    cfg["driver"]["enable_active_low"] = True
    fake = FakeArduinoTransport(scenario="movement_timeout")
    fake.motion_commissioned = True
    with NeedleController(fake, allow_motion=True, command_timeout_s=0.05) as controller:
        with pytest.raises(PositionUncertainError):
            run_test_02(controller, cfg, HardDeadline(20), sleep_fn=lambda _: None)
        assert any(" STOP" in command for command in fake.tx_log)
        assert controller.position_certain is False


def test_cleanup_does_not_assume_physical_position():
    fake = FakeArduinoTransport(scenario="movement_timeout")
    with NeedleController(fake, allow_motion=True, command_timeout_s=0.05) as controller:
        with pytest.raises(PositionUncertainError):
            controller.jog(10, 100)
        assert not any("MOVE_ABS" in command for command in fake.tx_log)


def test_test4a_performs_no_physical_action():
    serial_instances = []

    def serial_factory(**kwargs):
        instance = MockChemyxSerial(**kwargs)
        serial_instances.append(instance)
        return instance

    pump = Pump(serial_factory=serial_factory, response_delay=0)
    nmr = FakeNmrClient()
    arduino = FakeArduinoTransport()
    with NeedleController(arduino) as controller, pump:
        report = run_test_04a(controller, pump, nmr, HardDeadline(1))
    assert report["physical_actions"] == []
    assert not pump.ser
    assert nmr.calls == ["ping"]
    assert [line.split()[1] for line in arduino.tx_log] == ["PING", "STATUS"]
    assert serial_instances[0].tx_log == ["help\r"]


def test_production_instrument_modules_are_reused():
    source = (Path(__file__).resolve().parents[1] / "python" / "workflows.py").read_text(encoding="utf-8")
    assert "from chemyx_lab.instruments.chemyx import Pump" in source
    assert "run_nmr_acquisition" in source
