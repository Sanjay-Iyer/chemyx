"""Small Chemyx serial-port emulator used by tests and dry runs."""

from __future__ import annotations

from .. import config


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


class MockMXValveSerial:
    """Emulates an IDEX MX Series II driver board per IDEX doc 2321382G.

    Faithful quirks that matter for testing:

    - While the (simulated) motor is moving, the board answers ANY incoming
      bytes with ``*`` and does not execute commands.
    - A ``P`` command for a position the valve does not have is silently
      ignored: no ack, no error, no movement.
    - When ``command_mode`` is not BCD (0x03), position commands over
      UART/USB are ignored while ``M`` (home) still works -- the suspected
      real-world failure on the MXX777-601 bring-up.
    - ``F`` stores a new command mode to non-volatile memory, but it only
      becomes ACTIVE after a board reset. Call :meth:`power_cycle` to apply.
    """

    BCD_MODE = 0x03

    def __init__(
        self,
        port=None,
        baudrate=19200,
        positions=2,
        command_mode=0x03,
        start_position=1,
        busy_polls=2,
        profile=0x02,
        firmware=0x23,
        force_status_error=None,
        lag_polls=0,
        **kwargs,
    ):
        self.port = port
        self.baudrate = baudrate
        self.is_open = True
        self._rx = bytearray()
        self.tx_log = []       # decoded command tokens, in order
        self.raw_writes = []   # every raw bytes object written, in order

        self.positions = positions
        self.position = start_position
        self._target = start_position
        self._busy_remaining = 0
        self._busy_polls = busy_polls
        # A real move takes time to start; lag_polls > 0 makes the first
        # status polls after an accepted P still report the OLD position.
        self._lag_polls_default = lag_polls
        self._lag_remaining = 0

        self.active_mode = command_mode
        self.stored_mode = command_mode
        self.profile = profile
        self.firmware = firmware
        self.last_error = 0x00
        self.force_status_error = force_status_error

    # -- serial-port surface -------------------------------------------------
    @property
    def in_waiting(self):
        return len(self._rx)

    def open(self):
        self.is_open = True

    def close(self):
        self.is_open = False

    def flush(self):
        pass

    def reset_input_buffer(self):
        self._rx.clear()

    def reset_output_buffer(self):
        pass

    def read(self, size=1):
        if not self._rx:
            return b""
        chunk = bytes(self._rx[:size])
        del self._rx[:size]
        return chunk

    def read_all(self):
        return self.read(len(self._rx))

    def write(self, data):
        self.raw_writes.append(bytes(data))
        text = data.decode("ascii", errors="replace")
        for token in text.split("\r"):
            token = token.strip()
            if token:
                self._handle(token)
        return len(data)

    # -- board behaviour -----------------------------------------------------
    def power_cycle(self):
        """Simulate unplugging the 24 V barrel jack: NVM settings go active."""
        self.active_mode = self.stored_mode
        self._busy_remaining = 0
        self._lag_remaining = 0

    def _reply(self, text):
        self._rx.extend(text.encode("ascii"))

    def _start_move(self, target):
        if target != self.position:
            self._target = target
            self._lag_remaining = self._lag_polls_default
            self._busy_remaining = self._busy_polls

    def _handle(self, token):
        self.tx_log.append(token)

        # Motion lag: the motor has not engaged yet, so the board still
        # answers normally but status reports the old position.
        if self._lag_remaining > 0:
            if token.upper() == "S":
                self._lag_remaining -= 1
                self._reply(f"{self.position:02X}\r")
                return
            self._lag_remaining = 0

        if self._busy_remaining > 0:
            # Doc 2321382G: during the motion profile the board answers any
            # incoming data with '*'.
            self._busy_remaining -= 1
            if self._busy_remaining == 0:
                self.position = self._target
            self._reply("*")
            return

        cmd = token.upper()
        if cmd == "S":
            if self.force_status_error is not None:
                self._reply(f"{self.force_status_error:02X}\r")
            else:
                self._reply(f"{self.position:02X}\r")
        elif cmd == "M":
            self._reply("\r")
            self._start_move(1)
        elif cmd.startswith("P") and len(cmd) == 3:
            try:
                value = int(cmd[1:], 16)
            except ValueError:
                return  # unrecognized -> no response
            if self.active_mode != self.BCD_MODE:
                return  # wrong command mode -> position commands ignored
            if not 1 <= value <= self.positions:
                return  # nonexistent position -> silently ignored
            self._reply("\r")
            self._start_move(value)
        elif cmd == "D":
            self._reply(f"{self.stored_mode:02X}\r")
        elif cmd.startswith("F") and len(cmd) == 3:
            try:
                value = int(cmd[1:], 16)
            except ValueError:
                return
            if 0x01 <= value <= 0x05:
                self.stored_mode = value
                self._reply("\r")
            # invalid mode -> no response (board would store error 77)
        elif cmd == "Q":
            self._reply(f"{self.profile:02X}\r")
        elif cmd == "R":
            self._reply(f"{self.firmware:02X}\r")
        elif cmd == "E":
            self._reply(f"{self.last_error:02X}\r")
        # anything else: unrecognized command -> no response
