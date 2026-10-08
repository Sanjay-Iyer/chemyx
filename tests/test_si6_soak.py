"""Focused offline checks of the thin real-soak reuse layer."""
from datetime import datetime, timedelta, timezone
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
import yaml

from chemyx_lab import config
from chemyx_lab.analysis import si6_final_qc as qc
from chemyx_lab.workflows import si6_automated_nmr as base, three_instrument_si6 as si6
from chemyx_lab.workflows import si6_soak as soak
from chemyx_lab.testing.si6_synthetic_analysis import metadata_carrier

SOURCE = config.REPO_ROOT / "configs/experiments/si6_real_toluene_soak_test.yaml"
ARDUINO = config.REPO_ROOT / "arduino/configs/arduino_real_COM3.yaml"


class Clock:
    def __init__(self):
        self.seconds = 0.0

    def now(self):
        return self.seconds

    def sleep(self, label, seconds):
        self.seconds += seconds


def configured(tmp_path, *, iterations=3, trigger=2, interval=10, hours=2, enabled=True):
    raw = yaml.safe_load(SOURCE.read_text())
    raw["soak_test"].update(iterations=iterations, interval_minutes=interval, max_hours=hours)
    raw["soak_test"]["channel2"].update(trigger_iteration=trigger, enabled=enabled)
    raw["output"]["run_root_dir"] = str(tmp_path / "runs")
    path = tmp_path / "soak.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


def setup_services(s, clock, monkeypatch, *, render=False):
    s.sleep = clock.sleep
    def acquire(nmr, directory, *, label):
        source = directory / (label + ".dx")
        metadata_carrier(source, datetime(2026, 10, 7, tzinfo=timezone.utc) + timedelta(seconds=clock.now()))
        return source
    def process(source, paths, dataset):
        output = paths.run_dir / "processed_nmr" / source.stem
        audit = output / "phase_audit" / source.stem
        audit.mkdir(parents=True)
        # Controlled processor output with no candidates, consumed by the real
        # moving-area helper. This tests orchestration, not a second algorithm.
        np.savez_compressed(audit / "spectral_evidence.npz", region_ppm=np.linspace(5, 6.5, 100), regional_quantitative=np.zeros(100))
        (audit / "processing_metadata.json").write_text(json.dumps({
            "raw_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "peak_candidates": [], "phase": {"method": "acme", "p0_deg": 0, "p1_deg": 0},
        }))
        return output
    s.acquire, s.process = acquire, process
    original = si6.tracked_peak_area_observation
    def flagged(*args, **kwargs):
        row = original(*args, **kwargs)
        row.update(qc_pass=False, low_snr_warning=True, high_noise_warning=True)
        return row
    monkeypatch.setattr(si6, "tracked_peak_area_observation", flagged)
    def no_endpoint(*args, **kwargs):
        pytest.fail("Soak called chemistry endpoint logic")
    monkeypatch.setattr(si6, "completion_evidence", no_endpoint)
    monkeypatch.setattr(qc, "completion_evidence", no_endpoint)
    if not render:
        monkeypatch.setattr(soak, "write_final_qc", lambda *args, **kwargs: [])


def session(path, tmp_path):
    return si6.open_services(*si6.prepare(path, tmp_path / "no_machine.yaml", ARDUINO, mock=True, allow_soak=True), identity=si6.RunIdentity("soak", True))


def records(s):
    return [json.loads(line) for line in s.paths.journal_jsonl.read_text().splitlines()]


@pytest.mark.parametrize("iterations,trigger,interval,hours", [(3, 2, 10, 2), (10, 5, 60, 12), (20, 10, 60, 24)])
def test_count_missing_peak_and_channel2_once(tmp_path, monkeypatch, iterations, trigger, interval, hours):
    clock = Clock()
    with session(configured(tmp_path, iterations=iterations, trigger=trigger, interval=interval, hours=hours), tmp_path) as s:
        setup_services(s, clock, monkeypatch)
        outcome = soak.run_soak(s, monotonic_fn=clock.now)
        with s.paths.time_series_csv.open(newline="") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == iterations
        assert all(row["peak_area"] == "0.0" and row["peak_found"] == "False" and not row["peak_ppm"] for row in rows)
        assert all(row["plateau"] == "False" and row["stage"] == soak.STAGE for row in rows)
        assert rows[trigger-1]["channel2_action"] == "CONFIRMED"
        assert sum(row["channel2_action"] == "CONFIRMED" for row in rows) == 1
        assert all(row["timestamp_source"] == "LONG DATE header" for row in rows)
        events = records(s)
        returns = [e for e in events if e["event_type"] == "pump_needle_context" and e.get("channel") == 1 and e.get("operation_type") == "infuse" and e.get("requested_volume_ml") == 13]
        assert len(returns) == iterations and all(e["needle_state"] == "UP" for e in returns)
        transfers = [e for e in events if e.get("channel") == 2 and e.get("operation_type") == "infuse" and e.get("lifecycle_state") == "completed"]
        assert len(transfers) == 1
        assert transfers[0]["requested_parameters"]["volume"] == .5
        assert sum(e["event_type"] == "cycle_completed" for e in events) == iterations
        assert s.dose_guard.data["confirmation_iteration"] == trigger
        assert outcome.status is base.TerminalStatus.COMPLETED


@pytest.mark.parametrize("enabled,hours,expected", [(True, .4, 1), (False, 2, 3)])
def test_duration_first_or_channel_disabled(tmp_path, monkeypatch, enabled, hours, expected):
    clock = Clock()
    with session(configured(tmp_path, hours=hours, enabled=enabled, interval=60 if enabled else 10), tmp_path) as s:
        setup_services(s, clock, monkeypatch)
        outcome = soak.run_soak(s, monotonic_fn=clock.now)
        summary = json.loads((s.paths.run_dir / "soak_summary.json").read_text())
        assert summary["recorded_observations"] == expected
        assert summary["termination_reason"] == ("maximum_duration" if enabled else "iteration_count")
        assert s.dose_guard.data["status"] == "RESERVED"
        assert not any(e["event_type"] == "dose_confirmed" for e in records(s))
        assert outcome.status is base.TerminalStatus.COMPLETED


@pytest.mark.parametrize("failure", ["processor", "pump", "interrupt", "channel2"])
def test_operational_failures_propagate_and_stop(tmp_path, monkeypatch, failure):
    clock = Clock()
    with session(configured(tmp_path), tmp_path) as s:
        setup_services(s, clock, monkeypatch)
        calls = []
        if failure == "processor":
            def fail_process(*args):
                raise RuntimeError("processor crashed")
            s.process = fail_process
        else:
            move = s.pump_move
            def fail_move(*args, **kwargs):
                if failure != "channel2" or kwargs.get("channel") == 2:
                    calls.append(kwargs.get("channel"))
                    raise KeyboardInterrupt() if failure == "interrupt" else RuntimeError("pump link failed")
                return move(*args, **kwargs)
            s.pump_move = fail_move
        expected = si6.MeasurementFailedAfterCleanup if failure == "processor" else KeyboardInterrupt if failure == "interrupt" else RuntimeError
        with pytest.raises(expected):
            soak.run_soak(s, monotonic_fn=clock.now)
        assert s.dose_guard.data["status"] == ("DISPATCH_INTENT" if failure == "channel2" else "RESERVED")
        assert not any(e["event_type"] == "dose_confirmed" for e in records(s))
        assert all(state.last_stop_status is base.StopStatus.SUCCEEDED for state in s.states.values())
        if failure == "channel2":
            assert calls == [2]
            with s.paths.time_series_csv.open(newline="") as handle:
                rows = list(csv.DictReader(handle))
            assert rows[-1]["channel2_action"] == "DISPATCH_INTENT"


def test_shared_plots_have_soak_dataset_titles_and_no_endpoint(tmp_path, monkeypatch):
    clock = Clock()
    seen = []
    save = qc.save_figure
    def capture(fig, stem, dataset, title, manifest):
        result = save(fig, stem, dataset, title, manifest)
        seen.append((result, fig._suptitle.get_text(), dataset))
        return result
    monkeypatch.setattr(qc, "save_figure", capture)
    with session(configured(tmp_path, iterations=1, trigger=1), tmp_path) as s:
        setup_services(s, clock, monkeypatch, render=True)
        assert soak.run_soak(s, monotonic_fn=clock.now).status is base.TerminalStatus.COMPLETED
        report = json.loads((s.paths.run_dir / "final_qc/qc_summary.json").read_text())
        assert report["plots_available"] and report["stages"][0]["endpoint"] is None
        manifest = json.loads((s.paths.run_dir / "final_qc/plot_manifest.json").read_text())
        assert len(manifest["figures"]) == 12
        assert {Path(f["path"]).suffix for f in manifest["figures"]} == {".png", ".svg", ".pdf"}
        assert all(title == visible and title.startswith(dataset + " ") for title, visible, dataset in seen)
        assert all(f["dataset_display_name"] == s.dataset_name and f["visible_title"].startswith(s.dataset_name + " ") for f in manifest["figures"])


def test_config_ports_commands_and_chemistry_separation(tmp_path):
    for port in ("COM4", "COM6"):
        raw, arduino, pump, nmr = si6.prepare(SOURCE, config.REPO_ROOT / f"configs/machines/si6_real_{port}.yaml", ARDUINO, mock=True, allow_soak=True)
        assert pump.port == port and arduino["arduino"]["port"] == "COM3" and nmr.scans == 8
        assert not base.build_stages(raw["workflow"])[0].completion
    with pytest.raises(ValueError, match="Unknown Si6"):
        base.load_si6_config(SOURCE) # Chemistry entry points refuse a soak config.
    invalid = yaml.safe_load(SOURCE.read_text())
    invalid["soak_test"]["channel2"]["trigger_iteration"] = 11
    with pytest.raises(ValueError, match="trigger_iteration"):
        soak.configure_soak(invalid)
    invalid = yaml.safe_load(SOURCE.read_text())
    invalid["soak_test"]["channel2"]["volume_ml"] = 3
    with pytest.raises(ValueError, match="infuses more"):
        soak.configure_soak(invalid)
    guide = (config.REPO_ROOT / "docs/REAL_INSTRUMENT_RUN_COMMANDS.md").read_text(encoding="utf-8")
    assert "$si6MachineCfg" not in guide and ".venv" not in guide and "Activate" not in guide
    commands = [line for line in guide.splitlines() if line.startswith("python ")]
    assert len(commands) == 11
    for line in commands:
        tokens = line.split()
        assert (config.REPO_ROOT / tokens[1]).is_file()
        for option in ("--workflow-config", "--machine-config", "--arduino-config", "--config"):
            if option in tokens:
                assert (config.REPO_ROOT / tokens[tokens.index(option)+1]).is_file()
