"""Serial-relative decoding for older configurable-offset installations."""

from .discovery import STANDARD_SENSORS, validate_identity
from .protocol import Frame, ProtocolError
from .telemetry import Telemetry

# Byte positions relative to the serial plus the configured offset. These were
# measured with single-byte changes at offsets 6 and 8 in synthetic records.
_FIELDS = {
    "pvstatus": (15, 2),
    "pvpowerin": (17, 4),
    "pv1voltage": (21, 2),
    "pv1current": (23, 2),
    "pv1watt": (25, 4),
    "pv2voltage": (29, 2),
    "pv2current": (31, 2),
    "pv2watt": (33, 4),
    "pvpowerout": (37, 4),
    "pvfrequentie": (41, 2),
    "pvgridvoltage": (43, 2),
    "pvenergytoday": (67, 4),
    "pvenergytotal": (71, 4),
    "pvtemperature": (79, 2),
    "pvipmtemperature": (97, 2),
}


class CompatibilityDecoder:
    def __init__(self, identity: str, offset: int = 6, decrypt: bool = True) -> None:
        validate_identity(identity)
        if len(identity) != 10 or identity == "automatic":
            raise ValueError("Compatibility decoding requires a ten-character inverter identity")
        if type(offset) is not int or offset < 0 or offset > 65535:
            raise ValueError("Compatibility offset is out of range")
        self.identity, self.offset, self.decrypt = identity, offset, decrypt

    def decode(self, frame: Frame) -> Telemetry:
        data = frame.payload if self.decrypt else frame.to_bytes()[8:]
        serial = data.find(self.identity.encode("ascii"))
        if serial < 0:
            raise ProtocolError("Configured inverter identity was not present")
        origin = serial + self.offset
        values = {"pvserial": self.identity}
        for key, (offset, width) in _FIELDS.items():
            raw = data[origin + offset : origin + offset + width]
            if len(raw) != width:
                raise ProtocolError("Truncated compatibility record")
            values[key] = int.from_bytes(raw, "big")
        if values["pvstatus"] not in {0, 1}:
            raise ProtocolError("Compatibility record has an invalid status")
        sensors = {
            sensor.key: {
                "source": sensor.key,
                "divisor": sensor.divisor,
                **({"unit": sensor.unit} if sensor.unit else {}),
                **({"device_class": sensor.device_class} if sensor.device_class else {}),
                **({"state_class": sensor.state_class} if sensor.state_class else {}),
            }
            for sensor in STANDARD_SENSORS
            if sensor.key in values
        }
        return Telemetry(
            values, None, "compatibility", frame.function == 80, sensor_metadata=sensors
        )
