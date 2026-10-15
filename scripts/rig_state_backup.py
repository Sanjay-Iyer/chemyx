"""Explicit inventory/backup/verify/compare/restore-plan; never contacts instruments."""
import argparse
import json
from pathlib import Path
import _bootstrap  # noqa
from chemyx_lab import config, rig_backup


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("operation", choices=("inventory", "backup", "verify", "compare", "restore-plan"))
    p.add_argument("--backup", type=Path)
    p.add_argument("--root", type=Path, default=config.REPO_ROOT)
    p.add_argument("--destination", type=Path, default=config.REPO_ROOT / "rig_backups")
    p.add_argument("--state-path", type=Path, action="append", default=[])
    args = p.parse_args(argv)
    if args.operation == "inventory":
        result = rig_backup.inventory(args.root, args.state_path)
    elif args.operation == "backup":
        result = {"backup": str(rig_backup.backup(args.root, args.destination, args.state_path))}
    else:
        if not args.backup:
            p.error("--backup folder is required for verification/comparison")
        result = rig_backup.verify(args.backup) if args.operation == "verify" else rig_backup.compare(args.backup, current_root=args.root)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
