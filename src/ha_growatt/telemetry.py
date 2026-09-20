"""Decode documented scalar fields using separately observed wire profiles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .discovery import validate_identity
from .profiles import wire_profiles
from .protocol import Frame, ProtocolError


@dataclass(frozen=True, slots=True)
class Telemetry:
    values: dict[str, int | str]
    recorded_at: datetime | None
    profile: str
    buffered: bool = False


def default_profile(frame: Frame) -> str:
    profile = {
        (2, 215): "classic-2",
        (5, 215): "classic-5",
        (6, 255): "classic-6",
        (6, 575): "extended-6",
        (6, 829): "extended-6",
    }.get((frame.protocol, len(frame.payload)))
    if profile is None:
        raise ProtocolError("No verified default profile matches this frame")
    return profile


class Decoder:
    """An explicit profile prevents silently guessing between overlapping layouts."""

    def __init__(self, profile: str, *, include_all: bool = False) -> None:
        profiles = wire_profiles()
        if profile not in profiles and profile != "auto":
            raise ValueError("Unknown wire profile")
        self.profile = profile
        self._profiles = profiles
        self.include_all = include_all

    def decode(self, frame: Frame) -> Telemetry:
        profile = self.profile
        if profile == "auto":
            profile = default_profile(frame)
        schema = self._profiles[profile]
        if frame.protocol != schema["protocol"] or frame.function not in {3, 4, 80}:
            raise ProtocolError("Frame does not match the selected telemetry profile")
        if len(frame.payload) not in schema["verified_payload_lengths"]:
            raise ProtocolError("This packet size has not been verified for the selected profile")
        width = 30 if frame.protocol == 6 else 10
        values: dict[str, int | str] = {}
        for field, offset in (("datalogserial", 0), ("pvserial", width)):
            if field in schema["text_fields"]:
                try:
                    identity = frame.payload[offset : offset + 10].decode("ascii").rstrip("\x00 ")
                    validate_identity(identity)
                except (ValueError, UnicodeError) as error:
                    raise ProtocolError("Telemetry identity is invalid") from error
                values[field] = identity
        for key, (offset, size, signed) in schema["numeric_fields"].items():
            if not self.include_all and key in schema["excluded_fields"]:
                continue
            if offset + size > len(frame.payload):
                raise ProtocolError("Truncated telemetry field")
            values[key.strip()] = int.from_bytes(
                frame.payload[offset : offset + size], "big", signed=signed
            )
        stamp = frame.payload[width * 2 : width * 2 + 6]
        try:
            timestamp = datetime(2000 + stamp[0], *stamp[1:])
        except ValueError:
            timestamp = None
        return Telemetry(values, timestamp, profile, frame.function == 80)
