import re
from urllib.parse import unquote, urlsplit
from chemyx_lab import config


def test_current_guide_links_exist():
    root = config.REPO_ROOT
    paths = [root / "README.md", root / "arduino/README.md"]
    paths += [p for p in (root / "docs").rglob("*.md")
              if "99_archive" not in p.parts and "vendor" not in p.parts]
    broken = []
    for path in paths:
        text = path.read_text(encoding="utf-8")
        text = re.sub(r"^```.*?^```[^\n]*", "", text, flags=re.M | re.S)
        for target in re.findall(r'\]\(([^)]+)\)', text):
            target = target.strip().strip("<>")
            if not target or target.startswith("#") or urlsplit(target).scheme:
                continue
            target_path = unquote(target.split('#')[0].split(' "')[0])
            if not (path.parent / target_path).exists():
                broken.append((path.relative_to(root).as_posix(), target))
    assert not broken, broken


def test_numbered_groups_have_linked_entry_points_and_current_workflow():
    docs = config.REPO_ROOT / "docs"
    index = (docs / "00_START_HERE.md").read_text(encoding="utf-8")
    for group in (p for p in docs.iterdir() if p.is_dir()):
        assert re.fullmatch(r"\d{2}_[a-z_]+", group.name), group
        assert (group / "00_START_HERE.md").is_file()
        assert f"{group.name}/00_START_HERE.md" in index
    current = (docs / "03_fixed_time_experiment/00_START_HERE.md").read_text(encoding="utf-8")
    for target in ("scripts/04_si6_fixed_time_run.py", "configs/experiments/si6_fixed_48h_6h.yaml",
                   "scripts/05_analyze_fixed_time_run.py"):
        assert target in current
        assert (config.REPO_ROOT / target).is_file()


def test_current_paths_and_acme_authority():
    text = (config.REPO_ROOT / "docs/07_other_experiments/01_legacy_experiment_index.md").read_text(encoding="utf-8")
    for name in ("scripts/02_si6_experiment.py", "configs/experiments/si6_real_two_stage.yaml",
                 "configs/machines/si6_real_COM4.yaml", "arduino/configs/arduino_real_COM3.yaml"):
        assert (config.REPO_ROOT / name).is_file()
        assert name in text
    assert "nmr.processing.phase_method: acme" in text
