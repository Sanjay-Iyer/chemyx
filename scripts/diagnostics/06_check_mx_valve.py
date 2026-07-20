"""Safe standalone bring-up test for the MX Series II valve (MXX777-601).

The MXX777-601 is a 2-POSITION, 6-port switching valve: it has 6 fluidic
ports but only positions 1 and 2 exist. The MX II board silently ignores a
command to any other position, so this test never asks for one -- and it
proves that the driver now rejects such requests loudly before they reach
the wire.

What it does, in order:
  1. Lists serial ports and reports the valve's COM port + FTDI id.
  2. Connects and reads firmware, valve profile, command mode, stored error.
     Warns if the command mode is not BCD (required for USB control).
  3. Reads the current position with the S status query.
  4. Asks the driver for nonexistent position 5 and confirms it is rejected
     cleanly (ValueError) with nothing sent over the wire.
  5. Homes the valve (the proven-working M command) as a motion baseline.
  6. Toggles position 1 -> 2 -> 1 -> 2, waiting for ready and reading the
     position back after every move.

Every command and response is printed raw (bytes + hex) and decoded.

PowerShell examples (repo root, any Python with pyserial):

    python scripts\diagnostics\06_check_mx_valve.py --mock
    python scripts\diagnostics\06_check_mx_valve.py --mock --mock-level-logic
    python scripts\diagnostics\06_check_mx_valve.py
    python scripts\diagnostics\06_check_mx_valve.py --port REPLACE_WITH_COM_PORT
    python scripts\diagnostics\06_check_mx_valve.py --port REPLACE_WITH_COM_PORT --set-bcd

If position moves fail but home works, the usual cause is the command mode:
run with --set-bcd, power-cycle the 24 V supply, and run again.
See docs/valve/mx2_guide.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import _bootstrap  # noqa: F401

from chemyx_lab import config
from chemyx_lab.testing.mock_serial import MockMXValveSerial
from chemyx_lab.instruments.valve import (
    BCD_MODE,
    MX_valve,
    ValveError,
    describe_com_port,
    find_address,
)


TOGGLE_SEQUENCE = (1, 2, 1, 2)
INVALID_DEMO_POSITION = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safe MX Series II valve test: status, home, 1<->2 toggles."
    )
    parser.add_argument(
        "--machine-config",
        type=Path,
        default=config.REPO_ROOT / "configs" / "machines" / "00_machine.local.yaml",
        help="machine YAML config path",
    )
    parser.add_argument(
        "--port",
        default=None,
        help=(
            "serial port (default: machine YAML valve section, "
            "else auto-detect the FTDI FT232R)"
        ),
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=None,
        help="baud rate (default from config; MX II factory default is 19200)",
    )
    parser.add_argument(
        "--ports",
        type=int,
        default=None,
        help=(
            "selectable positions on the valve (default from config: "
            f"{config.VALVE_POSITIONS}). The MXX777-601 has 2."
        ),
    )
    parser.add_argument(
        "--motion-timeout",
        type=float,
        default=None,
        help="max seconds to wait for a move to finish (default from config)",
    )
    parser.add_argument(
        "--set-bcd",
        action="store_true",
        help=(
            "store BCD command mode (F03) if the board is not already in BCD, "
            "then stop so you can power-cycle the board"
        ),
    )
    parser.add_argument(
        "--skip-home",
        action="store_true",
        help="skip the home baseline step",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="run against a simulated MX II board (no hardware, no COM port)",
    )
    parser.add_argument(
        "--mock-level-logic",
        action="store_true",
        help=(
            "with --mock: simulate a board stuck in level-logic command mode "
            "(home works, position commands silently ignored)"
        ),
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="list visible serial ports and exit",
    )
    return parser


def print_port_table() -> list:
    try:
        from serial.tools import list_ports
    except ImportError:
        print("pyserial is not installed; cannot list ports.")
        print("Install it with: python -m pip install -r requirements.txt")
        return []
    ports = sorted(list_ports.comports(), key=lambda item: item.device)
    if not ports:
        print("No serial ports found.")
        return ports
    print("Visible serial ports:")
    for info in ports:
        print(f"  {describe_com_port(info)}")
    return ports


def resolve_port(args) -> str:
    if args.port:
        return args.port
    machine = config.load_machine_config(args.machine_config)
    if machine.valve.serial_port:
        print(f"Using configured port {machine.valve.serial_port} (machine YAML).")
        return machine.valve.serial_port
    print("No port configured; auto-detecting the FTDI FT232R (0403:6001)...")
    address = find_address()
    print(f"Auto-detected valve port: {address}")
    return address


def step(title: str) -> None:
    print()
    print(f"--- {title} ---")


def check_command_mode(valve: MX_valve, args) -> bool:
    """Report the command mode; return False if the test should stop."""
    mode, mode_name = valve.get_command_mode()
    print(f"Command mode: 0x{mode:02X} ({mode_name})")
    if mode == BCD_MODE:
        return True

    print()
    print("WARNING: the board is NOT in BCD command mode.")
    print("IDEX requires BCD (not level logic) for USB/I2C control; in the")
    print("wrong mode the board homes fine but IGNORES position commands --")
    print("exactly the observed symptom.")
    if not args.set_bcd:
        print("Fix: re-run this script with --set-bcd, then power-cycle the")
        print("board (unplug the 24 V supply), then run the test again.")
        print("Continuing anyway so the wire traffic below shows the failure.")
        return True

    print("Storing BCD command mode (F03)...")
    stored, stored_name = valve.set_command_mode_bcd()
    print(f"Command mode stored as 0x{stored:02X} ({stored_name}).")
    if hasattr(valve.ser, "power_cycle"):  # simulated board (--mock*)
        print("(mock) Simulating a power cycle to apply the stored mode.")
        valve.ser.power_cycle()
        return True
    print()
    print("NOW POWER-CYCLE THE BOARD: unplug the 24 V barrel jack, wait a few")
    print("seconds, plug it back in, then run this script again WITHOUT")
    print("--set-bcd. Stopping here because the new mode is inactive until")
    print("the board resets.")
    return False


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    machine = config.load_machine_config(args.machine_config)

    failures = 0

    step("Step 1: serial ports")
    address = "MOCK"
    if args.mock:
        print("Mock mode: simulating an MX II board, no COM port will be used.")
        if args.list_only:
            return 0
    else:
        print_port_table()
        if args.list_only:
            return 0
        try:
            address = resolve_port(args)
        except ValveError as exc:
            print(f"FAILED: {exc}")
            return 1

    serial_factory = None
    if args.mock and args.mock_level_logic:
        # Build the simulated board in the suspected broken state: home
        # works, position commands are silently ignored.
        ports = (
            args.ports
            if args.ports is not None
            else machine.valve.positions or config.VALVE_POSITIONS
        )

        def serial_factory(**kwargs):
            return MockMXValveSerial(positions=ports, command_mode=0x01, **kwargs)

    valve = MX_valve(
        address=address,
        ports=args.ports if args.ports is not None else machine.valve.positions,
        name="MXX777-601",
        verbose=True,  # print every TX/RX: raw bytes, hex, and decoded
        baud=args.baud if args.baud is not None else machine.valve.baud_rate,
        timeout=machine.valve.timeout_seconds,
        motion_timeout=(
            args.motion_timeout
            if args.motion_timeout is not None
            else machine.valve.motion_timeout_seconds
        ),
        mock=args.mock and serial_factory is None,
        serial_factory=serial_factory,
    )

    try:
        with valve:
            print(f"Connected to {valve.ser.port} at {valve.ser.baudrate} baud "
                  f"(8N1, CR-terminated, {valve.ports}-position valve).")

            step("Step 2: controller identity")
            print(f"Firmware revision: 0x{valve.get_firmware():02X}")
            print(f"Valve profile:     0x{valve.get_profile():02X}")
            error_code, error_text = valve.get_error()
            print(f"Stored error:      0x{error_code:02X} ({error_text})")
            if not check_command_mode(valve, args):
                return 0

            step("Step 3: current position")
            position = valve.get_port()
            print(f"Valve reports position {position}.")

            step(f"Step 4: reject nonexistent position {INVALID_DEMO_POSITION}")
            # The mock records every write; on real hardware validation is
            # pre-wire by construction, so the count check is mock-only.
            tx_before = (
                len(valve.ser.raw_writes)
                if hasattr(valve.ser, "raw_writes")
                else None
            )
            try:
                valve.change_port(INVALID_DEMO_POSITION)
            except ValueError as exc:
                print(f"PASS: rejected cleanly with ValueError: {exc}")
                print("      (validation happens before any bytes are sent)")
                if tx_before is not None and len(valve.ser.raw_writes) != tx_before:
                    print("FAIL: bytes WERE sent for the invalid position!")
                    failures += 1
            else:
                print("FAIL: invalid position was not rejected!")
                failures += 1

            if not args.skip_home:
                step("Step 5: home (proven-working baseline)")
                settled = valve.home()
                print(f"PASS: home finished; valve reports position {settled}.")
            else:
                step("Step 5: home (skipped)")

            step(f"Step 6: toggle positions {' -> '.join(map(str, TOGGLE_SEQUENCE))}")
            for target in TOGGLE_SEQUENCE:
                print(f"\nMoving to position {target}...")
                try:
                    valve.change_port(target)
                except ValveError as exc:
                    print(f"FAIL: {exc}")
                    failures += 1
                    continue
                readback = valve.get_port()
                if readback == target:
                    print(f"PASS: readback confirms position {readback}.")
                else:
                    print(f"FAIL: asked for {target} but valve reports "
                          f"{readback}.")
                    failures += 1
    except ValveError as exc:
        print(f"FAILED: {exc}")
        return 1

    step("Summary")
    if failures:
        print(f"{failures} step(s) FAILED. Scroll up: every TX/RX line shows "
              "exactly what went over the wire.")
        print("If home worked but moves failed, check the command mode "
              "(Step 2) and see docs/valve/mx2_guide.md.")
        return 1
    print("All steps passed: the valve toggled 1 -> 2 -> 1 -> 2 with "
          "confirmed readbacks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
