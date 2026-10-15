"""Offline post-session research; never opens instrument services."""
import argparse
from pathlib import Path
import _bootstrap  # noqa
from chemyx_lab.analysis.phase_comparison import compare_run

if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("run_dir", type=Path)
    print(compare_run(p.parse_args().run_dir))
