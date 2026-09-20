"""Read user-supplied layout descriptions without executing Python expressions."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .profiles import wire_profiles
from .protocol import Frame, ProtocolError
from .telemetry import Telemetry

_LAYOUT_NAME = re.compile(r"T[0-9A-Fa-f]{2}(?:NNNN|[0-9A-Fa-f]{4})(?:X)?[A-Za-z0-9]*\Z")
_TYPES = {"num", "numx", "text", "log", "logpos", "logneg", "def"}


@dataclass(frozen=True, slots=True)
class LayoutField:
    name: str
    kind: str
    offset: int = 0
    size: int = 0
    divisor: int = 1
    included: bool = True
    position: int = 0


class CustomLayout:
    def __init__(self, name: str, description: dict) -> None:
        if not _LAYOUT_NAME.fullmatch(name) or not isinstance(description, dict):
            raise ValueError("Invalid custom layout name or definition")
        self.name = name
        self.description = description
        self.protocol = int(name[1:3], 16)
        self.fields = []
        self.date_offset = None
        self.log_offset = None
        self.device = None
        self.encrypted = str(description.get("decrypt", {}).get("value", "True")).lower() == "true"
        for key, definition in description.items():
            if not isinstance(key, str) or not isinstance(definition, dict):
                raise ValueError("Invalid custom layout field")
            if key in {"decrypt", "date", "logstart", "device"}:
                value = definition.get("value")
                if key == "date":
                    self.date_offset = self._offset(value)
                elif key == "logstart":
                    self.log_offset = self._offset(value)
                elif key == "device":
                    if not isinstance(value, str):
                        raise ValueError("A custom device name must be text")
                    self.device = value
                continue
            kind = definition.get("type", "num")
            if kind not in _TYPES:
                raise ValueError("Unknown custom layout field type")
            if kind == "def":
                continue
            size = definition.get("length", 0)
            divisor = definition.get("divide", 1)
            position = definition.get("pos", 0)
            if any(type(v) is not int for v in (size, divisor, position)):
                raise ValueError("Custom layout dimensions must be integers")
            if size < 0 or size > 65535 or divisor <= 0 or position < 0:
                raise ValueError("Custom layout dimensions are out of range")
            self.fields.append(
                LayoutField(
                    key.strip(),
                    kind,
                    self._offset(definition.get("value", 0)),
                    size,
                    divisor,
                    definition.get("incl", "yes") != "no",
                    position,
                )
            )

    @staticmethod
    def _offset(value: int) -> int:
        if type(value) is not int or value < 0 or value > 131070 or value % 2:
            raise ValueError("Custom layout offsets must address complete bytes")
        return value // 2

    def decode(self, frame: Frame, *, include_all: bool = False) -> Telemetry:
        if frame.protocol != self.protocol:
            raise ProtocolError("Frame does not match the custom layout protocol")
        wire = frame.to_bytes()
        data = wire[:8] + frame.payload if self.encrypted else wire
        logs = []
        if self.log_offset is not None:
            logs = data[self.log_offset :].decode("ascii", errors="replace").split(",")
        values = {}
        errors = 0
        sensors = {}
        for field in self.fields:
            if not field.included and not include_all:
                continue
            try:
                if field.kind.startswith("log"):
                    value = logs[field.position - 1]
                    if field.kind == "logpos" and float(value) <= 0:
                        value = 0
                    elif field.kind == "logneg" and float(value) >= 0:
                        value = 0
                else:
                    raw = data[field.offset : field.offset + field.size]
                    if not raw or (field.kind == "numx" and len(raw) != field.size):
                        raise ValueError("Missing field")
                    value = (
                        raw.decode("ascii")
                        if field.kind == "text"
                        else int.from_bytes(raw, "big", signed=field.kind == "numx")
                    )
                values[field.name] = value
                known = next(
                    (
                        schema["sensors"][field.name]
                        for schema in wire_profiles().values()
                        if field.name in schema["sensors"]
                    ),
                    {},
                )
                sensors[field.name] = known | {"source": field.name, "divisor": field.divisor}
            except (ValueError, IndexError, UnicodeError):
                errors += 1
        if self.device:
            values["device"] = self.device
        stamp = None
        if self.date_offset is not None:
            raw = data[self.date_offset : self.date_offset + 6]
            try:
                stamp = datetime(2000 + raw[0], *raw[1:])
            except (IndexError, TypeError, ValueError):
                pass
        return Telemetry(
            values, stamp, f"custom:{self.name}", frame.function == 80, errors, sensors
        )


def load_layouts(directory: Path) -> dict[str, CustomLayout]:
    result = {}
    for path in sorted(directory.iterdir()):
        if (
            not path.is_file()
            or not path.name.lower().startswith("t")
            or path.suffix.lower() != ".json"
        ):
            continue
        try:
            descriptions = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(descriptions, dict):
                raise ValueError
            for name, description in descriptions.items():
                result[name] = CustomLayout(name, description)
        except (ValueError, TypeError, KeyError):
            raise ValueError("A custom layout file contains an invalid definition") from None
    return result
