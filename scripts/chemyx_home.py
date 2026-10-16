"""Return Chemyx channel(s) to their saved software HOME.

Thin wrapper for ``python scripts/chemyx_control.py home``; the same tested
implementation calculates and executes the return. Example:

    python scripts/chemyx_home.py --channel both --live
"""
from __future__ import annotations

import _bootstrap  # noqa: F401
from chemyx_lab.workflows.chemyx_manual import main


if __name__ == "__main__":
    raise SystemExit(main(command="home"))
