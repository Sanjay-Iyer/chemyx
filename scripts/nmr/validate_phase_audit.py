"""Build the offline real-data NMR validation package or append manual checkpoints."""
import _bootstrap  # noqa: F401
from chemyx_lab.analysis.nmr_validation import main

if __name__ == "__main__":
    raise SystemExit(main())
