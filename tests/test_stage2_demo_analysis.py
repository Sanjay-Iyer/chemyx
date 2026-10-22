"""Instrument-free monitoring, validated math, authoritative time and titles."""
from copy import deepcopy
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest
import yaml

from chemyx_lab.analysis import stage2_demo_analysis as analysis
from chemyx_lab.analysis.scipy_peak_comparison import choose_target, detection_context, evaluate


@pytest.fixture
def spectrum(tmp_path):
    x = np.linspace(5.3, 6.3, 10001)
    y = 100 * np.exp(-.5 * ((x - 5.79) / .025) ** 2) + 2 * x
    return {"path": tmp_path / "stage_2_0001.dx", "ppm": x, "intensity": y, "noise": 1.,
            "timestamp": datetime(2026, 10, 20, tzinfo=timezone.utc), "timestamp_source": "LONG DATE header",
            "timestamp_value": "2026/10/20 00:00:00+0000", "raw_sha256": "synthetic-test", "phase_audit": {}, "cached": False}


def write_config(path, **changes):
    cfg = {"experiment": {"name": "test_demo"}, "analysis": {"dataset_display_name": " TEST   DATA ", **changes}}
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def test_primary_area_and_boundaries_match_existing_approved_algorithm(spectrum):
    settings = analysis.detection_settings({})
    assert settings.prominence_snr == 8
    context = detection_context(spectrum["ppm"], spectrum["intensity"], spectrum["noise"], settings)
    peak, status, flags = choose_target(context, settings)
    expected = evaluate(context, peak, status, flags, "width_95", settings, fixed_area=float("nan"))
    row = analysis.measure([spectrum], settings)[0]
    assert row["area"] == expected["area_dynamic"]
    assert row["boundary_low_ppm"] == expected["boundary_low_ppm"]
    assert row["boundary_high_ppm"] == expected["boundary_high_ppm"]
    assert row["analysis_quality_pass"]
    assert row["primary_area_method"] == "scipy_width95_prominence8"


def test_missing_peak_stays_nan_and_analysis_completes(spectrum):
    spectrum["intensity"] *= .001
    row = analysis.measure([spectrum], analysis.detection_settings({}))[0]
    assert row["detection_status"] == "missing"
    assert math.isnan(row["area"])
    assert row["analysis_status"] == "COMPLETE"
    assert not row["analysis_quality_pass"]


@pytest.mark.parametrize("target", [{"prominence_snr": 7}, {"rel_height": .9}, {"extra": 1},
                                      {"search_low": 6.1}, {"distance_ppm": -1}])
def test_invalid_algorithm_settings_fail_in_child_only(target):
    with pytest.raises(ValueError):
        analysis.detection_settings({"target_peak": target})


def test_metadata_timing_prefers_long_date_and_never_filename_or_mtime():
    analysis.pipeline()
    stamp, source = analysis.authoritative_time({"LONG DATE": "2026/10/20 10:04:31-0400", "$DATE": "1"})
    assert stamp.isoformat() == "2026-10-20T10:04:31-04:00"
    assert source == "LONG DATE header"
    with pytest.raises(ValueError, match="No authoritative"):
        analysis.authoritative_time({})


@pytest.mark.parametrize("prefix", ["stage_1", "stage_2", "observation", "check"])
def test_cumulative_table_and_every_plot_have_actual_dataset_title(tmp_path, spectrum, prefix):
    spectrum["path"] = tmp_path / f"{prefix}_0001.dx"
    second = deepcopy(spectrum)
    second["path"] = tmp_path / f"{prefix}_0002.dx"
    second["timestamp"] = datetime(2026, 10, 20, 0, 15, 40, tzinfo=timezone.utc)
    second["intensity"] *= .001
    rows = analysis.measure([spectrum, second], analysis.detection_settings({}))
    manifest, errors = analysis.render([spectrum, second], rows, tmp_path, "TEST DATA", {}, "test-processing")
    analysis.write_csv(tmp_path / "peak_areas.csv", rows)
    assert not errors
    assert len(manifest) == 5
    assert rows[1]["elapsed_hours"] == pytest.approx(940 / 3600)
    for item in manifest:
        assert item["visible_title"].startswith("TEST DATA ")
        assert "TEST DATA TEST DATA" not in item["visible_title"]
        assert item["dataset"] == "TEST DATA"
        with Image.open(item["figure"]) as png:
            assert png.info["Title"] == item["visible_title"]
            assert png.info["Dataset"] == item["dataset"]
    with (tmp_path / "peak_areas.csv").open(newline="", encoding="utf-8") as handle:
        saved = list(csv.DictReader(handle))
    assert float(saved[1]["elapsed_hours"]) == rows[1]["elapsed_hours"]
    assert math.isnan(float(saved[1]["area"]))


@pytest.mark.parametrize("prefix", ["stage_1", "stage_2", "observation", "check"])
def test_each_raw_filename_is_discovered_with_its_rpc_variant_and_exact_source(tmp_path, prefix):
    for name in (f"{prefix}_001.dx", f"{prefix}_001.rpc.dx", f"{prefix}_002.dx", "unrelated.dx"):
        (tmp_path / name).write_text("raw file", encoding="utf-8")
    groups = analysis.raw_groups(tmp_path, {}, tmp_path)
    assert list(groups) == [1, 2]
    assert [path.name for path in groups[1]] == [f"{prefix}_001.rpc.dx", f"{prefix}_001.dx"]
    assert [path.name for path in groups[2]] == [f"{prefix}_002.dx"]


def test_bad_raw_file_is_isolated_from_following_spectrum(tmp_path, spectrum, monkeypatch):
    run = tmp_path / "run"
    raw = run / "nmr_raw"
    raw.mkdir(parents=True)
    for name in ("stage_2_0001.dx", "stage_2_0002.dx"):
        (raw / name).write_text("untouched raw test", encoding="utf-8")
    cfg = write_config(tmp_path / "config.yaml", save_spectrum_plots=False, save_area_vs_time=False)
    monkeypatch.setattr(analysis, "processing_setup", lambda _: (None, None, "test", {}))
    def fake_process(path, *_):
        if path.name.endswith("0001.dx"):
            raise ValueError("bad FID")
        result = deepcopy(spectrum)
        result["path"] = path
        return result
    monkeypatch.setattr(analysis, "process_raw", fake_process)
    summary = analysis.analyze(run, cfg)
    assert summary["status"] == "PARTIAL"
    assert summary["successful_analyses"] == 1
    assert summary["failed_analyses"] == 1
    assert "bad FID" in summary["errors"][0]["error"]
    assert all(path.read_text() == "untouched raw test" for path in raw.glob("*.dx"))


def test_existing_outputs_are_not_startup_requirements_and_corrupt_cache_is_rebuilt(tmp_path, spectrum, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    raw = tmp_path / "stage_2_0001.dx"
    raw.write_text("raw content", encoding="utf-8")
    (cache / (raw.stem + ".npz")).write_text("broken cache", encoding="utf-8")
    phased = SimpleNamespace(ppm_axis=spectrum["ppm"], metadata={"LONG DATE": "2026/10/20 00:00:00+0000"}, phase_audit={})
    calls = []
    monkeypatch.setattr(analysis, "build_phased_spectrum", lambda *a, **kw: calls.append(kw) or phased)
    production = SimpleNamespace(process_spectrum_for_peaks=lambda *_: (spectrum["intensity"], None, SimpleNamespace(noise=1.)))
    args = SimpleNamespace(line_broadening_hz=.03, zero_fill_points=65536, truncation_window="none")
    first = analysis.process_raw(raw, cache, production, args, "processing")
    second = analysis.process_raw(raw, cache, production, args, "processing")
    assert len(calls) == 1
    assert calls[0]["phase_method"] == "acme"
    assert not first["cached"] and second["cached"]
    assert np.array_equal(first["intensity"], second["intensity"])
    raw.write_text("new raw content", encoding="utf-8")
    analysis.process_raw(raw, cache, production, args, "processing")
    assert len(calls) == 2


def test_plot_failure_is_an_analysis_warning_not_raw_loss(tmp_path, spectrum, monkeypatch):
    rows = analysis.measure([spectrum], analysis.detection_settings({}))
    monkeypatch.setattr(analysis, "save_png", lambda *_: (_ for _ in ()).throw(OSError("disk error")))
    _, errors = analysis.render([spectrum], rows, tmp_path, "TEST DATA", {}, "test")
    assert errors and "disk error" in errors[0]["error"]
    assert rows[0]["analysis_status"] == "PLOT_FAILED"


def test_actual_fid_production_processing_and_cache_preserve_raw_and_titles(tmp_path, monkeypatch):
    import shutil
    source = Path(__file__).resolve().parents[1] / "results/raw/nmr/06-09-26/CEC-PhSi2-flow(sequence-1000)-06-09-26.dx"
    if not source.is_file():
        pytest.skip("Local real-FID fixture unavailable")
    run = tmp_path / "real_review"
    raw = run / "nmr_raw"
    raw.mkdir(parents=True)
    saved = raw / "stage_2_0001.dx"
    shutil.copyfile(source, saved)
    digest = hashlib.sha256(saved.read_bytes()).hexdigest()
    cfg = write_config(tmp_path / "config.yaml")
    result = analysis.analyze(run, cfg)
    assert result["successful_analyses"] == 1
    assert result["failed_analyses"] == 0
    assert result["dataset"] == "TEST DATA"
    with (run / "analysis/peak_areas.csv").open(newline="", encoding="utf-8") as handle:
        row = next(csv.DictReader(handle))
    assert row["timestamp_source"] == "LONG DATE header"
    assert row["acquisition_time"].startswith("2026-06-09T10:07:43")
    assert float(row["area"]) > 0
    assert row["primary_area_method"] == "scipy_width95_prominence8"
    with (run / "analysis/plot_manifest.csv").open(newline="", encoding="utf-8") as handle:
        manifest = list(csv.DictReader(handle))
    assert len(manifest) == 3
    for item in manifest:
        with Image.open(item["figure"]) as png:
            assert png.info["Title"] == item["visible_title"]
            assert item["visible_title"].startswith("TEST DATA ")
    monkeypatch.setattr(analysis, "build_phased_spectrum", lambda *a, **kw: pytest.fail("Earlier FID rephased despite cache"))
    second = analysis.analyze(run, cfg)
    assert second["cached_spectra"] == 1
    assert hashlib.sha256(saved.read_bytes()).hexdigest() == digest
    assert hashlib.sha256(source.read_bytes()).hexdigest() == digest


def test_cli_failure_does_not_require_or_modify_hardware(tmp_path, capsys):
    cfg = write_config(tmp_path / "config.yaml", phase_method="invalid")
    assert analysis.main(["--run-dir", str(tmp_path / "run"), "--config", str(cfg)]) == 1
    assert "raw data preserved" in capsys.readouterr().err


def watch_run(tmp_path, status="RUNNING", every_n=1):
    run = tmp_path / "run"
    (run / "nmr_raw").mkdir(parents=True)
    (run / "summary.json").write_text(json.dumps({"status": status, "nmr_successful": 1}), encoding="utf-8")
    cfg = write_config(tmp_path / "config.yaml", every_n_acquisitions=every_n)
    return run, cfg


def test_watch_terminal_forces_partial_frequency_group_and_merges_only_analysis_counts(tmp_path, monkeypatch):
    run, cfg = watch_run(tmp_path, "COMPLETE", every_n=3)
    (run / "nmr_raw/stage_2_0001.dx").write_text("raw", encoding="utf-8")
    calls = []
    def process(*args, **kwargs):
        calls.append(args)
        return {"successful_analyses": 1, "failed_analyses": 0}
    monkeypatch.setattr(analysis, "analyze", process)
    monkeypatch.setattr(analysis, "parent_alive", lambda _: True)
    assert analysis.watch(run, cfg, 123) == 0
    assert len(calls) == 1
    result = json.loads((run / "summary.json").read_text())
    assert result == {"status": "COMPLETE", "nmr_successful": 1, "analyses_successful": 1,
                      "analyses_failed": 0, "analyses_pending": 0, "analysis_complete": True}


def test_watch_parent_gone_drains_and_exits_without_waiting(tmp_path, monkeypatch):
    run, cfg = watch_run(tmp_path)
    calls = []
    monkeypatch.setattr(analysis, "parent_alive", lambda _: False)
    monkeypatch.setattr(analysis, "analyze", lambda *a, **kw: calls.append(a) or {"successful_analyses": 0, "failed_analyses": 0})
    monkeypatch.setattr(analysis.time, "sleep", lambda _: pytest.fail("Waiting after parent exited"))
    assert analysis.watch(run, cfg, 123) == 0
    assert len(calls) == 1
    assert json.loads((run / "summary.json").read_text())["status"] == "RUNNING"


def test_watch_failed_batch_recovers_on_next_saved_spectrum_and_never_edits_running_summary(tmp_path, monkeypatch):
    run, cfg = watch_run(tmp_path)
    (run / "nmr_raw/stage_2_0001.dx").write_text("raw one", encoding="utf-8")
    calls = []
    def process(*args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise RuntimeError("temporary processing error")
        return {"successful_analyses": 2, "failed_analyses": 0}
    def advance(_):
        current = json.loads((run / "summary.json").read_text())
        assert "analysis_complete" not in current  # no controller status write while running
        assert current["status"] == "RUNNING"
        (run / "nmr_raw/stage_2_0002.dx").write_text("raw two", encoding="utf-8")
        current["status"] = "COMPLETE"
        (run / "summary.json").write_text(json.dumps(current), encoding="utf-8")
    monkeypatch.setattr(analysis, "parent_alive", lambda _: True)
    monkeypatch.setattr(analysis, "analyze", process)
    monkeypatch.setattr(analysis.time, "sleep", advance)
    assert analysis.watch(run, cfg, 123) == 0
    assert len(calls) == 2
    assert json.loads((run / "summary.json").read_text())["analyses_successful"] == 2


def test_parent_liveness_check_does_not_signal_windows_processes(monkeypatch):
    if os.name == "nt":
        monkeypatch.setattr(os, "kill", lambda *a: pytest.fail("Windows process received os.kill"))
    assert analysis.parent_alive(os.getpid())
    assert not analysis.parent_alive(2147483000)


def test_watch_frequency_batches_catch_up_without_delaying_raw_collection(tmp_path, monkeypatch):
    run, cfg = watch_run(tmp_path, every_n=2)
    raw = run / "nmr_raw"
    (raw / "stage_2_0001.dx").write_text("raw 1", encoding="utf-8")
    counts = []
    def process(*args, **kwargs):
        count = len(list(raw.glob("*.dx")))
        counts.append(count)
        return {"successful_analyses": count, "failed_analyses": 0}
    def advance(_):
        count = len(list(raw.glob("*.dx"))) + 1
        (raw / f"stage_2_{count:04d}.dx").write_text(f"raw {count}", encoding="utf-8")
        if count == 4:
            (run / "summary.json").write_text('{"status":"COMPLETE"}', encoding="utf-8")
    monkeypatch.setattr(analysis, "parent_alive", lambda _: True)
    monkeypatch.setattr(analysis, "analyze", process)
    monkeypatch.setattr(analysis.time, "sleep", advance)
    assert analysis.watch(run, cfg, 123) == 0
    assert counts == [2, 4]
    assert len(list(raw.glob("*.dx"))) == 4


@pytest.mark.parametrize("primary,rpc,reported,selected", [
    ("partial", "valid", None, "stage_2_0001.rpc.dx"),
    ("valid", "valid", "rpc", "stage_2_0001.rpc.dx"),
    ("valid", "valid", "primary", "stage_2_0001.dx"),
    ("valid", "partial", None, "stage_2_0001.dx"),
])
def test_primary_and_rpc_variants_produce_one_observation_with_reported_path_preferred(
        tmp_path, spectrum, monkeypatch, primary, rpc, reported, selected):
    run, cfg = watch_run(tmp_path, "COMPLETE")
    raw = run / "nmr_raw"
    (raw / "stage_2_0001.dx").write_text(primary, encoding="utf-8")
    (raw / "stage_2_0001.rpc.dx").write_text(rpc, encoding="utf-8")
    (raw / "stage_2_0001.foreign.dx").write_text("ignored", encoding="utf-8")
    if reported:
        reported_path = raw / ("stage_2_0001.rpc.dx" if reported == "rpc" else "stage_2_0001.dx")
        (run / "summary.json").write_text(json.dumps({"status": "COMPLETE", "observations": [
            {"status": "NMR_SAVED", "observation": 1, "path": str(reported_path)}]}), encoding="utf-8")
    write_config(cfg, save_spectrum_plots=False, save_area_vs_time=False)
    monkeypatch.setattr(analysis, "processing_setup", lambda _: (None, None, "test", {}))
    def process(path, *_):
        if path.read_text() == "partial":
            raise ValueError("partial FID")
        result = deepcopy(spectrum)
        result["path"] = path
        return result
    monkeypatch.setattr(analysis, "process_raw", process)
    for _ in range(2):
        result = analysis.analyze(run, cfg)
        assert result["raw_files"] == 1
        assert result["successful_analyses"] == 1 and result["failed_analyses"] == 0
        with (run / "analysis/peak_areas.csv").open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert len(rows) == 1
        assert Path(rows[0]["source_file"]).name == selected
        assert rows[0]["observation"] == "observation_0001"


def test_watch_complete_no_spectra_exits_even_if_parent_remains_alive(tmp_path, monkeypatch):
    run, cfg = watch_run(tmp_path, "COMPLETE_NO_SPECTRA")
    monkeypatch.setattr(analysis, "parent_alive", lambda _: True)
    monkeypatch.setattr(analysis, "analyze", lambda *a, **kw: {"successful_analyses": 0, "failed_analyses": 0})
    monkeypatch.setattr(analysis.time, "sleep", lambda _: pytest.fail("Waiting after COMPLETE_NO_SPECTRA"))
    assert analysis.watch(run, cfg, 123) == 0
    assert json.loads((run / "summary.json").read_text())["status"] == "COMPLETE_NO_SPECTRA"


def test_watch_frequency_counts_unique_observations_rather_than_rpc_variants(tmp_path, monkeypatch):
    run, cfg = watch_run(tmp_path, every_n=2)
    raw = run / "nmr_raw"
    (raw / "stage_2_0001.dx").write_text("raw primary", encoding="utf-8")
    (raw / "stage_2_0001.rpc.dx").write_text("raw rpc", encoding="utf-8")
    calls = []
    def advance(_):
        assert not calls  # two variants are only one acquired observation
        (raw / "stage_2_0002.dx").write_text("second observation", encoding="utf-8")
        (run / "summary.json").write_text('{"status":"COMPLETE"}', encoding="utf-8")
    monkeypatch.setattr(analysis, "parent_alive", lambda _: True)
    monkeypatch.setattr(analysis, "analyze", lambda *a, **kw: calls.append(a) or {"successful_analyses": 2, "failed_analyses": 0})
    monkeypatch.setattr(analysis.time, "sleep", advance)
    assert analysis.watch(run, cfg, 123) == 0
    assert len(calls) == 1


def test_watch_retries_changed_admitted_raw_before_next_acquisition(tmp_path, monkeypatch):
    run, cfg = watch_run(tmp_path)
    raw = run / "nmr_raw/stage_2_0001.dx"
    raw.write_text("partial", encoding="utf-8")
    calls, sleeps = [], []
    def process(*args, **kwargs):
        value = raw.read_text()
        calls.append(value)
        return {"successful_analyses": int(value == "complete raw spectrum"),
                "failed_analyses": int(value != "complete raw spectrum")}
    def advance(_):
        sleeps.append(None)
        if len(sleeps) == 1:
            raw.write_text("complete raw spectrum", encoding="utf-8")
        else:
            assert calls[:2] == ["partial", "complete raw spectrum"]
            (run / "summary.json").write_text('{"status":"COMPLETE"}', encoding="utf-8")
    monkeypatch.setattr(analysis, "parent_alive", lambda _: True)
    monkeypatch.setattr(analysis, "analyze", process)
    monkeypatch.setattr(analysis.time, "sleep", advance)
    assert analysis.watch(run, cfg, 123) == 0
    assert len(list((run / "nmr_raw").glob("*.dx"))) == 1
    assert json.loads((run / "summary.json").read_text())["analyses_successful"] == 1


def test_watch_changed_raw_does_not_admit_unready_frequency_group(tmp_path, monkeypatch):
    run, cfg = watch_run(tmp_path, every_n=2)
    raw = run / "nmr_raw/stage_2_0001.dx"
    raw.write_text("partial", encoding="utf-8")
    calls, sleeps = [], []
    def process(*args, **kwargs):
        calls.append(len(list(raw.parent.glob("*.dx"))))
        return {"successful_analyses": 2, "failed_analyses": 0}
    def advance(_):
        sleeps.append(None)
        if len(sleeps) == 1:
            raw.write_text("complete raw spectrum", encoding="utf-8")
            assert not calls
        elif len(sleeps) == 2:
            assert not calls  # existing file changed, but no two-observation batch was admitted
            (raw.parent / "stage_2_0002.dx").write_text("second acquisition", encoding="utf-8")
        else:
            assert calls == [2]
            (run / "summary.json").write_text('{"status":"COMPLETE"}', encoding="utf-8")
    monkeypatch.setattr(analysis, "parent_alive", lambda _: True)
    monkeypatch.setattr(analysis, "analyze", process)
    monkeypatch.setattr(analysis.time, "sleep", advance)
    assert analysis.watch(run, cfg, 123) == 0
    assert calls == [2, 2]  # normal admitted batch and final cached drain


@pytest.mark.parametrize("explicit,statistics,target,expected", [
    (" DEMO  RUN ", "STATISTICS", "TARGET", "DEMO RUN"),
    (None, " STATISTICS DATA ", "TARGET", "STATISTICS DATA"),
    (" ", None, " TARGET DATA ", "TARGET DATA"),
    (None, " ", "TARGET", "TARGET"),
    (None, None, None, "test_demo run"),
])
def test_dataset_name_hierarchy_honors_production_configuration_before_run_fallback(
        tmp_path, monkeypatch, explicit, statistics, target, expected):
    run, cfg = watch_run(tmp_path, "COMPLETE_NO_SPECTRA")
    write_config(cfg, dataset_display_name=explicit, save_spectrum_plots=False, save_area_vs_time=False)
    production_config = {"statistics": {"dataset_display_name": statistics}, "target_peak": {"dataset_display_name": target}}
    monkeypatch.setattr(analysis, "processing_setup", lambda _: (None, None, "test", production_config))
    assert analysis.analyze(run, cfg)["dataset"] == expected
