"""NMR Test 01: read-only connection test. Never changes a setting or starts an acquisition."""

from __future__ import annotations

import _bootstrap  # noqa: F401

from chemyx_lab.workflows.nmr_standalone import connection_test

if __name__ == "__main__":
    raise SystemExit(connection_test())
