"""Exercise the real serial drivers against their existing in-memory fakes."""
import json

import pytest

from arduino.python.errors import PositionUncertainError
from arduino.python.needle_state import NeedleStateStore
from chemyx_lab.workflows import stage2_demo_instruments as instruments


class Clock:
    def __init__(self):
        self.seconds = 0.0

    def sleep(self, seconds):
        self.seconds += seconds


@pytest.fixture
def cfg():
    return {
        "arduino": {"port": "COM3", "baud_rate": 115200, "state_path": "runs/arduino/needle_state.json", "steps_per_unit": 200, "up_step_sign": 1, "min_position": -3, "max_position": 5, "home_position": 0, "up_position": 1, "speed_steps_s": 100, "acceleration_steps_s2": 500},
        "chemyx": {"port": "COM4", "baud_rate": 115200, "timeout_seconds": 2, "response_delay_seconds": 0.2, "syringe_diameter_mm": 20, "syringe_capacity_ml": 20, "safety_margin_ml": 1, "sampling_channel": 1, "initial_volume_ml": 0, "pre_sample_volume_ml": 8, "sample_volume_ml": 5, "sample_rate_ml_min": 5, "return_rate_ml_min": 5, "wash_volume_ml": 5, "wash_rate_ml_min": 5, "settle_seconds": 10, "extra_seconds": 2},
        "channel_2": {"enabled": False, "channel": 2, "volume_ml": 1.86, "rate_ml_min": 1, "initial_volume_ml": 5, "syringe_diameter_mm": 20, "syringe_capacity_ml": 20, "safety_margin_ml": 1, "delay_after_time_zero_seconds": 180},
    }


def test_proven_sample_sequence_uses_actual_mock_drivers(cfg, tmp_path):
    clock, messages, nmr_at = Clock(), [], []
    with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=clock, emit=messages.append) as rig:
        rig.prepare()
        def acquire():
            nmr_at.append((rig.needle.state.logical_position, dict(rig.retained), clock.seconds))
            return {"status": "NMR_SAVED"}
        result = rig.sample(acquire)
        assert result["status"] == "NMR_SAVED"
        tx = list(rig.pump.ser.tx_log)
        assert [line.strip() for line in tx if "set volume" in line] == ["1 set volume -8.0", "1 set volume -5.0", "1 set volume 13.0", "1 set volume -5.0", "1 set volume 5.0"]
        assert [line.strip() for line in tx if "start" in line] == ["1 start 0"] * 5
        assert not any(line.startswith("2 ") for line in tx)
        assert nmr_at == [(0, {1: 13, 2: 5}, 170)]
        assert rig.retained == {1: 0, 2: 5}
        assert rig.needle.state.logical_position == 1
        assert clock.seconds == 452
        assert not any(line.split(maxsplit=1)[-1] == "HOME" for line in rig.controller.transport.tx_log)
        assert not rig.needle.store.home_pending_path.exists()
    assert messages.index("Arduino: Moving needle DOWN / HOME") < messages.index("Arduino: Moving needle UP")


def test_channel_two_is_explicit_and_returns_needle_up(cfg, tmp_path):
    cfg["channel_2"]["enabled"] = True
    with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=Clock(), emit=lambda _: None) as rig:
        rig.prepare()
        rig.dose()
        tx = list(rig.pump.ser.tx_log)
        assert [line.strip() for line in tx if "set volume" in line] == ["2 set volume 1.86"]
        assert [line.strip() for line in tx if "start" in line] == ["2 start 0"]
        assert rig.retained[1] == 0
        assert rig.retained[2] == pytest.approx(3.14)
        assert rig.needle.state.logical_position == 1


def test_no_pump_ledger_or_historical_file_is_read(cfg, tmp_path, monkeypatch):
    paths, load = [], NeedleStateStore.load
    def observed(store):
        paths.append(store.path.resolve())
        return load(store)
    monkeypatch.setattr(NeedleStateStore, "load", observed)
    with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=Clock(), emit=lambda _: None) as rig:
        rig.prepare()
        rig.sample(lambda: {"status": "NMR_FAILED"})
    assert set(paths) == {(tmp_path / "mock_state/needle_state.json").resolve()}


def test_nmr_exception_still_completes_cleanup(cfg, tmp_path):
    with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=Clock(), emit=lambda _: None) as rig:
        rig.prepare()
        def failed():
            raise TimeoutError("NMR connection")
        assert rig.sample(failed)["status"] == "NMR_FAILED"
        assert rig.retained[1] == 0
        assert rig.needle.state.logical_position == 1


def test_uncertain_pump_start_is_never_repeated_and_blocks_motion(cfg, tmp_path, monkeypatch):
    with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=Clock(), emit=lambda _: None) as rig:
        rig.prepare()
        start = rig.pump.start
        def uncertain(*args, **kwargs):
            start(*args, **kwargs)
            raise TimeoutError("START reply lost")
        monkeypatch.setattr(rig.pump, "start", uncertain)
        with pytest.raises(TimeoutError, match="START reply lost"):
            rig.sample(lambda: pytest.fail("Acquisition must not run"))
        before = list(rig.pump.ser.tx_log)
        with pytest.raises(instruments.PhysicalInstrumentError, match="uncertain"):
            rig.move_needle(0)
        assert rig.pump.ser.tx_log == before
        assert sum("start 0" in line for line in before) == 1
        assert rig.needle.state.logical_position == 1
        assert not rig.pump.ser.channel_states[1]["running"]


def test_failed_needle_does_not_start_pump(cfg, tmp_path, monkeypatch):
    with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=Clock(), emit=lambda _: None) as rig:
        def failed(_):
            raise PositionUncertainError("JOG interrupted")
        monkeypatch.setattr(rig.needle, "move_to", failed)
        with pytest.raises(PositionUncertainError):
            rig.prepare()
        assert not any("start" in line for line in rig.pump.ser.tx_log)
        with pytest.raises(instruments.PhysicalInstrumentError):
            rig.transfer(1, -1, 5, "Must not run")


@pytest.mark.parametrize("section,key,value", [("chemyx", "sampling_channel", 2), ("channel_2", "channel", 1), ("chemyx", "sample_volume_ml", 15), ("chemyx", "sample_rate_ml_min", 0), ("chemyx", "initial_volume_ml", 1), ("arduino", "home_position", 1), ("arduino", "up_step_sign", 0)])
def test_invalid_physical_settings_rejected_before_connect(cfg, section, key, value):
    cfg[section][key] = value
    with pytest.raises(ValueError):
        instruments.validate_physical_config(cfg)


def test_capacity_guard_prevents_dispatch(cfg, tmp_path):
    with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=Clock(), emit=lambda _: None) as rig:
        rig.prepare()
        before = list(rig.pump.ser.tx_log)
        with pytest.raises(instruments.PhysicalInstrumentError):
            rig.transfer(1, -20, 5, "Over capacity")
        assert rig.pump.ser.tx_log == before


def test_official_reference_not_redefined_or_pending_marker_deleted(cfg, tmp_path):
    with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=Clock(), emit=lambda _: None) as rig:
        rig.prepare()
        state = rig.needle.store.load()
        assert state.home_position == 0 and state.reason == "motion_completed"
        assert state.steps_per_unit == 200 and state.up_step_sign == 1
        reference = json.loads(rig.needle.store.path.read_text())
        assert reference["logical_position"] == 1
        rig.needle.store.home_pending_path.write_text("pending")
        with pytest.raises(PositionUncertainError, match="incomplete"):
            instruments.TrackedNeedle(rig.controller, {"needle": {"min_position": -3, "max_position": 5, "steps_per_unit": 200, "up_step_sign": 1, "state_path": str(rig.needle.store.path)}, "motion": {"maximum_speed_steps_s": 100, "maximum_acceleration_steps_s2": 500}})
        assert rig.needle.store.home_pending_path.read_text() == "pending"


@pytest.mark.parametrize("port", ["com3", " COM3 ", r"\\.\COM3"])
def test_shared_serial_port_rejected_before_open(cfg, tmp_path, monkeypatch, port):
    cfg["chemyx"]["port"] = port
    monkeypatch.setattr(instruments, "controller_session", lambda *args, **kwargs: pytest.fail("Must validate before serial open"))
    with pytest.raises(ValueError, match="different serial ports"):
        with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=Clock(), emit=lambda _: None):
            pass


@pytest.mark.parametrize("action,bad_reply", [("start", "garbled"), ("start", "pump stop"), ("stop", "garbled"), ("stop", "pump start")])
def test_unrelated_pump_ack_does_not_authorize_more_motion(cfg, tmp_path, monkeypatch, action, bad_reply):
    with instruments.setup_instruments(cfg, tmp_path, mock=True, clock=Clock(), emit=lambda _: None) as rig:
        rig.prepare()
        original = getattr(rig.pump, action)
        def wrong_reply(*args, **kwargs):
            original(*args, **kwargs)
            return bad_reply
        monkeypatch.setattr(rig.pump, action, wrong_reply)
        with pytest.raises(instruments.PhysicalInstrumentError, match="uncertain"):
            rig.transfer(1, -1, 5, "Test transfer")
        assert rig.uncertain
        before = list(rig.pump.ser.tx_log)
        assert sum("start 0" in line for line in before) == 1
        with pytest.raises(instruments.PhysicalInstrumentError, match="uncertain"):
            rig.move_needle(0)
        assert rig.pump.ser.tx_log == before
