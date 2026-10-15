import re
from pathlib import Path
from chemyx_lab import config


def test_current_guide_links_exist():
    for name in ("SI6_START_HERE.md", "SI6_NMR_PHASE_METHODS.md", "RIG_STATE_BACKUP.md"):
        path = config.REPO_ROOT / "docs" / name
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if "://" not in target and not target.startswith("#"):
                assert (path.parent / target.split('#')[0]).is_file(), (name, target)


def test_current_paths_and_acme_authority():
    text = (config.REPO_ROOT / "docs/SI6_START_HERE.md").read_text(encoding="utf-8")
    for name in ("scripts/02_si6_experiment.py", "configs/experiments/si6_real_two_stage.yaml",
                 "configs/machines/si6_real_COM4.yaml", "arduino/configs/arduino_real_COM3.yaml"):
        assert (config.REPO_ROOT / name).is_file()
        assert name in text
    assert "nmr.processing.phase_method: acme" in text
