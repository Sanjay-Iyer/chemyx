"""Compatibility bridge for the proven workflow 01 implementation.

New code imports :mod:`chemyx_lab.workflows.first_real_chemyx_nmr`. This file
remains temporarily so historical commands and external imports keep working
until work-laptop validation is complete.
"""

from __future__ import annotations

import _bootstrap  # noqa: F401

from chemyx_lab.workflows.first_real_chemyx_nmr import *  # noqa: F401,F403
from chemyx_lab.workflows.first_real_chemyx_nmr import main


if __name__ == "__main__":
    raise SystemExit(main())
