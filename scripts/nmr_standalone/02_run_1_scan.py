"""NMR Test 02: one live 1-scan acquisition through the 06/08 worker, fully recorded."""

from __future__ import annotations

import _bootstrap  # noqa: F401

from chemyx_lab.workflows.nmr_standalone import acquisition_test

if __name__ == "__main__":
    raise SystemExit(acquisition_test(scans=1))
