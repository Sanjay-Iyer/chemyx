"""Focused standalone checkout acceptance tests; all device access is mocked."""
from dataclasses import replace
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from arduino.python.needle_state import NeedleState, NeedleStateStore
from chemyx_lab import config
from chemyx_lab.instruments.chemyx import Pump
from chemyx_lab.instruments import nmr as rpc
from chemyx_lab.testing.mock_serial import MockChemyxSerial
from chemyx_lab.workflows import simple_instrument_checkout as checkout
from chemyx_lab.workflows import instrument_operations as operations
from chemyx_lab.workflows import chemyx_position as position
from chemyx_lab.analysis.synthetic_phase_benchmark import write_jcamp
from chemyx_lab.analysis.nmr import read_jcamp_fid


@pytest.fixture
def rig(tmp_path, monkeypatch):
    raw, arduino, settings, nmr = checkout.load_settings()
    monkeypatch.setenv(position.STATE_DIR_ENV, str(tmp_path / "state"))
    settings = replace(settings, dose_ledger_dir=tmp_path / "doses")
    settings.dose_ledger_dir.mkdir()
    (settings.dose_ledger_dir / "existing_reaction.json").write_text(
        '{"experiment_id":"existing_reaction","status":"CONFIRMED","volume_ml":1.86}\n')
    needle_path = tmp_path / "needle.json"
    arduino["needle"]["state_path"] = str(needle_path)
    saved = NeedleState(logical_position=0, position_valid=True, reason="operator_confirmed_home",
                        last_updated=datetime.now(timezone.utc).isoformat(), steps_per_unit=200, up_step_sign=1)
    NeedleStateStore(needle_path).save(saved)
    root = position.state_root(False)
    with position.PositionLedger(root, owner="test fixture").acquire() as ledger:
        for ch in (1, 2):
            ledger.set_home(settings.channels[ch].identity(settings.pump_identity(False)),
                            receipt_id=f"original_home_{ch}", established_by="operator test fixture")
        before = {ch: ledger.state(ch) for ch in (1, 2)}
    controller_session = checkout.controller_session
    monkeypatch.setattr(checkout, "controller_session", lambda cfg, mode, **kwargs:
                        controller_session(cfg, "mock", **kwargs))
    ports = []

    def serial_factory(**kwargs):
        port = MockChemyxSerial(**kwargs)
        ports.append(port)
        return port

    monkeypatch.setattr(checkout.manual, "make_pump", lambda settings, mock:
                        Pump(port=settings.port, response_delay=0, serial_factory=serial_factory, mock=True))
    monkeypatch.setattr(checkout.manual, "sleep_with_progress", lambda *_: None)
    monkeypatch.setattr(checkout, "NmrRpcClient", lambda *_: SimpleNamespace(ping=lambda: {"connected": True}))
    acquire = checkout.acquire_nmr
    monkeypatch.setattr(checkout, "acquire_nmr", lambda nmr, directory, mock: acquire(nmr, directory, mock=True))

    def process(dx, output, dataset):
        path = output / "nmr_processed" / "mock_processing"
        path.mkdir()
        return path

    actual_process = checkout.process_nmr
    monkeypatch.setattr(checkout, "process_nmr", process)
    prompts = []
    def answer(prompt):
        prompts.append(prompt)
        return "y"
    return SimpleNamespace(raw=raw, arduino=arduino, settings=settings, nmr=nmr, root=root,
                           needle_path=needle_path, before=before, ports=ports, prompts=prompts,
                           answer=answer, output=tmp_path / "result", actual_process=actual_process)


def run(rig, **kwargs):
    return checkout.run_checkout(rig.raw, rig.arduino, rig.settings, rig.nmr,
                                 mock=False, output=rig.output, input_fn=kwargs.pop("input_fn", rig.answer), **kwargs)


def test_defaults_and_com6():
    raw, arduino, settings, nmr = checkout.load_settings()
    assert arduino["arduino"]["port"] == "COM3" and settings.port == "COM4"
    for ch in (1, 2):
        assert settings.channels[ch].transfer_volume_ml == 1
        assert settings.channels[ch].rate_ml_min == 10
        assert settings.channels[ch].diameter_mm == 20
        assert settings.channels[ch].capacity_ml == 20
    assert settings.channels[2].syringe_volume_at_home_ml == 5
    assert (nmr.route, nmr.result_type, nmr.scans) == ("iflow", "fid", 2)
    assert (nmr.receiver_gain, nmr.auto_gain, nmr.spectral_center, nmr.sweep_width) == (12, False, 5, 20)
    assert checkout.load_settings(machine_config="configs/machines/si6_real_COM6.yaml")[2].port == "COM6"


@pytest.mark.parametrize("start", [0, 1])
def test_arduino_up_down_home_restores_known_start(rig, start):
    store = NeedleStateStore(rig.needle_path)
    store.save(replace(store.load(), logical_position=start))
    report = run(rig)
    assert report["overall"] == "PASS"
    assert store.load().logical_position == start
    assert store.load().home_position == 0 and store.load().position_valid
    records = [json.loads(line) for line in (rig.output / "operation_journal.jsonl").read_text().splitlines()]
    assert [e["target"] for e in records if e["event_type"] == "instrument_checkout_needle_request"] == [1, 0, 0, start]
    assert all(report["checks"][f"Arduino {name}"] == "PASS" for name in ("UP", "DOWN", "HOME", "restore"))
    assert len(rig.prompts) == 1


def test_both_pumps_independent_exact_transfers_fill_restoration_and_home(rig):
    report = run(rig)
    assert report["overall"] == "PASS"
    with position.PositionLedger(rig.root, owner="restart verify").acquire() as ledger:
        for ch, fill in ((1, [0, 1, 0]), (2, [5, 6, 5])):
            end = ledger.state(ch)
            assert end["position_state"] == "KNOWN"
            assert end["current_displacement_nl"] == rig.before[ch]["current_displacement_nl"] == 0
            assert end["home"] == rig.before[ch]["home"]
            assert end["configuration_identity"] == rig.before[ch]["configuration_identity"]
            assert end["tracked_since_home"] == {"withdrawn_nl": position.NL_PER_ML,
                                                  "infused_nl": position.NL_PER_ML, "movements": 2}
            assert report["positions"][f"channel_{ch}"]["retained_volume_ml"] == fill
    tx = rig.ports[0].tx_log
    assert [line.strip() for line in tx if "start" in line] == ["1 start 0", "1 start 0", "2 start 0", "2 start 0"]
    assert all(not rig.ports[0].channel_states[ch]["running"] for ch in (1, 2))
    volume_commands = [line.split() for line in tx if "set volume" in line]
    assert [(int(words[0]), float(words[-1])) for words in volume_commands] == [(1, -1), (1, 1), (2, -1), (2, 1)]
    assert all("set rate 10" in line for line in tx if "set rate" in line)


def test_restore_nonzero_existing_pump_displacement(rig):
    with position.PositionLedger(rig.root, owner="preceding confirmed move").acquire() as ledger:
        for ch in (1, 2):
            tracker = ledger.tracker(channel=ch, pump=rig.settings.pump_identity(False), diameter_mm=20,
                                     capacity_ml=20, source="test preceding move", run_id="previous")
            token = tracker.begin("withdraw", 0.5, operation_id=f"previous_{ch}")
            assert tracker.complete(token)
            rig.before[ch] = ledger.state(ch)
    report = run(rig)
    assert report["overall"] == "PASS"
    assert report["positions"]["channel_1"]["retained_volume_ml"] == [0.5, 1.5, 0.5]
    assert report["positions"]["channel_2"]["retained_volume_ml"] == [5.5, 6.5, 5.5]


def test_production_dose_records_and_recipe_unchanged(rig):
    recipe = config.REPO_ROOT / "configs/experiments/si6_real_two_stage.yaml"
    before = recipe.read_bytes(), checkout.dose_snapshot(rig.settings.dose_ledger_dir)
    assert run(rig)["overall"] == "PASS"
    assert before == (recipe.read_bytes(), checkout.dose_snapshot(rig.settings.dose_ledger_dir))
    assert len(list(rig.settings.dose_ledger_dir.iterdir())) == 1


@pytest.mark.parametrize("status", ["RESERVED", "DISPATCH_INTENT", "UNREADABLE"])
def test_active_dose_guard_blocks_before_any_motor_movement(rig, status):
    (rig.settings.dose_ledger_dir / "active.json").write_text(json.dumps({"status": status}))
    saved = rig.needle_path.read_bytes()
    report = run(rig)
    assert report["overall"] == "FAILED"
    assert not rig.ports and rig.needle_path.read_bytes() == saved
    assert report["checks"]["NMR two-scan acquisition"] == "NOT RUN"


def test_decline_no_reset_or_connections(rig):
    saved = rig.needle_path.read_bytes()
    report = run(rig, input_fn=lambda _: "n")
    assert report["overall"] == "INCOMPLETE"
    assert not rig.ports and rig.needle_path.read_bytes() == saved


def test_unknown_needle_requires_existing_home_recovery_no_guess(rig):
    store = NeedleStateStore(rig.needle_path)
    store.save(replace(store.load(), position_valid=False))
    before = rig.needle_path.read_bytes()
    report = run(rig)
    assert report["overall"] == "FAILED" and not rig.ports
    assert "UNKNOWN" in report["attention"][0]
    assert rig.needle_path.read_bytes() == before


def test_unconfirmed_pump_command_fails_without_compensating_infuse(rig, monkeypatch):
    execute = checkout.manual.execute_move
    def fail(pump, *args, **kwargs):
        original = pump.start
        pump.start = lambda *a, **k: (_ for _ in ()).throw(OSError("mock lost START"))
        try:
            return execute(pump, *args, **kwargs)
        finally:
            pump.start = original
    monkeypatch.setattr(checkout.manual, "execute_move", fail)
    report = run(rig)
    assert report["overall"] == "FAILED"
    assert report["checks"]["Chemyx Channel 1 infuse"] == "NOT RUN"
    with position.PositionLedger(rig.root, owner="after failure").acquire() as ledger:
        assert ledger.state(1)["position_state"] == "UNKNOWN"
        assert ledger.state(2)["current_displacement_nl"] == 0


def test_nmr_acquisition_failure_cannot_pass(rig, monkeypatch):
    monkeypatch.setattr(checkout, "acquire_nmr", lambda *a, **k: (_ for _ in ()).throw(TimeoutError("mock acquisition timeout")))
    report = run(rig)
    assert report["overall"] == "FAILED"
    assert report["checks"]["NMR raw saved"] == "NOT RUN"
    assert report["checks"]["Chemyx Channel 2 restored"] == "PASS"


def test_scientific_processing_unavailable_keeps_acquisition_pass(rig, monkeypatch):
    monkeypatch.setattr(checkout, "process_nmr", lambda *a: (_ for _ in ()).throw(checkout.base.AnalysisInconclusiveError("blank spectrum: no signal to phase")))
    report = run(rig)
    assert report["overall"] == "PASS"
    assert report["checks"]["NMR processing"] == "UNAVAILABLE"
    assert report["checks"]["NMR two-scan acquisition"] == "PASS"
    assert list((rig.output / "nmr_raw").glob("*.dx"))


def test_real_acquisition_helper_dispatches_exactly_two_scans_and_saves_raw(rig, monkeypatch):
    requests = []
    class FakeRpc:
        base_url = "mock://NMR"
        def __init__(self, *a): pass
        def iflow_1d_settings(self): return rpc.example_iflow_1d_settings()
        def iflow_experiment_settings(self): return rpc.example_iflow_experiment_settings()
        def set_iflow_1d_settings(self, value): requests.append(value)
        def run_iflow_experiment(self, value): requests.append(value)
        def iflow_experiment_status(self): return {}
        def wait_for_idle(self, **kwargs):
            return {"ResultCode": 0, "NumberOfScansRun": 2, "JDX_FileContents_FD": checkout.MOCK_FID.read_text()}
    monkeypatch.setattr(operations, "NmrRpcClient", FakeRpc)
    raw_dir = rig.output / "nmr_raw"
    raw_dir.mkdir(parents=True)
    path = operations.run_nmr_acquisition(rig.nmr, raw_dir, label="test")
    assert path.is_file() and path.stat().st_size > 0
    assert requests[1]["NumberOfScans"] == 2
    assert requests[1]["ReceiverGain"] == 12
    assert requests[1]["SpectralCentreInPpm"] == 5
    assert requests[1]["SpectralWidthInPpm"] == 20
    assert requests[0]["AutoGain"] is False


def test_real_fft_acme_accepts_zero_fid_without_chemical_peak_gate(tmp_path, rig, monkeypatch):
    dx = tmp_path / "blank.dx"
    write_jcamp(dx, np.zeros(1024, dtype=complex), dict(acquired_points=1024, processed_points=4096,
                spectral_width_hz=1250, observe_frequency_mhz=60.55, center_ppm=5))
    assert not any(read_jcamp_fid(dx).complex_points)
    def acquire(nmr, directory, **kwargs):
        target = directory / "blank_2scan.dx"
        target.write_bytes(dx.read_bytes())
        return target
    monkeypatch.setattr(checkout, "acquire_nmr", acquire)
    monkeypatch.setattr(checkout, "process_nmr", rig.actual_process)
    # Full checkout, real FFT/ACME and an all-zero FID: no chemical result gate.
    report = run(rig)
    assert report["overall"] == "PASS"
    assert report["checks"]["NMR two-scan acquisition"] == "PASS"
    assert report["checks"]["NMR raw saved"] == "PASS"
    assert report["checks"]["NMR processing"] in {"PASS", "UNAVAILABLE"}
    assert report["chemical_analysis"] == {"required": False, "status": "NOT EVALUATED",
                                           "reason": "No liquid enters the NMR; no peak, solvent, integration or reaction criteria"}


def test_saved_rpc_error_is_not_falsely_reported_as_raw_fid_success(rig, monkeypatch):
    def acquire(nmr, directory, **kwargs):
        path = directory / "rpc_error.dx"
        path.write_text('{"ResultCode":0,"error":"no FID returned"}')
        return path
    monkeypatch.setattr(checkout, "acquire_nmr", acquire)
    report = run(rig)
    assert report["overall"] == "FAILED" and report["checks"]["NMR raw saved"] == "NOT RUN"


def test_existing_plot_path_receives_checkout_dataset_title(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(checkout.base, "run_process_fid_postprocessing",
        lambda dx, paths, dataset, **kwargs: calls.append((paths, dataset, kwargs)) or paths.processed_dir)
    checkout.process_nmr(tmp_path / "fid.dx", tmp_path, "CHECKOUT-001")
    assert calls[0][1] == "CHECKOUT-001" and calls[0][2]["phase_method"] == "acme"
    # Reuse the existing rendering path; inspect visible figure titles, not filenames.
    import importlib.util
    monkeypatch.syspath_prepend(str(checkout.base.PROCESS_FID_SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("checkout_plot_test", checkout.base.PROCESS_FID_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    from matplotlib import pyplot as plt
    titles = []
    original = plt.close
    def close(fig=None):
        if hasattr(fig, "axes"):
            titles.extend(ax.get_title() for ax in fig.axes)
        original(fig)
    monkeypatch.setattr(plt, "close", close)
    module._plot_real(np.linspace(0, 10, 100), np.zeros(100), tmp_path / "blank.png",
                     "Full Spectrum", (0, 10), (), zoom=False, dataset_display_name="CHECKOUT-001")
    assert titles and all(title.startswith("CHECKOUT-001 ") for title in titles)
