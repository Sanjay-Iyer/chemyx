import sys
from pathlib import Path

from chemyx_lab.config import NmrSettings, PumpConfig


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from chemyx_lab.workflows import first_real_chemyx_nmr as first_real_test  # noqa: E402


class RecordingPump:
    def __init__(self):
        self.calls = []

    def set_volume(self, volume):
        self.calls.append(("set_volume", volume))
        return f"volume = {volume}"

    def start(self, delay=None):
        self.calls.append(("start", delay))
        return f"start {delay}"

    def stop(self):
        self.calls.append(("stop", None))
        return "stop"

    def set_units(self, units):
        self.calls.append(("set_units", units))
        return f"units = {units}"

    def set_diameter(self, diameter):
        self.calls.append(("set_diameter", diameter))
        return f"diameter = {diameter}"

    def set_rate(self, rate):
        self.calls.append(("set_rate", rate))
        return f"rate = {rate}"


def _pump_config(rate: float = 5.0) -> PumpConfig:
    return PumpConfig(
        port="FAKE_PORT",
        baud_rate=115200,
        channel=1,
        units=0,
        diameter=28.6,
        rate=rate,
        volume=5.0,
        timeout=2.0,
        response_delay=0.2,
    )


def test_first_real_withdraw_command_sequence_preserves_baseline():
    pump = RecordingPump()

    first_real_test.run_metered_move(
        pump,
        _pump_config(),
        "withdraw",
        5.0,
        extra_seconds=2.0,
        mock=True,
    )

    assert pump.calls == [
        ("set_volume", -5.0),
        ("start", 0),
        ("stop", None),
    ]


def test_first_real_infuse_command_sequence_preserves_baseline():
    pump = RecordingPump()

    first_real_test.run_metered_move(
        pump,
        _pump_config(),
        "infuse",
        5.0,
        extra_seconds=2.0,
        mock=True,
    )

    assert pump.calls == [
        ("set_volume", 5.0),
        ("start", 0),
        ("stop", None),
    ]


def test_first_real_setup_command_sequence_preserves_baseline():
    pump = RecordingPump()

    first_real_test.configure_pump(pump, _pump_config())

    assert pump.calls == [
        ("set_units", 0),
        ("set_diameter", 28.6),
        ("set_rate", 5.0),
    ]


class RecordingNmrClient:
    calls = []

    def __init__(self, rpc_config):
        self.base_url = rpc_config.base_url

    def iflow_1d_settings(self):
        self.calls.append(("iflow_1d_settings", None))
        return {"ReceiverGain": 1.0, "AutoGain": True, "ExportFilename": ""}

    def iflow_experiment_settings(self):
        self.calls.append(("iflow_experiment_settings", None))
        return {
            "NumberOfScans": 1,
            "ReceiverGain": 1.0,
            "SpectralCentreInPpm": 5.0,
            "SpectralWidthInPpm": 20.0,
        }

    def set_iflow_1d_settings(self, settings):
        self.calls.append(("set_iflow_1d_settings", settings))
        return {"ok": True}

    def run_iflow_experiment(self, settings):
        self.calls.append(("run_iflow_experiment", settings))
        return {"ok": True}

    def iflow_experiment_status(self):
        self.calls.append(("iflow_experiment_status", None))
        return "##TITLE=offline"

    def wait_for_idle(self, status_getter=None):
        self.calls.append(("wait_for_idle", getattr(status_getter, "__name__", None)))
        return status_getter()


def _nmr_settings() -> NmrSettings:
    return NmrSettings(
        host="fake-host",
        port=5000,
        scheme="http",
        timeout=10.0,
        poll_seconds=2.0,
        max_wait_seconds=300.0,
        route="iflow",
        experiment="1D",
        scans=2,
        receiver_gain=12.0,
        auto_gain=False,
        solvent="Toluene",
        spectral_center=5.0,
        sweep_width=20.0,
        result_name="DataSet",
        result_type="spectrum",
        save_dir=Path("unused"),
        target_ppm=6.1,
        peak_window_ppm=0.12,
    )


def test_first_real_iflow_rpc_sequence_preserves_baseline(tmp_path, monkeypatch):
    RecordingNmrClient.calls = []
    monkeypatch.setattr(first_real_test, "NmrRpcClient", RecordingNmrClient)

    first_real_test.run_nmr_acquisition(_nmr_settings(), tmp_path, label="offline")

    assert [name for name, _ in RecordingNmrClient.calls] == [
        "iflow_1d_settings",
        "iflow_experiment_settings",
        "set_iflow_1d_settings",
        "run_iflow_experiment",
        "wait_for_idle",
        "iflow_experiment_status",
    ]
    assert RecordingNmrClient.calls[2][1]["ReceiverGain"] == 12.0
    assert RecordingNmrClient.calls[3][1]["NumberOfScans"] == 2
