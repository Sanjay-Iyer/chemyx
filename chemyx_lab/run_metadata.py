"""Small operational summaries; the existing durable journal remains authoritative."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import platform
import csv
import yaml

from .runtime_state import write_json_atomic


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def begin_run(paths, raw, identity, repository_root, config_sources=None, *, software_version=None):
    from .analysis.phase_methods import settings as phase_settings
    filename = paths.run_dir / "metadata" / ("experiment_info.json" if paths.layout else "run_metadata.json")
    versions = {}
    for package in ("pyserial", "PyYAML", "numpy", "scipy", "matplotlib", "nmrglue"):
        try:
            versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            versions[package] = "unavailable"
    source_hashes = {}
    for name in ("chemyx_lab", "scripts", "arduino/python", "arduino/scripts"):
        for source in sorted((repository_root / name).rglob("*.py")):
            source_hashes[source.relative_to(repository_root).as_posix()] = hashlib.sha256(source.read_bytes()).hexdigest()
    processing_configs = {}
    for name in ("configs/nmr/analysis.yaml", "configs/nmr/analysis.local.yaml"):
        source = repository_root / name
        if source.is_file():
            contents = source.read_bytes()
            processing_configs[name] = {"sha256": hashlib.sha256(contents).hexdigest(), "yaml_at_start": contents.decode("utf-8-sig")}
    payload = dict(experiment_id=raw["workflow"].get("experiment_id"), run_id=paths.run_dir.name,
                   mode=identity.mode, run_kind=identity.kind, started_at_utc=utc_now(), ended_at_utc=None,
                   git_commit=software_version, source_files_sha256=source_hashes, processing_configs_at_start=processing_configs, python_version=platform.python_version(),
                   package_versions=versions, configuration_sources=config_sources or {},
                   configuration_sha256=hashlib.sha256(json.dumps(raw, sort_keys=True).encode()).hexdigest(),
                   configuration_snapshot=str((paths.metadata_dir / "config_snapshot.json").relative_to(paths.run_dir)), instrument_snapshot=str((paths.metadata_dir / "instrument_settings_snapshot.json").relative_to(paths.run_dir)),
                   needle_snapshot=str((paths.metadata_dir / "arduino_config_snapshot.json").relative_to(paths.run_dir)), journal=str(paths.journal_jsonl.relative_to(paths.run_dir)), status="running",
                   position_source="software estimate; no encoder or HOME switch")
    if paths.layout:
        layout = paths.layout
        payload.update(output_layout_version=1, output_directory=str(paths.run_dir),
                       run_sequence_suffix=layout.suffix, started_at_local=layout.started_at_local.isoformat(),
                       started_at_utc=layout.started_at_local.astimezone(timezone.utc).isoformat(),
                       local_start_date=layout.started_at_local.strftime("%Y%m%d"),
                       timezone_offset=layout.started_at_local.strftime("%z"), stage_names=list(layout.stage_names),
                       stage_directories={name: str(layout.step(name).relative_to(layout.root)) for name in layout.stage_names},
                       chemical_completion_verified=False, timed_fallback_occurred=False)
        (layout.metadata / "configuration_snapshot.yaml").write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
        write_json_atomic(layout.metadata / "software_version.json", {k: payload[k] for k in ("git_commit", "source_files_sha256", "python_version", "package_versions")})
    payload["effective_nmr_processing"] = phase_settings(raw["nmr"])
    if "processing" not in raw["nmr"]:
        payload["effective_nmr_processing"].update(phase_method=None,
            selection_source="Legacy common processing config snapshots; actual method recorded per spectrum")
    write_json_atomic(filename, payload)


def finish_run(paths):
    filename = paths.run_dir / "metadata" / ("experiment_info.json" if paths.layout else "run_metadata.json")
    payload = json.loads(filename.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in paths.journal_jsonl.read_text(encoding="utf-8").splitlines()]
    terminal = next((e for e in reversed(events) if e["event_type"] == "terminal"), {})
    payload.update(ended_at_utc=utc_now(), status=terminal.get("terminal_status", "session_closed"),
                   terminal_classification=terminal.get("result_classification"),
                   timed_fallback_stages=terminal.get("timed_fallback_stages", []),
                   stages=[{k: e[k] for k in ("workflow_phase", "result_classification", "timestamp_utc", "scheduled_measurement_number") if k in e}
                           for e in events if e["event_type"] == "stage_transition_decision"],
                   dose_confirmed=any(e["event_type"] == "dose_confirmed" for e in events))
    if paths.layout:
        payload["ended_at_local"] = datetime.now().astimezone().isoformat()
        decisions = {e.get("workflow_phase"): e.get("result_classification") for e in events if e["event_type"] == "stage_transition_decision"}
        payload["stage_end_reasons"] = {name: decisions.get(name, "not_completed") for name in paths.layout.stage_names}
        for name in paths.layout.stage_names:
            evidence = {"stage": name, "end_reason": payload["stage_end_reasons"][name],
                        "events": [e for e in events if e.get("workflow_phase") == name and e["event_type"] in {"stage_transition_decision", "stage_completion_evidence", "solvent_timed_fallback", "monitoring_stage_started"}]}
            write_json_atomic(paths.stage_dir(name) / "analysis" / "stage_completion.json", evidence)
        payload["timed_fallback_occurred"] = bool(payload["timed_fallback_stages"]) or any(e["event_type"] == "solvent_timed_fallback" for e in events)
        payload["chemical_completion_verified"] = payload["status"] == "completed" and not payload["timed_fallback_occurred"] and all(reason == "plateau_reached" for reason in payload["stage_end_reasons"].values())
        payload["output_paths"] = {"time_series": str(paths.time_series_csv), "journal": str(paths.journal_jsonl),
                                   "live_status": str(paths.status_json), "channel_2_dose": str(paths.dose_json),
                                   "step_1": str(paths.run_dir / "step_1"), "step_2": str(paths.run_dir / "step_2")}
        write_json_atomic(paths.layout.final_qc / "summary.json", payload)
        with (paths.layout.final_qc / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=["experiment_id", "run_id", "stage", "end_reason", "status", "timed_fallback", "chemical_completion_verified"])
            writer.writeheader()
            for stage, reason in payload["stage_end_reasons"].items():
                writer.writerow(dict(experiment_id=payload["experiment_id"], run_id=payload["run_id"], stage=stage,
                                     end_reason=reason, status=payload["status"], timed_fallback=reason == "timed_fallback_completed", chemical_completion_verified=reason == "plateau_reached"))
        if paths.dose_json.is_file():
            dose = json.loads(paths.dose_json.read_text(encoding="utf-8"))
            dose.update(command_evidence=[e for e in events if e.get("channel") == 2 or e["event_type"].startswith("dose_")],
                        experiment_status=payload["status"], errors=[e for e in events if e.get("error_message")],
                        record_updated_at_utc=utc_now())
            write_json_atomic(paths.dose_json, dose)
    write_json_atomic(filename, payload)
    status_file = paths.status_json
    latest = json.loads(status_file.read_text(encoding="utf-8")) if status_file.exists() else {}
    latest.update(status=payload["status"], ended_at_utc=payload["ended_at_utc"], terminal_classification=payload["terminal_classification"],
                  dose_confirmed=payload["dose_confirmed"], timed_fallback_stages=payload["timed_fallback_stages"])
    write_json_atomic(status_file, latest)


def publish_observation(paths, experiment_id, stage, schedule, row):
    number = schedule.scheduled_measurement_number
    offset = stage.interval_minutes * 60 * (number - (1 if stage.measure_immediately else 0))
    elapsed = (schedule.actual_cycle_start_monotonic - schedule.scheduled_monotonic + offset) / 3600
    payload = dict(experiment_id=experiment_id, stage=stage.name, observation=number, updated_at_utc=utc_now(),
                   operational_elapsed_hours=elapsed, timed_fallback_duration_hours=stage.max_hours,
                   solvent_present=row.get("solvent_present"), consecutive_missing=row.get("solvent_missing_streak", 0),
                   target_area=row.get("peak_area"), endpoint_satisfied=bool(row["plateau"]),
                   timed_fallback=bool(row.get("stage_timed_fallback")), raw_path=row.get("raw_path"),
                   processed_path=row.get("processed_path"), solvent_evidence_path=row.get("solvent_evidence_path"),
                   status="monitoring", next_action="stage transition after cleanup" if row["plateau"] else "wait for next scheduled acquisition")
    write_json_atomic(paths.status_json, payload)
    area = "UNAVAILABLE" if row.get("peak_area") is None else f"{row['peak_area']:.6g}"
    print(f"EXPERIMENT: {experiment_id} | STAGE: {stage.name} | OBSERVATION: {number}\n"
          f"Operational elapsed: {elapsed:.3f} h | SOLVENT: {'DETECTED' if row.get('solvent_present', True) else 'NOT CONFIRMED'}\n"
          f"TARGET AREA: {area} | ENDPOINT: {'SATISFIED' if row['plateau'] else 'NOT SATISFIED'} | "
          f"FALLBACK: {'ACTIVE' if row.get('stage_timed_fallback') else 'INACTIVE'}\nNEXT ACTION: {payload['next_action']}")
