"""Bounded, process-lifetime datalogger connection diagnostics."""

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Logger:
    identity: str
    connections: int = 0
    last_contact: str | None = None
    reconnects: list[str] = field(default_factory=list)

    def contact(self, *, new_connection=False):
        self.last_contact = datetime.now(UTC).isoformat()
        if new_connection:
            self.connections += 1
            if self.connections > 1:
                self.reconnects.append(self.last_contact)
                del self.reconnects[:-10]


def logger_identifier(identity):
    return f"ha_growatt_logger_{identity}"


def discovery(identity, hardware):
    identifier = logger_identifier(identity)
    device = {
        "identifiers": [identifier],
        "name": f"Growatt datalogger {identity}",
        "manufacturer": "Growatt",
    }
    for key, destination in (("model", "model"), ("firmware", "sw_version")):
        if hardware.get(key):
            device[destination] = hardware[key]
    root = f"ha_growatt/logger/{identity}"
    result = {}
    for key, label, options in (
        ("connection", "Data connection", {}),
        ("last_contact", "Last contact", {"device_class": "timestamp"}),
        (
            "upload_interval",
            "Observed upload interval",
            {"unit_of_measurement": "s", "device_class": "duration"},
        ),
        (
            "reconnects",
            "Reconnects since service start",
            {"json_attributes_topic": f"{root}/history"},
        ),
    ):
        result[f"homeassistant/sensor/ha_growatt/{identifier}_{key}/config"] = {
            "device": device,
            "name": label,
            "unique_id": f"{identifier}_{key}",
            "entity_category": "diagnostic",
            "state_topic": f"{root}/status",
            "value_template": "{{ value_json." + key + " }}",
            "expire_after": 30,
            "availability_topic": "ha_growatt/service/status",
            "availability_template": "{{ 'online' if value_json.online else 'offline' }}",
            **options,
        }
    return result
