"""Strict TOML configuration; secrets never appear in generated diagnostics."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .publisher import MqttSettings
from .relay import RelaySettings


@dataclass(frozen=True, slots=True)
class Settings:
    relay: RelaySettings
    mqtt: MqttSettings
    wire_profile: str


def load_settings(path: Path) -> Settings:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(data) - {"relay", "mqtt", "wire_profile"}:
        raise ValueError("Unknown configuration section")
    if not isinstance(data.get("wire_profile"), str):
        raise ValueError("An explicit wire_profile is required during development")
    try:
        relay = RelaySettings(**data.get("relay", {}))
        mqtt_options = dict(data.get("mqtt", {}))
        if "password" in mqtt_options:
            raise ValueError("Set the broker password through HA_GROWATT_MQTT_PASSWORD")
        mqtt_options["password"] = os.environ.get("HA_GROWATT_MQTT_PASSWORD", "")
        broker = MqttSettings(**mqtt_options)
    except TypeError as error:
        raise ValueError("Missing or unknown configuration option") from error
    return Settings(relay, broker, data["wire_profile"])
