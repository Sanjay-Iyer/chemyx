"""Shared Chemyx pump and NMR workflow helpers."""

__all__ = [
    "EchoMismatchError",
    "Pump",
    "PumpConnectionError",
    "PumpError",
]


def __getattr__(name):
    if name in __all__:
        from .pump import EchoMismatchError, Pump, PumpConnectionError, PumpError

        exports = {
            "EchoMismatchError": EchoMismatchError,
            "Pump": Pump,
            "PumpConnectionError": PumpConnectionError,
            "PumpError": PumpError,
        }
        return exports[name]
    raise AttributeError(name)
