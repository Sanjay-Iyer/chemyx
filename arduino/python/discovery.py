"""Explicit Arduino port selection and collision detection."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .errors import PortCollisionError, PortSelectionError


@dataclass(frozen=True)
class PortInfo:
    device: str
    description: str = ""
    hwid: str = ""
    vid: int | None = None
    pid: int | None = None
    serial_number: str | None = None
    manufacturer: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def list_serial_ports(list_ports_fn=None) -> list[PortInfo]:
    if list_ports_fn is None:
        try:
            from serial.tools import list_ports
        except ImportError as exc:
            raise PortSelectionError("pyserial is required to list serial ports") from exc
        list_ports_fn = list_ports.comports
    return sorted(
        [
            PortInfo(
                device=str(item.device),
                description=str(item.description or ""),
                hwid=str(item.hwid or ""),
                vid=getattr(item, "vid", None),
                pid=getattr(item, "pid", None),
                serial_number=getattr(item, "serial_number", None),
                manufacturer=getattr(item, "manufacturer", None),
            )
            for item in list_ports_fn()
        ],
        key=lambda item: item.device.casefold(),
    )


def _matches(info: PortInfo, fingerprint: dict) -> bool:
    checks = {
        "vid": info.vid,
        "pid": info.pid,
        "serial_number": info.serial_number,
        "manufacturer": info.manufacturer,
    }
    supplied = False
    for key, actual in checks.items():
        expected = fingerprint.get(key)
        if expected in (None, ""):
            continue
        supplied = True
        if key in {"vid", "pid"}:
            if int(expected) != actual:
                return False
        elif str(expected).casefold() != str(actual or "").casefold():
            return False
    if not supplied:
        raise PortSelectionError("Arduino fingerprint contains no identifying fields")
    return True


def resolve_arduino_port(
    explicit_port: str | None,
    fingerprint: dict | None,
    ports: list[PortInfo] | None = None,
) -> PortInfo:
    available = list_serial_ports() if ports is None else list(ports)
    if explicit_port:
        matches = [p for p in available if p.device.casefold() == explicit_port.casefold()]
        if len(matches) != 1:
            raise PortSelectionError(f"Configured Arduino port {explicit_port!r} is not present")
        selected = matches[0]
        if fingerprint and not _matches(selected, fingerprint):
            raise PortSelectionError(
                f"Device on {explicit_port} does not match the configured Arduino fingerprint"
            )
        return selected
    if not fingerprint:
        raise PortSelectionError(
            "Set arduino.port explicitly or provide a verified unique device fingerprint"
        )
    matches = [item for item in available if _matches(item, fingerprint)]
    if len(matches) != 1:
        raise PortSelectionError(
            f"Arduino fingerprint matched {len(matches)} ports; exactly one is required"
        )
    return matches[0]


def ensure_distinct_ports(arduino_port: str | None, chemyx_port: str | None) -> None:
    if arduino_port and chemyx_port and arduino_port.casefold() == chemyx_port.casefold():
        raise PortCollisionError(
            f"Arduino and Chemyx are both configured for {arduino_port}; ports must be distinct"
        )


def format_port_table(ports: list[PortInfo]) -> str:
    rows = ["PORT                 DESCRIPTION                         VID:PID      SERIAL"]
    for item in ports:
        pair = ""
        if item.vid is not None and item.pid is not None:
            pair = f"{item.vid:04X}:{item.pid:04X}"
        rows.append(
            f"{item.device:<20} {item.description[:35]:<35} {pair:<12} {item.serial_number or ''}"
        )
    return "\n".join(rows)

