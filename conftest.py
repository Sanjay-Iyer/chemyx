"""
conftest.py — makes the repo root importable so `import pump` / `import config`
work no matter where pytest is launched from.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def _isolated_chemyx_position_ledger(tmp_path_factory):
    """Keep every test (and its subprocesses) away from runtime/chemyx_state."""
    name = "CHEMYX_POSITION_STATE_DIR"
    previous = os.environ.get(name)
    os.environ[name] = str(tmp_path_factory.mktemp("chemyx_position_state"))
    yield
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous
