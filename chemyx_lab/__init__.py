"""Shared Chemyx pump, MX valve, and NMR workflow helpers."""

__all__ = [
    "EchoMismatchError",
    "Pump",
    "PumpConnectionError",
    "PumpError",
    "MX_valve",
    "ValveConnectionError",
    "ValveError",
    "ValveReportedError",
    "ValveTimeoutError",
    "find_address",
]

_PUMP_EXPORTS = {
    "EchoMismatchError",
    "Pump",
    "PumpConnectionError",
    "PumpError",
}
_VALVE_EXPORTS = {
    "MX_valve",
    "ValveConnectionError",
    "ValveError",
    "ValveReportedError",
    "ValveTimeoutError",
    "find_address",
}


def __getattr__(name):
    if name in _PUMP_EXPORTS:
        from .instruments import chemyx as pump

        return getattr(pump, name)
    if name in _VALVE_EXPORTS:
        from .instruments import valve

        return getattr(valve, name)
    raise AttributeError(name)
