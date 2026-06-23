"""
hardware_bringup / list_ports.py
================================
List every serial (COM) port THIS machine can see, with its description and
hardware ID, so you can identify which one is the Chemyx pump.

WHY: the pump's port number is machine-specific. On your personal laptop it
might be COM3; on the work laptop it could be COM5, /dev/ttyUSB0, etc. This
script makes no assumptions — it just shows what's actually present here.

This script does NOT open or talk to the pump. It only enumerates ports, so it
is always safe to run.

RUN
---
    python hardware_bringup/list_ports.py
    python hardware_bringup/list_ports.py --mock   # prints a simulated example

Once you spot the pump's port, set it for this machine (env var or
config_local.py) — see ../BRINGUP-style notes in hardware_bringup/BRINGUP.md.
"""

import sys
from pathlib import Path

# Make the repo root importable no matter where this is launched from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

USE_MOCK = "--mock" in sys.argv


def _print_row(device, description, hwid):
    print(f"  {device:<22} | {description:<34} | {hwid}")


def _print_header():
    print()
    print("  " + "-" * 78)
    _print_row("PORT", "DESCRIPTION", "HARDWARE ID")
    print("  " + "-" * 78)


def list_mock():
    """Show what a typical result looks like, without touching hardware."""
    print("=" * 82)
    print(" SERIAL PORTS (MOCK / simulated example — not this machine's real ports)")
    print("=" * 82)
    _print_header()
    _print_row("COM5", "USB Serial Port (FTDI)", "USB VID:PID=0403:6001 SER=FT1ABCDE")
    _print_row("COM3", "Communications Port", "ACPI\\PNP0501")
    print("  " + "-" * 78)
    print()
    print("  (Example only.) On the real pump, look for an FTDI / 'USB Serial'")
    print("  entry — that's usually the Chemyx. Note its PORT, then set it for")
    print("  this machine via CHEMYX_PORT or config_local.py.")
    return 0


def list_real():
    # Imported here so --mock works even on a stripped-down Python install.
    from serial.tools import list_ports

    ports = sorted(list_ports.comports(), key=lambda p: p.device)

    print("=" * 82)
    print(" SERIAL PORTS VISIBLE ON THIS MACHINE")
    print("=" * 82)

    if not ports:
        print()
        print("  No serial ports found.")
        print()
        print("  Likely causes:")
        print("    * The pump is powered OFF or the USB-B / serial cable is unplugged.")
        print("    * The USB-to-serial DRIVER is not installed (install the Chemyx")
        print("      driver, or the FTDI/Prolific driver for your adapter).")
        print("    * Try a different USB port / cable.")
        print()
        print("  Fix the above, then re-run this script.")
        return 0

    _print_header()
    for p in ports:
        _print_row(p.device, p.description or "(no description)", p.hwid or "(no hwid)")
    print("  " + "-" * 78)
    print()
    print(f"  Found {len(ports)} port(s). The Chemyx is usually the FTDI / 'USB")
    print("  Serial' entry. Note its PORT name, then set it for this machine:")
    print("    Windows : $env:CHEMYX_PORT=\"COMx\"   (or edit config_local.py)")
    print("    Mac/Lin : export CHEMYX_PORT=/dev/tty...   (or edit config_local.py)")
    return 0


def main():
    if USE_MOCK:
        return list_mock()
    return list_real()


if __name__ == "__main__":
    sys.exit(main())
