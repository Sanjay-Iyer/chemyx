"""Test 3: issue one deliberately confirmed reverse/forward cycle."""

from __future__ import annotations

import argparse
import time

import serial

from serial_test_utils import (
    ArduinoTestError,
    add_serial_arguments,
    open_arduino_serial,
    print_motion_safety_checklist,
    print_observation_checklist,
    require_run_confirmation,
    send_command_and_wait,
    serial_error_message,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="One-time slow DM542S reverse/forward test."
    )
    add_serial_arguments(parser)
    args = parser.parse_args()

    print_motion_safety_checklist()
    print(
        "\nThe physical clockwise/counterclockwise labels do not matter. The expected "
        "result is 200 slow pulses in reverse, a 2-second pause, then 200 slow "
        "pulses forward, ending approximately at the starting angle."
    )

    command_sent = False
    try:
        require_run_confirmation()
        with open_arduino_serial(args.port, args.baud) as board:
            command_sent = True
            send_command_and_wait(
                board,
                "MOVE -200",
                "DONE MOVE -200",
                timeout=15.0,
            )
            time.sleep(2.0)
            send_command_and_wait(
                board,
                "MOVE 200",
                "DONE MOVE 200",
                timeout=15.0,
            )
    except serial.SerialException as error:
        print(f"FAIL: {serial_error_message(error, args.port)}")
        raise SystemExit(1) from error
    except ArduinoTestError as error:
        print(f"FAIL: {error}")
        raise SystemExit(1) from error
    except KeyboardInterrupt:
        if command_sent:
            print(
                "\nCANCELLED: the serial port is closing. If motion is still occurring "
                "or anything is abnormal, turn off DM542S 24 V power immediately."
            )
        else:
            print("\nCANCELLED: no motion command was sent.")
        raise SystemExit(130)

    print("PASS: Arduino completed the single reverse/forward cycle")
    print_observation_checklist()


if __name__ == "__main__":
    main()
