"""Parse self-describing input/holding register reports."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType

from .protocol import Frame, ProtocolError


@dataclass(frozen=True, slots=True)
class RegisterReport:
    logger: str
    inverter: str
    recorded_at: datetime | None
    namespace: str
    registers: Mapping[int, int]

    def unsigned(self, address: int, words: int = 1) -> int:
        if words not in (1, 2, 4):
            raise ValueError("A value must contain one, two or four words")
        value = 0
        for index in range(address, address + words):
            value = (value << 16) | self.registers[index]
        return value

    def signed(self, address: int, words: int = 1) -> int:
        value = self.unsigned(address, words)
        bits = 16 * words
        return value - (1 << bits) if value & (1 << (bits - 1)) else value


def _identity(data: bytes) -> str:
    value, separator, padding = data.partition(b"\x00")
    if separator and any(padding):
        raise ProtocolError("Identity contains non-zero padding")
    if not value or any(octet < 33 or octet > 126 for octet in value):
        raise ProtocolError("Invalid device identity")
    return value.decode("ascii")


def parse_register_report(frame: Frame) -> RegisterReport:
    """Decode the version-6 report with 30-byte identities and register ranges.

    This format is not assumed for other protocol versions or fixed-offset
    layouts. A structurally different report raises ProtocolError.
    """
    if frame.protocol != 6 or frame.function not in (3, 4):
        raise ProtocolError("This frame is not a version-6 register report")
    payload = frame.payload
    if len(payload) < 67:
        raise ProtocolError("Truncated register-report header")
    logger, inverter = _identity(payload[:30]), _identity(payload[30:60])
    stamp = payload[60:66]
    try:
        recorded_at = datetime(2000 + stamp[0], *stamp[1:])
    except ValueError:
        recorded_at = None
    count = payload[66]
    if not count:
        raise ProtocolError("Register report has no ranges")
    offset = 67
    values: dict[int, int] = {}
    for _ in range(count):
        if offset + 4 > len(payload):
            raise ProtocolError("Truncated register-range header")
        start = int.from_bytes(payload[offset : offset + 2], "big")
        end = int.from_bytes(payload[offset + 2 : offset + 4], "big")
        offset += 4
        if end < start or offset + 2 * (end - start + 1) > len(payload):
            raise ProtocolError("Invalid or truncated register range")
        for address in range(start, end + 1):
            if address in values:
                raise ProtocolError("Overlapping register ranges")
            values[address] = int.from_bytes(payload[offset : offset + 2], "big")
            offset += 2
    if offset != len(payload):
        raise ProtocolError("Unrecognised bytes after the register ranges")
    return RegisterReport(
        logger,
        inverter,
        recorded_at,
        "input" if frame.function == 4 else "holding",
        MappingProxyType(values),
    )
