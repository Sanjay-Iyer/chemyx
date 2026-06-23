"""
mock_serial.py — A fake serial port that imitates a Chemyx Fusion 4000X.

This lets you run every script and the whole test suite WITHOUT real hardware.
It mimics just enough of pyserial's `serial.Serial` API (write/read/readline/
close/is_open/in_waiting) and, crucially, it echoes back the value it "sets" the
same way the real pump does (e.g. you send "set diameter 4.5" and it replies
"diameter = 4.5").  That echo is what the Pump class verifies against.

It is intentionally simple and dependency-free.
"""

import config


class MockChemyxSerial:
    """Drop-in stand-in for serial.Serial that behaves like a 4000X pump."""

    def __init__(self, port=None, baudrate=9600, bytesize=8, parity="N",
                 stopbits=1, timeout=1.0, **kwargs):
        self.port = port
        self.baudrate = baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        self.is_open = True

        # Bytes waiting to be read by the host (pump -> host direction).
        self._rx = bytearray()
        # Log of everything the host wrote (host -> pump), for inspection/tests.
        self.tx_log = []

        # Internal "pump state" so responses are self-consistent.
        self.state = {
            "diameter": None,
            "rate": None,
            "volume": None,
            "units": config.DEFAULT_UNITS,
            "running": False,
            "paused": False,
        }

    # -- pyserial-compatible surface -----------------------------------------
    @property
    def in_waiting(self):
        return len(self._rx)

    def write(self, data):
        text = data.decode("ascii", errors="replace")
        self.tx_log.append(text)
        # A single write may contain several CR-terminated commands.
        for line in text.replace("\n", "\r").split("\r"):
            line = line.strip()
            if line:
                self._handle(line)
        return len(data)

    def read(self, size=1):
        if not self._rx:
            return b""
        chunk = bytes(self._rx[:size])
        del self._rx[:size]
        return chunk

    def readline(self):
        idx = self._rx.find(b"\n")
        if idx == -1:
            chunk = bytes(self._rx)
            self._rx.clear()
            return chunk
        chunk = bytes(self._rx[: idx + 1])
        del self._rx[: idx + 1]
        return chunk

    def reset_input_buffer(self):
        self._rx.clear()

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False

    # -- pump emulation -------------------------------------------------------
    def _respond(self, text):
        self._rx.extend((text + "\r\n").encode("ascii"))

    def _handle(self, line):
        parts = line.split()
        # Strip an optional leading single-digit channel address ("2 set ...").
        if parts and len(parts[0]) == 1 and parts[0] in "1234":
            parts = parts[1:]
        if not parts:
            return
        cmd = " ".join(parts).lower()

        if cmd.startswith("set diameter") and len(parts) >= 3:
            val = parts[2]
            self.state["diameter"] = val
            self._respond(f"diameter = {val}")
        elif cmd.startswith("set rate") and len(parts) >= 3:
            val = parts[2]
            self.state["rate"] = val
            self._respond(f"rate = {val}")
        elif cmd.startswith("set volume") and len(parts) >= 3:
            val = parts[2]
            self.state["volume"] = val
            self._respond(f"volume = {val}")
        elif cmd.startswith("set units") and len(parts) >= 3:
            code = int(parts[2])
            self.state["units"] = code
            self._respond(f"units = {config.UNITS.get(code, code)}")
        elif cmd.startswith("set delay") and len(parts) >= 3:
            self._respond(f"delay = {parts[2]}")
        elif cmd.startswith("set primerate") and len(parts) >= 3:
            self._respond(f"primerate = {parts[2]}")
        elif cmd == "start":
            self.state["running"] = True
            self.state["paused"] = False
            self._respond("pump start")
        elif cmd == "stop":
            self.state["running"] = False
            self.state["paused"] = False
            self._respond("pump stop")
        elif cmd == "pause":
            self.state["paused"] = True
            self._respond("pump pause")
        elif cmd.startswith("echo"):
            self._respond(line)
        elif cmd == "help":
            self._respond(
                "Commands: start | stop | pause | "
                "set units [x] | set diameter [x.x] | set rate [x.x] | "
                "set volume [x.x] | set delay [xxx] | set primerate [x.x] | "
                "echo on | echo off | help"
            )
        else:
            # Unknown command: echo it back, like the real firmware does.
            self._respond(line)
