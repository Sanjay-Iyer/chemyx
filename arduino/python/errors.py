"""Typed errors for the staged Arduino subsystem."""


class ArduinoError(RuntimeError):
    """Base class for Arduino subsystem failures."""


class ConfigurationError(ArduinoError):
    """Configuration is malformed or incomplete."""


class LiveExecutionBlocked(ConfigurationError):
    """A live test failed one or more required interlocks."""

    def __init__(self, test_name: str, missing: list[str]):
        self.test_name = test_name
        self.missing = list(missing)
        detail = "\n".join(f"- {item}" for item in self.missing)
        super().__init__(f"{test_name} blocked; missing requirements:\n{detail}")


class TransportError(ArduinoError):
    """The bounded serial transport failed."""


class PortSelectionError(TransportError):
    """No explicit or uniquely fingerprinted Arduino port is available."""


class PortCollisionError(ConfigurationError):
    """Two instruments were assigned the same serial port."""


class ProcessLockError(TransportError):
    """Another process owns the requested Arduino port lock."""


class ProtocolError(ArduinoError):
    """The firmware response violated the serial protocol."""


class ReadyTimeout(ProtocolError):
    """A valid READY identity was not received in time."""


class IdentityMismatch(ProtocolError):
    """READY identified an unexpected device, board, or firmware."""


class AckTimeout(ProtocolError):
    """ACK was not received before the command deadline."""


class DoneTimeout(ProtocolError):
    """DONE was not received before the command deadline."""


class CommandNotDispatched(ProtocolError):
    """The overall deadline expired before any bytes were written."""


class SequenceMismatch(ProtocolError):
    """A response sequence number did not match the active command."""


class DeviceError(ArduinoError):
    """Firmware returned ERR."""

    def __init__(self, sequence: int, code: str, detail: str = ""):
        self.sequence = sequence
        self.code = code
        self.detail = detail
        suffix = f" {detail}" if detail else ""
        super().__init__(f"Arduino ERR {sequence} {code}{suffix}")


class MotionInterlockError(ArduinoError):
    """Host-side policy prohibited a motion command."""


class PositionUncertainError(ArduinoError):
    """Interrupted motion made the commanded position uncertain."""


class HardRuntimeExceeded(ArduinoError):
    """A script exceeded its configured overall runtime ceiling."""
