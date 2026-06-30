"""Small Chemyx serial-port emulator used by tests and dry runs."""

from __future__ import annotations

from . import config


class MockChemyxSerial:
    """Drop-in stand-in for ``serial.Serial`` for Chemyx command tests."""

    def __init__(
        self,
        port=None,
        baudrate=None,
        bytesize=8,
        parity="N",
        stopbits=1,
        timeout=1.0,
        **kwargs,
    ):
        self.port = port
        self.baudrate = config.BAUD_RATE if baudrate is None else baudrate
        self.bytesize = bytesize
        self.parity = parity
        self.stopbits = stopbits
        self.timeout = timeout
        self.is_open = True
        self._rx = bytearray()
        self.tx_log = []
        self.state = {
            "diameter": None,
            "rate": None,
            "volume": None,
            "units": config.DEFAULT_UNITS,
            "running": False,
            "paused": False,
        }

    @property
    def in_waiting(self):
        return len(self._rx)

    def write(self, data):
        text = data.decode("ascii", errors="replace")
        self.tx_log.append(text)
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

    def read_all(self):
        return self.read(len(self._rx))

    def readline(self):
        idx = self._rx.find(b"\n")
        if idx == -1:
            return self.read_all()
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

    def _respond(self, text):
        self._rx.extend((text + "\r\n").encode("ascii"))

    def _handle(self, line):
        parts = line.split()
        if parts and len(parts[0]) == 1 and parts[0] in "1234":
            parts = parts[1:]
        if not parts:
            return
        cmd = " ".join(parts).lower()

        if cmd.startswith("set diameter") and len(parts) >= 3:
            self.state["diameter"] = parts[2]
            self._respond(f"diameter = {parts[2]}")
        elif cmd.startswith("set rate") and len(parts) >= 3:
            self.state["rate"] = parts[2]
            self._respond(f"rate = {parts[2]}")
        elif cmd.startswith("set volume") and len(parts) >= 3:
            self.state["volume"] = parts[2]
            self._respond(f"volume = {parts[2]}")
        elif cmd.startswith("set units") and len(parts) >= 3:
            code = int(parts[2])
            self.state["units"] = code
            self._respond(f"units = {config.UNITS.get(code, code)}")
        elif cmd.startswith("set delay") and len(parts) >= 3:
            self._respond(f"delay = {parts[2]}")
        elif cmd.startswith("set primerate") and len(parts) >= 3:
            self._respond(f"primerate = {parts[2]}")
        elif parts[0] == "start":
            self.state["running"] = True
            self.state["paused"] = False
            self._respond("pump start")
        elif parts[0] == "stop":
            self.state["running"] = False
            self.state["paused"] = False
            self._respond("pump stop")
        elif parts[0] == "pause":
            self.state["paused"] = True
            self._respond("pump pause")
        elif cmd.startswith("echo"):
            self._respond(line)
        elif cmd == "help":
            self._respond(
                "Commands: start | stop | pause | set units [x] | "
                "set diameter [x.x] | set rate [x.x] | set volume [x.x] | "
                "set delay [xxx] | set primerate [x.x] | echo on | echo off | help"
            )
        else:
            self._respond(line)
