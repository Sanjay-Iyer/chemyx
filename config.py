"""
config.py — Single source of truth for the Chemyx Fusion 4000X test scripts.

This file is COMMITTED to git, so its defaults are deliberately GENERIC and SAFE
(a placeholder port, tiny volumes, low rates).  It is NOT meant to hold your
machine's real port.  There are three ways to set per-machine values, in order
of priority (later wins):

    1. The generic defaults in this file (committed).
    2. Environment variables   CHEMYX_PORT / CHEMYX_BAUD / CHEMYX_CHANNEL.
    3. An untracked  config_local.py  next to this file (gitignored).
       Copy config_local.example.py -> config_local.py and edit it.

So a fresh clone on a new machine needs NO edits to this file: set an env var or
drop in a config_local.py.  The mock/dry-run path doesn't even need that.

Quick reference for the bits people change most:
    PORT        -> the COM port (Windows) or /dev/tty.* device (Mac/Linux)
    BAUD_RATE   -> must match the baud rate shown on the pump's screen
    CHANNEL     -> which pump drive on the dual-channel 4000X to talk to
    DIAMETER    -> inner diameter (mm) of the syringe you loaded
"""

import os


def _env(name, default, cast=str):
    """Read an override from the environment, falling back to `default`."""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return cast(raw.strip())
    except (TypeError, ValueError):
        return default


# =============================================================================
# USER SETTINGS  --  GENERIC defaults.  DO NOT hardcode your real port here.
# Override per-machine with env vars or config_local.py (see the docstring).
# =============================================================================

# --- Serial port -------------------------------------------------------------
# Generic placeholder.  Your real port WILL differ per machine — find it with
# hardware_bringup/list_ports.py, then set CHEMYX_PORT or config_local.py.
# Windows : something like "COM3"  (Device Manager > Ports (COM & LPT))
# Mac     : something like "/dev/tty.usbserial-XXXX"
# Linux   : something like "/dev/ttyUSB0"
PORT = _env("CHEMYX_PORT", "COM3")

# Baud rate configured on the pump.  Chemyx pumps are commonly 9600 or 38400.
# This MUST match the value on the pump's System / Settings screen.
BAUD_RATE = _env("CHEMYX_BAUD", 9600, int)

# --- Channel selection (Fusion 4000X is DUAL channel) ------------------------
# The 4000X has two independent pump drives.  How channels are addressed:
#   0 -> default / both  (command is sent with NO channel prefix)
#   1 -> channel 1 only  (command is prefixed with "1 ")
#   2 -> channel 2 only  (command is prefixed with "2 ")
# Single-channel pumps (e.g. Fusion 200X) just leave this at 0.
CHANNEL = _env("CHEMYX_CHANNEL", 0, int)

# --- Syringe / flow defaults -------------------------------------------------
# Inner diameter of the loaded syringe in mm (check the syringe spec sheet).
DIAMETER = _env("CHEMYX_DIAMETER", 4.5, float)

# Default flow rate, expressed in the DEFAULT_UNITS chosen below.
DEFAULT_RATE = _env("CHEMYX_RATE", 1.0, float)

# Default volume to move (in mL when units are mL/min or mL/hr).
# Kept deliberately small as a safety default for the demo in main.py.
DEFAULT_VOLUME = _env("CHEMYX_VOLUME", 0.5, float)

# --- First-light (hardware_bringup) safe amounts -----------------------------
# Intentionally TINY: the very first real movement should barely move at all.
# Used by hardware_bringup/first_light.py.
FIRST_LIGHT_VOLUME = _env("CHEMYX_FIRST_LIGHT_VOLUME", 0.05, float)  # mL
FIRST_LIGHT_RATE = _env("CHEMYX_FIRST_LIGHT_RATE", 0.5, float)      # in DEFAULT_UNITS


# =============================================================================
# UNITS
# =============================================================================
# Chemyx "set units [x]" takes an integer code.  Mapping used by the firmware:
UNITS = {
    0: "mL/min",
    1: "mL/hr",
    2: "uL/min",
    3: "uL/hr",
}

# Reverse lookup so you can pass a human string ("mL/min") OR the code (0).
UNITS_BY_NAME = {
    "ml/min": 0, "mlmin": 0,
    "ml/hr": 1, "ml/h": 1, "mlhr": 1,
    "ul/min": 2, "ulmin": 2, "µl/min": 2, "μl/min": 2,
    "ul/hr": 3, "ul/h": 3, "ulhr": 3, "µl/hr": 3, "μl/hr": 3,
}

# Default units for the session (code from the UNITS table above).
DEFAULT_UNITS = 0  # mL/min


# =============================================================================
# HARDWARE LIMITS  (from the Fusion 4000X spec sheet)
# =============================================================================
# Syringe inner-diameter range, millimetres.
DIAMETER_MIN = 0.103
DIAMETER_MAX = 40.000

# Volumetric flow-rate limits depend on the active units.  The 4000X spans
# ~0.0001 uL/min up to 170.5 mL/min.  These (min, max) pairs are that physical
# envelope converted into each unit so set_rate() can validate any units.
RATE_LIMITS = {
    0: (1e-7, 170.5),        # mL/min
    1: (6e-6, 10230.0),      # mL/hr
    2: (0.0001, 170500.0),   # uL/min
    3: (0.006, 10230000.0),  # uL/hr
}


# =============================================================================
# SAFETY CAPS  (soft limits used by the demo / your own scripts)
# =============================================================================
# These are intentionally conservative.  They are *not* the hardware maximums;
# they exist so a typo in a script can't command a huge rate or volume.
SAFE_MAX_RATE = 50.0     # in DEFAULT_UNITS
SAFE_MAX_VOLUME = 5.0    # in mL (when using mL units)


# =============================================================================
# SERIAL FRAMING  (rarely needs changing)
# =============================================================================
# 8 data bits, no parity, 1 stop bit, commands terminated with a carriage return.
BYTESIZE = 8
PARITY = "N"
STOPBITS = 1
TIMEOUT = 1.0            # seconds to wait for a response
COMMAND_TERMINATOR = "\r"


# =============================================================================
# Helpers
# =============================================================================
def resolve_units(units):
    """Accept either a units code (0-3) or a human string and return the code."""
    if isinstance(units, int):
        if units not in UNITS:
            raise ValueError(f"Unknown units code {units}; valid codes: {list(UNITS)}")
        return units
    key = str(units).strip().lower()
    if key in UNITS_BY_NAME:
        return UNITS_BY_NAME[key]
    raise ValueError(f"Unknown units '{units}'; try one of {sorted(set(UNITS.values()))}")


def rate_limits(units):
    """Return the (min, max) valid flow rate for the given units code/name."""
    return RATE_LIMITS[resolve_units(units)]


# =============================================================================
# PER-MACHINE LOCAL OVERRIDES  (highest priority; untracked / gitignored)
# =============================================================================
# If a `config_local.py` sits next to this file, anything it defines wins over
# everything above (including env vars).  This is the easiest per-machine knob:
# copy config_local.example.py -> config_local.py and edit PORT/BAUD_RATE/CHANNEL.
# It is in .gitignore, so it never gets committed and never travels between
# machines.  Absent on a fresh clone -> we silently fall back to the defaults.
try:
    from config_local import *  # noqa: F401,F403  (per-machine overrides)
except ImportError:
    pass
