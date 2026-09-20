"""Load bridge settings without including credentials in diagnostics."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .publisher import MqttSettings
from .relay import RelaySettings
from .selection import FamilyDecoder, SelectionSettings
from .telemetry import Decoder


class ConfigurationError(ValueError):
    """A configuration diagnostic that contains no option values or credentials."""


@dataclass(frozen=True, slots=True)
class Settings:
    relay: RelaySettings
    mqtt: MqttSettings
    wire_profile: str
    selection: SelectionSettings = SelectionSettings()

    def decoder(self) -> Decoder | FamilyDecoder:
        if self.wire_profile == "auto":
            return FamilyDecoder(self.selection, include_all=self.mqtt.include_all)
        return Decoder(self.wire_profile, include_all=self.mqtt.include_all)


def load_settings(path: Path) -> Settings:
    try:
        return _load_settings(path)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, UnicodeError):
        raise ConfigurationError(
            "The configuration could not be read. Check the file syntax."
        ) from None
    except TypeError:
        raise ConfigurationError("A configuration option has the wrong type.") from None
    except ValueError as error:
        # Validation below and in the bridge settings uses fixed messages only.
        raise ConfigurationError(str(error)) from None


def _load_settings(path: Path) -> Settings:
    if path.suffix.lower() in {".ini", ".json"}:
        from .legacy_config import load_legacy_options

        relay, mqtt, selection = load_legacy_options(path)
        return Settings(relay, mqtt, "auto", selection)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(data) - {"relay", "mqtt", "wire_profile", "selection"}:
        raise ValueError("Unknown configuration section")
    if not isinstance(data.get("wire_profile"), str):
        raise ValueError("An explicit wire_profile is required during development")
    Decoder(data["wire_profile"])
    if "selection" in data and data["wire_profile"] != "auto":
        raise ValueError("Family selection requires wire_profile = auto")
    try:
        relay = RelaySettings(**data.get("relay", {}))
        mqtt_options = dict(data.get("mqtt", {}))
        if "password" in mqtt_options:
            raise ValueError("Set the broker password through HA_GROWATT_MQTT_PASSWORD")
        mqtt_options["password"] = os.environ.get("HA_GROWATT_MQTT_PASSWORD", "")
        broker = MqttSettings(**mqtt_options)
        selection = SelectionSettings(**data.get("selection", {}))
    except TypeError as error:
        raise ValueError("Missing or unknown configuration option") from error
    return Settings(relay, broker, data["wire_profile"], selection)
