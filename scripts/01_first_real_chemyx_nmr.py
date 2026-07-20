"""Numbered entry point for workflow 01: Chemyx withdraw, NMR, Chemyx infuse."""

from __future__ import annotations

import _bootstrap  # noqa: F401
from chemyx_lab.workflows.first_real_chemyx_nmr import main


if __name__ == "__main__":
    raise SystemExit(main())
