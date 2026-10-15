"""Explicit, hardware-free rig-state snapshots. Restoration is deliberately a plan.

There is no generic writer for dose ledgers or physical position references:
conflicting/uncertain records need instrument-side reconciliation, not rollback.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

SCHEMA = "chemyx.rig-backup.v1"


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(root, extras=()):
    root = Path(root).resolve()
    sources = {"arduino_state": [root / "runs/arduino"],
               "dose_history": [root / "runtime/si6_doses"],
               "relevant_journals": [], "configured_state": []}
    # Both dated runs and legacy directories; no raw spectra or scientific arrays.
    for parent in (root / "results", root / "runs"):
        if parent.exists():
            for pattern in ("**/*journal*.jsonl", "**/*journal*.json", "**/run_manifest.json", "**/*dose*.json"):
                sources["relevant_journals"].extend(parent.glob(pattern))
    # Discover explicit needle state references from operator YAML; parsing is read-only.
    import yaml
    for cfg in (root / "arduino/configs").glob("*.yaml"):
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
        state = data.get("needle", {}).get("state_path")
        if state:
            p = Path(state)
            p = p if p.is_absolute() else root / p
            sources["configured_state"].extend([p, p.with_name(p.name + ".home_pending.json")])
        run_root = data.get("results", {}).get("run_root_dir")
        if run_root:
            p = Path(run_root)
            sources["configured_state"].append(p if p.is_absolute() else root / p)
    sources["configured_state"].extend(Path(p) if Path(p).is_absolute() else root / p for p in extras)
    records, missing, seen = [], [], set()
    for category, paths in sources.items():
        for source in sorted(set(paths)):
            if not source.exists():
                missing.append(str(source))
                continue
            files = sorted(source.rglob("*")) if source.is_dir() else [source]
            for path in files:
                if path.is_symlink() or path.resolve() != path.absolute():
                    raise ValueError(f"Symlink/reparse state path refused: {path}")
                if not path.is_file() or path.resolve() in seen:
                    continue
                seen.add(path.resolve())
                stamp = path.stat()
                record = {"source_path": str(path.resolve()), "original_filename": path.name,
                          "category": category, "sha256": digest(path), "bytes": stamp.st_size,
                          "mtime_ns": stamp.st_mtime_ns, "experiment_ids": [], "record_version": None,
                          "manual_physical_verification_required": True}
                try:
                    obj = json.loads(path.read_text(encoding="utf-8-sig"))
                    if isinstance(obj, dict):
                        record.update(record_version=obj.get("schema", obj.get("schema_version", obj.get("version"))),
                                      experiment_ids=[str(obj[k]) for k in ("experiment_id", "physical_experiment_id") if obj.get(k)],
                                      uncertainty=obj.get("uncertain"), position_valid=obj.get("position_valid"))
                        if category == "dose_history":
                            record.update(dose_status=obj.get("status"), blocks_repeat_dosing=True,
                                          uncertainty=bool(obj.get("uncertain")) or obj.get("status") in {"DISPATCH_INTENT", "UNCERTAIN"})
                        if "position_valid" in obj:
                            record["position_blocking"] = obj["position_valid"] is not True
                except (ValueError, UnicodeError):
                    record["content_status"] = "opaque_or_journal_preserved_verbatim"
                    if path.suffix == ".jsonl":
                        ids, versions, errors = set(), set(), 0
                        with path.open(encoding="utf-8-sig", errors="replace") as stream:
                            for line in stream:
                                if not line.strip():
                                    continue
                                try:
                                    event = json.loads(line)
                                    if isinstance(event, dict):
                                        if event.get("experiment_id"):
                                            ids.add(str(event["experiment_id"]))
                                        if event.get("schema_version"):
                                            versions.add(str(event["schema_version"]))
                                except ValueError:
                                    errors += 1
                        record.update(experiment_ids=sorted(ids), journal_versions=sorted(versions),
                                      journal_parse_errors=errors)
                if path.name.endswith(".home_pending.json"):
                    record.update(position_blocking=True, uncertainty=True, home_recovery_pending=True)
                records.append(record)
    return {"schema": SCHEMA, "root": str(root), "files": records, "missing_locations": sorted(set(missing)),
            "hardware_contacted": False}


def backup(root, destination, extras=()):
    from .runtime_journal import discover_git_commit
    root = Path(root).resolve()
    snapshot = inventory(root, extras)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    target = Path(destination).resolve() / stamp
    if any(target.is_relative_to(Path(r["source_path"])) for r in snapshot["files"]):
        raise ValueError("Backup destination overlaps a state source")
    target.mkdir(parents=True, exist_ok=False)
    for index, record in enumerate(snapshot["files"]):
        rel = Path(record["category"]) / f"{index:06d}" / record["original_filename"]
        output = target / rel
        output.parent.mkdir(parents=True)
        shutil.copy2(record["source_path"], output)
        if digest(output) != record["sha256"] or digest(record["source_path"]) != record["sha256"]:
            raise ValueError("State changed during backup; snapshot incomplete and must not be restored")
        record["backup_path"] = rel.as_posix()
    snapshot.update(created_at_utc=datetime.now(timezone.utc).isoformat(), software_version=discover_git_commit(root),
                    restoration_policy="READ-ONLY PLAN; no automatic rollback or dose authorization. Restored needle references require manual HOME.")
    manifest = target / "backup_manifest.json"
    manifest.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")
    (target / "backup_manifest.sha256").write_text(digest(manifest) + "\n", encoding="ascii")
    verify(target)
    return target


def verify(folder):
    folder = Path(folder).resolve()
    manifest = folder / "backup_manifest.json"
    if digest(manifest) != (folder / "backup_manifest.sha256").read_text().strip():
        raise ValueError("Backup manifest checksum mismatch")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    if data.get("schema") != SCHEMA:
        raise ValueError("Unsupported rig-backup schema")
    seen = set()
    for record in data["files"]:
        raw_path = folder / record["backup_path"]
        path = raw_path.resolve()
        if path != raw_path.absolute() or ".." in Path(record["backup_path"]).parts:
            raise ValueError(f"Reparse/traversal backup path refused: {raw_path}")
        if not path.is_relative_to(folder) or path in seen or path.is_symlink() or not path.is_file():
            raise ValueError(f"Missing/unsafe/duplicate backup file: {path}")
        seen.add(path)
        if digest(path) != record["sha256"]:
            raise ValueError(f"Backup checksum mismatch: {path}")
    return data


def compare(folder, *, current_root=None):
    data = verify(folder)
    original_root = Path(data["root"])
    root = Path(current_root).resolve() if current_root else original_root
    rows = []
    for record in data["files"]:
        original = Path(record["source_path"])
        if current_root:
            if not original.is_relative_to(original_root):
                rows.append({**record, "comparison": "external_path_requires_manual_mapping"})
                continue
            original = root / original.relative_to(original_root)
        state = "missing_current"
        if original.exists():
            state = "identical" if digest(original) == record["sha256"] else (
                "conflict_current_newer" if original.stat().st_mtime_ns > record["mtime_ns"] else "conflict")
        rows.append({**record, "current_path": str(original), "comparison": state})
    return {"files": rows, "automatic_restore_allowed": False,
            "required_action": "Preserve all current and backup records. Reconcile dose reservations conservatively on the rig. Never remove uncertain receipts. Use manual HOME for any restored needle state; copy does not establish physical position."}
