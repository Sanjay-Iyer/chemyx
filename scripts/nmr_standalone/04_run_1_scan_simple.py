"""NMR Test 04: one live 1-scan acquisition with simple legacy-style completion (ResultCode 0), fully recorded."""

from __future__ import annotations

import _bootstrap  # noqa: F401

from chemyx_lab.workflows.nmr_standalone import legacy_acquisition_test

if __name__ == "__main__":
    raise SystemExit(legacy_acquisition_test(scans=1))
