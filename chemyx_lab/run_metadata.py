"""Small operational summaries; the existing durable journal remains authoritative."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import platform

from .runtime_state import write_json_atomic


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def begin_run(paths, raw, identity, repository_root, config_sources=None, *, software_version=None):
    filename = paths.run_dir / "metadata" / "run_metadata.json"
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
                   configuration_snapshot="config_snapshot.json", instrument_snapshot="instrument_settings_snapshot.json",
                   needle_snapshot="arduino_config_snapshot.json", journal="operation_journal.jsonl", status="running",
                   position_source="software estimate; no encoder or HOME switch")
    write_json_atomic(filename, payload)


def finish_run(paths):
    filename = paths.run_dir / "metadata" / "run_metadata.json"
    payload = json.loads(filename.read_text(encoding="utf-8"))
    events = [json.loads(line) for line in paths.journal_jsonl.read_text(encoding="utf-8").splitlines()]
    terminal = next((e for e in reversed(events) if e["event_type"] == "terminal"), {})
    payload.update(ended_at_utc=utc_now(), status=terminal.get("terminal_status", "session_closed"),
                   terminal_classification=terminal.get("result_classification"),
                   timed_fallback_stages=terminal.get("timed_fallback_stages", []),
                   stages=[{k: e[k] for k in ("workflow_phase", "result_classification", "timestamp_utc", "scheduled_measurement_number") if k in e}
                           for e in events if e["event_type"] == "stage_transition_decision"],
                   dose_confirmed=any(e["event_type"] == "dose_confirmed" for e in events))
    write_json_atomic(filename, payload)
    status_file = paths.run_dir / "operator_status.json"
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
    write_json_atomic(paths.run_dir / "operator_status.json", payload)
    area = "UNAVAILABLE" if row.get("peak_area") is None else f"{row['peak_area']:.6g}"
    print(f"EXPERIMENT: {experiment_id} | STAGE: {stage.name} | OBSERVATION: {number}\n"
          f"Operational elapsed: {elapsed:.3f} h | SOLVENT: {'DETECTED' if row.get('solvent_present', True) else 'NOT CONFIRMED'}\n"
          f"TARGET AREA: {area} | ENDPOINT: {'SATISFIED' if row['plateau'] else 'NOT SATISFIED'} | "
          f"FALLBACK: {'ACTIVE' if row.get('stage_timed_fallback') else 'INACTIVE'}\nNEXT ACTION: {payload['next_action']}")
