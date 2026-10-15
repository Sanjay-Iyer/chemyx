"""Offline environment, source integrity and real-config checks; NEVER open hardware."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
import sysconfig

import _bootstrap  # noqa: F401
from chemyx_lab import config
from chemyx_lab.runtime_journal import discover_git_commit
from chemyx_lab.workflows import three_instrument_si6 as si6


def verify_release(root):
    marker = root / "RELEASE_VERSION.json"
    if not marker.is_file():
        return {"mode": "git checkout", "commit": discover_git_commit(root), "note": "For an export, RELEASE_VERSION.json is required"}
    data = json.loads(marker.read_text(encoding="utf-8"))
    changed_configs = []
    for name, expected in {**data["source_files"], **data.get("offline_assets", {})}.items():
        path = (root / name).resolve()
        if not path.is_relative_to(root.resolve()) or not path.is_file():
            raise ValueError(f"Missing/unsafe release file: {name}")
        with path.open("rb") as handle:
            actual = hashlib.file_digest(handle, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(handle.read()).hexdigest()
        if actual != expected:
            if name.endswith((".yaml", ".yml")) and name.startswith(("configs/", "arduino/configs/")):
                changed_configs.append(name)
            else:
                raise ValueError(f"Release hash mismatch: {name}")
    return {"mode": "verified release export", "commit": data["commit"], "changed_operator_configs": changed_configs,
            "files_verified": len(data["source_files"]) + len(data.get("offline_assets", {}))}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--machine-config", type=Path, default=config.REPO_ROOT / "configs/machines/si6_real_COM4.yaml")
    parser.add_argument("--workflow-config", type=Path, default=config.REPO_ROOT / "configs/experiments/si6_real_two_stage.yaml")
    parser.add_argument("--arduino-config", type=Path, default=config.REPO_ROOT / "arduino/configs/arduino_real_COM3.yaml")
    parser.add_argument("--expect-commit", help="Refuse a checkout/export with a different full commit")
    args = parser.parse_args(argv)
    try:
        versions = {}
        for module, package in (("serial", "pyserial"), ("yaml", "PyYAML"), ("numpy", "numpy"), ("scipy", "scipy"), ("matplotlib", "matplotlib"), ("nmrglue", "nmrglue"), ("pytest", "pytest")):
            importlib.import_module(module)
            versions[package] = importlib.metadata.version(package)
        from validate_si6_offline import RESOURCES
        missing = [name for name in RESOURCES if not (config.REPO_ROOT / name).is_file()]
        if missing:
            raise ValueError("Missing deployment files: " + ", ".join(missing))
        release = verify_release(config.REPO_ROOT)
        if args.expect_commit and release.get("commit") != args.expect_commit:
            raise ValueError("Deployed commit does not match --expect-commit")
        raw, arduino, pump, nmr = si6.prepare(args.workflow_config, args.machine_config, args.arduino_config, mock=True)
        if arduino["needle"]["down_position"] != 0 or arduino["needle"]["up_position"] != 1:
            raise ValueError("Real Si6 needle geometry requires DOWN=HOME=0 and UP=1")
        print(json.dumps({"status": "PASS", "hardware_contacted": False, "python": sys.version.split()[0], "platform": sysconfig.get_platform(),
                          "offline_lock_target": "CPython 3.11 win-amd64", "packages": versions, "release": release,
                          "ports": {"arduino": arduino["arduino"]["port"], "chemyx": pump.port}, "nmr": {"host": nmr.host, "port": nmr.port},
                          "stages": [{"name": s.name, "interval_minutes": s.interval_minutes, "fallback_hours": s.max_hours} for s in si6.base.build_stages(raw["workflow"])],
                          "solvent_monitor": raw["solvent_monitor"]}, indent=2))
        return 0
    except Exception as exc:
        print(f"FAIL (no hardware contacted): {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
