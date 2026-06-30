"""Compatibility import for older scripts.

New code should import from ``chemyx_lab.config``. This file remains so the
existing examples and tests keep working.
"""

from chemyx_lab.config import *  # noqa: F401,F403
