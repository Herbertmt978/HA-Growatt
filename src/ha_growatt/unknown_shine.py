"""Small, value-free summaries of Shine packets needing a new layout."""

from __future__ import annotations

from collections import OrderedDict

from .profiles import wire_profiles

MAX_FORMATS = 8


class UnknownShineFormats:
    """Keep packet shapes, never identities, timestamps or payload contents."""

    def __init__(self) -> None:
        self._formats: OrderedDict[tuple, int] = OrderedDict()
        self.total_frames = 0
        self.other_frames = 0

    def observe(self, frame, telemetry=None) -> None:
        if telemetry is not None and not telemetry.decode_errors:
            return
        result = "incomplete_fields" if telemetry is not None else "decode_failed"
        profile = None
        if telemetry is not None:
            profile = telemetry.profile if telemetry.profile in wire_profiles() else "custom"
        shape = (result, frame.protocol, frame.function, len(frame.payload), profile)
        self.total_frames += 1
        if shape in self._formats:
            self._formats[shape] += 1
        elif len(self._formats) < MAX_FORMATS:
            self._formats[shape] = 1
        else:
            self.other_frames += 1

    def export(self) -> dict:
        return {
            "total_frames": self.total_frames,
            "other_frames": self.other_frames,
            "formats": [
                {
                    "result": result,
                    "protocol": protocol,
                    "function": function,
                    "payload_bytes": payload_bytes,
                    "profile": profile,
                    "frames": frames,
                }
                for (
                    result,
                    protocol,
                    function,
                    payload_bytes,
                    profile,
                ), frames in self._formats.items()
            ],
        }
