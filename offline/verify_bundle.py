"""Verify the locally transferred wheel/installer manifest without network access."""
import hashlib
from pathlib import Path
import re


def verify(root):
    root = Path(root).resolve()
    manifest = root / "BUNDLE_MANIFEST.txt"
    entries = set()
    for line in manifest.read_text(encoding="ascii").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+(\d+)\s+(.+)", line.strip())
        if not match:
            raise ValueError("Invalid bundle manifest line")
        digest, size, name = match.groups()
        path = (root / name.replace("\\", "/")).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size != int(size):
            raise ValueError(f"Missing, unsafe or wrong-sized offline asset: {name}")
        with path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if checksum.lower() != digest.lower():
            raise ValueError(f"Offline asset hash mismatch: {name}")
        entries.add(path)
    wheels = set((root / "wheelhouse").glob("*.whl"))
    if not wheels or not wheels <= entries:
        raise ValueError("Wheelhouse missing or includes unverified wheels")
    return len(entries), len(wheels)


if __name__ == "__main__":
    try:
        assets, wheels = verify(Path(__file__).resolve().parent)
        print(f"PASS: {assets} offline assets verified; {wheels} wheels; no network used")
    except Exception as exc:
        raise SystemExit(f"FAIL: {exc}")
