"""Test 1: verify Python-to-Arduino USB serial communication only."""

from __future__ import annotations

import argparse

import serial

from serial_test_utils import (
    ArduinoTestError,
    add_serial_arguments,
    open_arduino_serial,
    send_command_and_wait,
    serial_error_message,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Serial-only Arduino UNO R4 hello-world test (DM542S power off)."
    )
    add_serial_arguments(parser)
    args = parser.parse_args()

    print("Keep the DM542S 24 V supply OFF for this serial-only test.")
    try:
        with open_arduino_serial(args.port, args.baud) as board:
            send_command_and_wait(
                board,
                "PING",
                "PONG",
                timeout=5.0,
                expected_is_prefix=True,
            )
    except serial.SerialException as error:
        print(f"FAIL: {serial_error_message(error, args.port)}")
        raise SystemExit(1) from error
    except ArduinoTestError as error:
        print(f"FAIL: {error}")
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        print("\nCANCELLED: no motion command was sent.")
        raise SystemExit(130)

    print("PASS: Arduino serial communication is working")


if __name__ == "__main__":
    main()
