"""Bounded support evidence containing no packet bodies or hardware identities."""

from __future__ import annotations

import time
from collections import Counter, deque
from dataclasses import dataclass


@dataclass
class ObservationStats:
    announcements: int = 0
    announcement_warnings: int = 0
    measurements: int = 0
    failed_measurements: int = 0
    incomplete_fields: int = 0
    buffered_records: int = 0


class SupportCapture:
    """Keep up to 256 frame summaries for ten minutes, only after an explicit start.

    Unknown layouts cannot be reliably scrubbed, so payload bytes are never copied.
    The export describes protocol traffic and decode results, not household readings.
    """

    def __init__(self):
        self._until = 0.0
        self._started = 0.0
        self._records = deque(maxlen=256)
        self._labels = {}

    def start(self):
        self._started = time.monotonic()
        self._until = self._started + 600
        self._records.clear()
        self._labels.clear()

    def stop(self):
        self._until = 0

    @property
    def active(self):
        return time.monotonic() < self._until

    def record(self, frame, result, telemetry=None):
        if not self.active:
            return
        identity = frame.payload[:10]
        if identity not in self._labels and len(self._labels) < 128:
            self._labels[identity] = f"Logger {len(self._labels) + 1}"
        row = {
            "seconds": round(time.monotonic() - self._started, 1),
            "logger": self._labels.get(identity, "Other logger"),
            "protocol": frame.protocol,
            "function": frame.function,
            "payload_bytes": len(frame.payload),
            "result": result,
        }
        if telemetry is not None:
            from .profiles import wire_profiles

            row.update(
                profile=telemetry.profile if telemetry.profile in wire_profiles() else "custom",
                decoded_fields=len(telemetry.values),
                incomplete_fields=telemetry.decode_errors,
                buffered=telemetry.buffered,
            )
        self._records.append(row)

    def export(self):
        return {
            "format": 1,
            "active": self.active,
            "limit": 256,
            "records": list(self._records),
            "summary": dict(Counter(row["result"] for row in self._records)),
        }
