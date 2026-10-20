"""Reprocess a selected Stage 2 data folder. No arguments opens a folder picker."""
import _bootstrap  # noqa: F401
from chemyx_lab.analysis.stage2only_reanalysis import main

if __name__ == "__main__":
    raise SystemExit(main())
