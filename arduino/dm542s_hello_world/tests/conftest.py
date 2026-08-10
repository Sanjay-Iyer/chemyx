"""Make the dm542s_hello_world scripts importable from these tests.

The workflow scripts live next to the modules they import (``motion_utils``,
``calibration_utils``, ``serial_test_utils``) so they can be run directly from
that directory. Adding the directory to ``sys.path`` lets pytest import the same
modules the same way, from anywhere the suite is launched.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))


@pytest.fixture
def package_root() -> Path:
    """Directory holding the scripts, configs, and firmware under test."""
    return PACKAGE_ROOT


def load_script(filename: str):
    """Import a workflow script whose module name starts with a digit.

    ``01_needle_move`` and ``99_needle_calibration`` are not valid Python
    identifiers, so a plain import is a syntax error. Loading them by path is
    what lets their execution and abort logic be tested at all.
    """
    import importlib.util

    path = PACKAGE_ROOT / filename
    name = f"script_{path.stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # @dataclass resolves types via sys.modules[cls.__module__], so the module
    # must be registered BEFORE its body executes.
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


@pytest.fixture(scope="session")
def needle_move_script():
    """The 01_needle_move.py module, imported by path."""
    return load_script("01_needle_move.py")


@pytest.fixture(scope="session")
def calibration_script():
    """The 99_needle_calibration.py module, imported by path."""
    return load_script("99_needle_calibration.py")
