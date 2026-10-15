"""Export manifest-selected source and local assets; explicit review snapshots, no network."""
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

from chemyx_lab.release_inventory import load_inventory, source_files
PREFIXES = tuple(load_inventory(config.REPO_ROOT)["source_roots"])


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def export_release(root, output, with_offline_payload=False, *, working_tree=False):
    root, output = Path(root).resolve(), Path(output).resolve()
    inventory = load_inventory(root)
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=root, text=True).strip()
    dirty = git("status", "--porcelain", "--untracked-files=normal")
    if dirty and not working_tree:
        raise ValueError("Clean committed source required, or explicitly use --working-tree for a review snapshot")
    commit = git("rev-parse", "HEAD")
    files = source_files(root)
    hashes = {p.relative_to(root).as_posix(): sha256_file(p) for p in files}
    identity = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
    output.mkdir(parents=True, exist_ok=True)
    label = f"review_{identity[:12]}" if working_tree else commit[:12]
    archive = output / f"si6_{label}{'_offline' if with_offline_payload else '_source'}.zip"
    if archive.exists():
        raise FileExistsError(f"Never overwrite an existing release: {archive}")
    manifest = {"commit": commit, "working_tree_snapshot": working_tree, "source_tree_sha256": identity,
                "git_status_at_export": dirty, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                "source_files": hashes, "offline_assets": {}, "python_target": inventory["python_target"],
                "physical_state_included": False, "release_inventory": "offline/release_manifest.yaml"}
    if with_offline_payload:
        from chemyx_lab.analysis.phase_methods import deep_inventory
        if inventory["deep_phaser"]["enabled_in_payload"]:
            deep_inventory(root / "offline/resources/deep_phaser")
    with zipfile.ZipFile(archive, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
        for p in files:
            bundle.write(p, p.relative_to(root).as_posix())
        if with_offline_payload:
            for directory in inventory["payload_roots"]:
                for path in sorted((root / directory).rglob("*")):
                    if path.is_file():
                        name = path.relative_to(root).as_posix()
                        # Wheels/installers and toolchain payloads are already
                        # compressed or binary. Store them without recompressing.
                        bundle.write(path, name, compress_type=zipfile.ZIP_STORED)
                        manifest["offline_assets"][name] = sha256_file(path)
            if not any(name.startswith("offline/wheelhouse/") for name in manifest["offline_assets"]):
                raise ValueError("No offline wheels found; build them before requesting an offline package")
        if any(sha256_file(root / name) != expected for name, expected in hashes.items()):
            raise ValueError("Source changed during export; discard this incomplete archive")
        bundle.writestr("RELEASE_VERSION.json", json.dumps(manifest, indent=2) + "\n")
    (output / (archive.stem + "_manifest.json")).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    digest = sha256_file(archive)
    (output / (archive.name + ".sha256")).write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, manifest, digest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=config.REPO_ROOT / "dist/si6_release")
    parser.add_argument("--with-offline-payload", action="store_true")
    parser.add_argument("--working-tree", action="store_true", help="Explicit review snapshot including uncommitted source; never commits")
    args = parser.parse_args(argv)
    try:
        archive, manifest, digest = export_release(config.REPO_ROOT, args.output_dir.resolve(), args.with_offline_payload, working_tree=args.working_tree)
        print(json.dumps({"archive": str(archive), "commit": manifest["commit"], "sha256": digest,
                          "source_files": len(manifest["source_files"]), "offline_assets": len(manifest["offline_assets"])}, indent=2))
        return 0
    except Exception as exc:
        print(f"Release export failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
