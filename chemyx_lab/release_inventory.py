"""One portable release inventory, shared by builders and offline validators."""
import json
from pathlib import Path


def load_inventory(root):
    data = json.loads((Path(root) / "offline/release_manifest.yaml").read_text(encoding="utf-8"))
    if data.get("schema") != "chemyx.si6-release.v1":
        raise ValueError("Unsupported release inventory")
    return data


def source_files(root):
    root = Path(root).resolve()
    data = load_inventory(root)
    files = set()
    for name in data["source_roots"]:
        path = root / name
        if not path.exists():
            raise ValueError(f"Missing release source root: {name}")
        for item in ([path] if path.is_file() else path.rglob("*")):
            rel = item.relative_to(root)
            if (not item.is_file() or item.suffix in {".pyc", ".pyo"}
                    or set(rel.parts) & set(data["excluded_parts"])
                    or any(part.startswith(("test_tmp_", ".test-tmp", ".codex_pytest_temp_", ".venv")) for part in rel.parts)
                    or any(rel.as_posix().startswith(p) for p in data["excluded_prefixes"])):
                continue
            if item.is_symlink() or not item.resolve().is_relative_to(root):
                raise ValueError(f"Unsafe source file: {item}")
            files.add(item)
    for name in data["required_resources"]:
        if root / name not in files:
            raise ValueError(f"Required resource omitted/missing in release inventory: {name}")
    return sorted(files)
