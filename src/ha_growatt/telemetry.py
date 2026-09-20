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
    decode_errors: int = 0
    sensor_metadata: dict | None = None
    device_id: str | None = None


def default_profile(frame: Frame) -> str:
    if frame.protocol == 6 and frame.unit == 1 and frame.function == 32:
        return "meter-6"
    if frame.protocol == 6 and frame.unit == 80 and frame.function == 27:
        return "meter-log-6"
    family = "extended" if len(frame.to_bytes()) > 375 else "classic"
    profile = f"{family}-{frame.protocol}"
    if profile not in wire_profiles():
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
        if frame.protocol != schema["protocol"] or frame.function not in schema.get(
            "functions", [3, 4, 80]
        ):
            raise ProtocolError("Frame does not match the selected telemetry profile")
        width = 30 if frame.protocol == 6 else 10
        minimum = schema.get("log_offset", width * 2 + 6)
        if len(frame.payload) < minimum:
            raise ProtocolError("Truncated telemetry identity or timestamp")
        values: dict[str, int | str] = {}
        for field, offset in (("datalogserial", 0), ("pvserial", width)):
            if field in schema["text_fields"]:
                try:
                    identity = frame.payload[offset : offset + 10].decode("ascii").rstrip("\x00 ")
                    validate_identity(identity)
                except (ValueError, UnicodeError) as error:
                    raise ProtocolError("Telemetry identity is invalid") from error
                values[field] = identity
        errors = 0
        if "log_fields" in schema:
            columns = (
                frame.payload[schema["log_offset"] :].decode("ascii", errors="replace").split(",")
            )
            for key, (position, mode) in schema["log_fields"].items():
                try:
                    value = columns[position]
                    if mode != "both":
                        number = float(value)
                        if (mode == "positive" and number <= 0) or (
                            mode == "negative" and number >= 0
                        ):
                            value = 0
                    values[key] = value
                except (IndexError, ValueError):
                    errors += 1
            values.update(schema.get("constants", {}))
            return Telemetry(values, None, profile, False, errors, device_id=values.get("device"))
        for key, (offset, size, signed) in schema["numeric_fields"].items():
            if not self.include_all and key in schema["excluded_fields"]:
                continue
            if offset >= len(frame.payload):
                errors += 1
                continue
            if signed and offset + size > len(frame.payload):
                errors += 1
                continue
            values[key.strip()] = int.from_bytes(
                frame.payload[offset : offset + size], "big", signed=signed
            )
        stamp = frame.payload[width * 2 : width * 2 + 6]
        try:
            timestamp = datetime(2000 + stamp[0], *stamp[1:])
        except ValueError:
            timestamp = None
        if schema.get("timestamp") is False:
            timestamp = None
        identity = values.get("datalogserial") if profile.startswith("meter-") else None
        return Telemetry(
            values, timestamp, profile, frame.function == 80, errors, device_id=identity
        )
