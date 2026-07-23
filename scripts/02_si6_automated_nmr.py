"""Numbered entry point for the automated Si6 Chemyx/NMR workflow."""

from __future__ import annotations

import _bootstrap  # noqa: F401
from chemyx_lab.workflows.si6_automated_nmr import main


if __name__ == "__main__":
    raise SystemExit(main())

