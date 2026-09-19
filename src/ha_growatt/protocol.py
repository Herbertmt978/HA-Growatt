"""Bounded framing for Growatt TCP protocol versions 2, 5 and 6.

Wire facts and their evidence are recorded in docs/protocol.md.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

_HEADER = struct.Struct(">HHHBB")
_MASK = b"Growatt"
PROTOCOLS = frozenset({2, 5, 6})
MAX_FRAME_SIZE = 65543


class ProtocolError(ValueError):
    """The input cannot be interpreted as a complete supported frame."""


def checksum(data: bytes) -> int:
    """Calculate CRC-16/MODBUS, leaving byte order to the caller."""
    result = 0xFFFF
    for octet in data:
        result ^= octet
        for _ in range(8):
            result = (result >> 1) ^ (0xA001 if result & 1 else 0)
    return result


def mask_payload(data: bytes) -> bytes:
    """Apply the reversible payload XOR; this does not provide encryption."""
    return bytes(value ^ _MASK[index % len(_MASK)] for index, value in enumerate(data))


def frame_size(header: bytes) -> int:
    """Resolve the complete wire length from an eight-byte header."""
    if len(header) != _HEADER.size:
        raise ProtocolError("An eight-byte header is required")
    _, protocol, length, _, _ = _HEADER.unpack(header)
    if protocol not in PROTOCOLS:
        raise ProtocolError("Unsupported wire protocol")
    if length < 2:
        raise ProtocolError("Record length omits the unit and function bytes")
    return length + (6 if protocol == 2 else 8)


@dataclass(frozen=True, slots=True)
class Frame:
    """A frame with its payload in plaintext, excluding any checksum."""

    transaction: int
    protocol: int
    unit: int
    function: int
    payload: bytes

    def to_bytes(self) -> bytes:
        if self.protocol not in PROTOCOLS:
            raise ProtocolError("Unsupported wire protocol")
        if len(self.payload) > 65533:
            raise ProtocolError("Payload exceeds the wire length field")
        try:
            header = _HEADER.pack(
                self.transaction, self.protocol, len(self.payload) + 2, self.unit, self.function
            )
        except struct.error as error:
            raise ProtocolError("Header value outside its wire range") from error
        if self.protocol == 2:
            return header + self.payload
        body = header + mask_payload(self.payload)
        return body + checksum(body).to_bytes(2, "big")

    @classmethod
    def from_bytes(cls, data: bytes, *, verify_checksum: bool = True) -> Frame:
        expected = frame_size(data[:8])
        if len(data) != expected:
            raise ProtocolError("Frame size differs from its declared length")
        transaction, protocol, _, unit, function = _HEADER.unpack(data[:8])
        if protocol == 2:
            payload = data[8:]
        else:
            if verify_checksum and checksum(data[:-2]) != int.from_bytes(data[-2:], "big"):
                raise ProtocolError("Frame checksum does not match")
            payload = mask_payload(data[8:-2])
        return cls(transaction, protocol, unit, function, payload)


class FrameBuffer:
    """Split arbitrary TCP chunks; retain at most one incomplete frame."""

    def __init__(self) -> None:
        self._pending = bytearray()

    @property
    def pending_bytes(self) -> int:
        return len(self._pending)

    def feed(self, chunk: bytes) -> list[bytes]:
        result: list[bytes] = []
        offset = 0
        try:
            while offset < len(chunk):
                target = 8 if len(self._pending) < 8 else frame_size(bytes(self._pending[:8]))
                amount = min(target - len(self._pending), len(chunk) - offset)
                self._pending.extend(chunk[offset : offset + amount])
                offset += amount
                if len(self._pending) >= 8:
                    target = frame_size(bytes(self._pending[:8]))
                    if len(self._pending) == target:
                        result.append(bytes(self._pending))
                        self._pending.clear()
        except ProtocolError:
            self._pending.clear()
            raise
        return result

    def finish(self) -> None:
        if self._pending:
            self._pending.clear()
            raise ProtocolError("Stream ended during a frame")


async def read_frame(reader: asyncio.StreamReader, deadline_seconds: float) -> bytes | None:
    """Read a whole frame within one deadline, or return None for clean EOF."""
    async with asyncio.timeout(deadline_seconds):
        try:
            header = await reader.readexactly(8)
        except asyncio.IncompleteReadError as error:
            if not error.partial:
                return None
            raise ProtocolError("Stream ended during the header") from error
        try:
            return header + await reader.readexactly(frame_size(header) - 8)
        except asyncio.IncompleteReadError as error:
            raise ProtocolError("Stream ended during the payload") from error
