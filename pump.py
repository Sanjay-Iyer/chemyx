"""Compatibility import for older scripts.

New code should import ``Pump`` from ``chemyx_lab.pump``.
"""

from chemyx_lab.pump import *  # noqa: F401,F403
from chemyx_lab.pump import serial  # re-exported for existing tests
