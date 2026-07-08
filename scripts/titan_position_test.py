"""Read or change the position of an IDEX/Rheodyne Titan or MX II controller."""

from __future__ import annotations

import argparse
import sys

import serial_hello_test


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Test an IDEX/Rheodyne Titan or MX II controller using its "
            "documented position protocol."
        )
    )
    parser.add_argument("--port", default="COM7", help="serial port to open")
    parser.add_argument(
        "--baud",
        type=int,
        default=19200,
        help="baud rate; the manufacturer default is 19200",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=1.0,
        help="serial read/write timeout in seconds",
    )
    parser.add_argument(
        "--motion-timeout",
        type=float,
        default=10.0,
        help="maximum seconds to wait for movement to finish",
    )

    operation = parser.add_mutually_exclusive_group()
    operation.add_argument(
        "--read",
        action="store_true",
        help="read the current position without moving the mechanism",
    )
    operation.add_argument(
        "--position",
        type=int,
        choices=range(1, 13),
        metavar="1-12",
        help="move to the requested indexed position",
    )
    operation.add_argument(
        "--identify",
        action="store_true",
        help="find the baud rate and read controller information",
    )
    operation.add_argument(
        "--home",
        action="store_true",
        help="run the controller's home movement",
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="list visible serial ports and exit",
    )
    return parser


def translate_arguments(args: argparse.Namespace) -> list[str]:
    forwarded = [
        "--port",
        args.port,
        "--baud",
        str(args.baud),
        "--timeout",
        str(args.timeout),
        "--motion-timeout",
        str(args.motion_timeout),
    ]

    if args.list_only:
        forwarded.append("--list-only")
    elif args.identify:
        forwarded.append("--identify")
    elif args.position is not None:
        forwarded.extend(("--position", str(args.position)))
    elif args.home:
        forwarded.append("--home")
    else:
        # Reading position is the safe default when no movement was requested.
        forwarded.extend(("--read", "status"))

    return forwarded


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return serial_hello_test.main(translate_arguments(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
