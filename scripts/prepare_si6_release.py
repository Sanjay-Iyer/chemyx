"""Export exactly committed source plus optional local offline assets; no network."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

import _bootstrap  # noqa: F401
from chemyx_lab import config

PREFIXES = ("AGENTS.md", "README.md", ".gitignore", "requirements.txt", "pyproject.toml", "chemyx_lab", "scripts", "arduino", "configs", "config_templates", "docs", "tests", "offline", "nmr_template", "smoke_test", "results/100426_algovalidation/code")


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_release(root, output, with_offline_payload=False):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    if git("status", "--porcelain", "--untracked-files=normal"):
        raise ValueError("Commit required source changes first; release export requires a clean working tree")
    commit = git("rev-parse", "HEAD")
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"si6_{commit[:12]}{'_offline' if with_offline_payload else '_source'}.zip"
    if archive.exists():
        raise FileExistsError(f"Never overwrite an existing release: {archive}")
    subprocess.run(["git", "archive", "--format=zip", f"--output={archive.resolve()}", commit, "--", *PREFIXES], cwd=root, check=True)
    manifest = {"commit": commit, "created_at_utc": datetime.now(timezone.utc).isoformat(), "source_files": {}, "offline_assets": {},
                "python_target": "CPython 3.11 win-amd64", "physical_state_included": False,
                "separate_assets": ["Rig-owned runs/arduino/needle_state.json and runtime/si6_doses with journals if continuing the same physical rig/reaction", "USB drivers and NMR vendor software/license as required", "Archived datasets are optional; the committed fixtures support core tests"]}
    with zipfile.ZipFile(archive, "a") as bundle:
        for entry in bundle.infolist():
            if not entry.is_dir():
                manifest["source_files"][entry.filename] = hashlib.sha256(bundle.read(entry)).hexdigest()
        if with_offline_payload:
            for directory in ("wheelhouse", "installers", "drivers", "arduino"):
                for path in sorted((root / "offline" / directory).rglob("*")):
                    if path.is_file():
                        name = path.relative_to(root).as_posix()
                        bundle.write(path, name)
                        manifest["offline_assets"][name] = sha256_file(path)
            original_manifest = root / "offline/BUNDLE_MANIFEST.txt"
            if original_manifest.is_file():
                bundle.write(original_manifest, "offline/BUNDLE_MANIFEST.txt")
                manifest["offline_assets"]["offline/BUNDLE_MANIFEST.txt"] = sha256_file(original_manifest)
            if not any(name.startswith("offline/wheelhouse/") for name in manifest["offline_assets"]):
                raise ValueError("No offline wheels found; build them before requesting an offline package")
        bundle.writestr("RELEASE_VERSION.json", json.dumps(manifest, indent=2) + "\n")
    (output / (archive.stem + "_manifest.json")).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    digest = sha256_file(archive)
    (output / (archive.name + ".sha256")).write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, manifest, digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=config.REPO_ROOT / "dist/si6_release")
    parser.add_argument("--with-offline-payload", action="store_true")
    args = parser.parse_args(argv)
    try:
        archive, manifest, digest = export_release(config.REPO_ROOT, args.output_dir.resolve(), args.with_offline_payload)
        print(json.dumps({"archive": str(archive), "commit": manifest["commit"], "sha256": digest,
                          "source_files": len(manifest["source_files"]), "offline_assets": len(manifest["offline_assets"])}, indent=2))
        return 0
    except Exception as exc:
        print(f"Release export failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
