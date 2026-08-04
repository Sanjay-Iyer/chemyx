"""Bounded, newline-delimited needle-controller protocol parsing."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .errors import ProtocolError

MAX_RESPONSE_CHARS = 512
_TOKEN = re.compile(r"^[A-Za-z0-9_.:+/-]+$")


@dataclass(frozen=True)
class Response:
    kind: str
    sequence: int | None = None
    command: str | None = None
    code: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    detail: str = ""
    raw: str = ""


def parse_fields(tokens: list[str]) -> tuple[dict[str, str], list[str]]:
    fields: dict[str, str] = {}
    remainder: list[str] = []
    for token in tokens:
        if "=" not in token:
            remainder.append(token)
            continue
        key, value = token.split("=", 1)
        if not key or not value or not _TOKEN.match(key) or not _TOKEN.match(value):
            raise ProtocolError(f"Malformed key/value token: {token!r}")
        fields[key.lower()] = value
    return fields, remainder


def parse_response(line: str) -> Response:
    text = line.strip("\r\n ")
    if not text:
        raise ProtocolError("Empty firmware response")
    if len(text) > MAX_RESPONSE_CHARS:
        raise ProtocolError("Firmware response exceeded bounded parser size")
    parts = text.split()
    kind = parts[0].upper()

    if kind == "READY":
        fields, remainder = parse_fields(parts[1:])
        if remainder:
            raise ProtocolError(f"Malformed READY response: {text!r}")
        return Response(kind=kind, fields=fields, raw=text)

    if kind in {"ACK", "DONE", "ERR"}:
        if len(parts) < 3:
            raise ProtocolError(f"Incomplete {kind} response: {text!r}")
        try:
            sequence = int(parts[1])
        except ValueError as exc:
            raise ProtocolError(f"Invalid sequence in response: {text!r}") from exc
        if sequence < 1:
            raise ProtocolError(f"Sequence must be positive: {text!r}")
        if kind == "ACK":
            return Response(kind=kind, sequence=sequence, command=" ".join(parts[2:]), raw=text)
        if kind == "ERR":
            return Response(
                kind=kind,
                sequence=sequence,
                code=parts[2],
                detail=" ".join(parts[3:]),
                raw=text,
            )
        fields, remainder = parse_fields(parts[2:])
        return Response(
            kind=kind,
            sequence=sequence,
            fields=fields,
            detail=" ".join(remainder),
            raw=text,
        )

    if kind == "EVENT":
        if len(parts) < 2:
            raise ProtocolError(f"Incomplete EVENT response: {text!r}")
        fields, remainder = parse_fields(parts[1:])
        return Response(kind=kind, fields=fields, detail=" ".join(remainder), raw=text)

    raise ProtocolError(f"Unknown firmware response type: {text!r}")


def bool_field(fields: dict[str, str], name: str) -> bool:
    try:
        value = fields[name].strip().lower()
    except KeyError as exc:
        raise ProtocolError(f"STATUS lacks required field {name!r}") from exc
    if value in {"1", "true", "on", "active", "yes"}:
        return True
    if value in {"0", "false", "off", "inactive", "no"}:
        return False
    raise ProtocolError(f"STATUS field {name!r} is not boolean: {fields[name]!r}")

