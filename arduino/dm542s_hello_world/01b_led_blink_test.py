"""Optional Test 1B: visibly blink the Arduino onboard LED ten times."""

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
        description="Blink the UNO R4 onboard LED ten times with DM542S power off."
    )
    add_serial_arguments(parser)
    args = parser.parse_args()

    print("Keep the DM542S 24 V supply OFF.")
    print("Watch the Arduino onboard LED: it should blink 10 times in about 2 seconds.")
    try:
        with open_arduino_serial(args.port, args.baud) as board:
            send_command_and_wait(
                board,
                "BLINK10",
                "DONE LED BLINK 10",
                timeout=6.0,
            )
    except serial.SerialException as error:
        print(f"FAIL: {serial_error_message(error, args.port)}")
        raise SystemExit(1) from error
    except ArduinoTestError as error:
        print(f"FAIL: {error}")
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        print("\nCANCELLED: no motor-motion command was sent.")
        raise SystemExit(130)

    print("PASS: Arduino completed all 10 onboard-LED blinks")


if __name__ == "__main__":
    main()
