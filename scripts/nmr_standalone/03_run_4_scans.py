"""NMR Test 03: one live 4-scan acquisition (simplified 10/11 method; --experiment-worker for 06/08), fully recorded."""

from __future__ import annotations

import _bootstrap  # noqa: F401

from chemyx_lab.workflows.nmr_standalone import acquisition_test

if __name__ == "__main__":
    raise SystemExit(acquisition_test(scans=4))
