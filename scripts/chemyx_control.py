"""Standalone Chemyx Fusion 4000 control with a custom software HOME.

HOME is a saved software reference, not a native Chemyx command. See
docs/02_chemyx/02_manual_control_and_home.md. Examples (PowerShell, repository root):

    python scripts/chemyx_control.py status --channel both
    python scripts/chemyx_control.py set-home --channel 1 --live
    python scripts/chemyx_control.py home --channel both --live
"""
from __future__ import annotations

import _bootstrap  # noqa: F401
from chemyx_lab.workflows.chemyx_manual import main


if __name__ == "__main__":
    raise SystemExit(main())
