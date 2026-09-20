"""Private, bounded snapshots for restoring measurements after a quiet restart."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from .discovery import validate_identity
from .telemetry import Telemetry

MAX_BYTES = 8 * 1024 * 1024
MAX_DEVICES = 128


@dataclass(frozen=True)
class Snapshot:
    telemetry: Telemetry
    received_at: datetime


class ReadingStore:
    def __init__(self, path: str, scope: tuple) -> None:
        self.path = Path(path)
        self.scope = list(scope)

    def load(self) -> dict[str, Snapshot]:
        if not self.path.exists():
            return {}
        if self.path.stat().st_size > MAX_BYTES:
            raise ValueError("Reading cache is too large")
        document = json.loads(self.path.read_text(encoding="utf-8"))
        if document.get("version") != 1 or document.get("scope") != self.scope:
            return {}
        records = document["readings"]
        if not isinstance(records, dict) or len(records) > MAX_DEVICES:
            raise ValueError("Invalid reading cache")
        result = {}
        for identity, record in records.items():
            validate_identity(identity)
            fields = record["telemetry"]
            values = fields["values"]
            if (
                not isinstance(values, dict)
                or len(values) > 2048
                or any(
                    not isinstance(k, str) or type(v) not in {int, str} for k, v in values.items()
                )
                or fields.get("buffered") is not False
                or (fields.get("device_id") or values.get("pvserial")) != identity
                or not isinstance(fields.get("profile"), str)
            ):
                raise ValueError("Invalid cached telemetry")
            received = datetime.fromisoformat(record["received_at"])
            if received.tzinfo is None:
                raise ValueError("Invalid cached timestamp")
            fields["recorded_at"] = (
                datetime.fromisoformat(fields["recorded_at"]) if fields["recorded_at"] else None
            )
            result[identity] = Snapshot(Telemetry(**fields), received)
        return result

    def save(self, readings: dict[str, Snapshot]) -> None:
        records = {}
        for identity, snapshot in readings.items():
            fields = asdict(snapshot.telemetry)
            fields["recorded_at"] = (
                snapshot.telemetry.recorded_at.isoformat()
                if snapshot.telemetry.recorded_at
                else None
            )
            records[identity] = {
                "telemetry": fields,
                "received_at": snapshot.received_at.isoformat(),
            }
        content = json.dumps(
            {"version": 1, "scope": self.scope, "readings": records}, allow_nan=False
        ).encode()
        if len(content) > MAX_BYTES or len(readings) > MAX_DEVICES:
            raise ValueError("Reading cache is too large")
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".readings-")
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
