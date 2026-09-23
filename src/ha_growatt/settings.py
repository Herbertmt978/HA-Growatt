"""Load bridge settings without including credentials in diagnostics."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

from .publisher import MqttSettings
from .relay import RelaySettings
from .runtime_options import RuntimeOptions
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
    runtime: RuntimeOptions = RuntimeOptions()

    def decoder(self) -> Decoder | FamilyDecoder:
        if self.runtime.compatibility:
            from .compat import CompatibilityDecoder

            return CompatibilityDecoder(
                self.runtime.inverter_identity, self.runtime.value_offset, self.runtime.decrypt
            )
        if self.wire_profile == "auto":
            from .custom_layouts import load_layouts

            layouts = (
                load_layouts(self.runtime.layouts_directory)
                if self.runtime.layouts_directory
                else None
            )
            return FamilyDecoder(
                self.selection, include_all=self.mqtt.include_all, custom_layouts=layouts
            )
        return Decoder(self.wire_profile, include_all=self.mqtt.include_all)


def load_settings(path: Path, environment: dict[str, str] | None = None) -> Settings:
    try:
        return _load_settings(path, environment)
    except (tomllib.TOMLDecodeError, json.JSONDecodeError, UnicodeError):
        raise ConfigurationError(
            "The configuration could not be read. Check the file syntax."
        ) from None
    except TypeError:
        raise ConfigurationError("A configuration option has the wrong type.") from None
    except ValueError as error:
        # Validation below and in the bridge settings uses fixed messages only.
        raise ConfigurationError(str(error)) from None


def _load_settings(path: Path, environment: dict[str, str] | None = None) -> Settings:
    if path.suffix.lower() in {".ini", ".json"}:
        from .legacy_config import load_legacy_options

        relay, mqtt, selection, runtime = load_legacy_options(path, environment)
        return Settings(relay, mqtt, "auto", selection, runtime)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if set(data) - {"relay", "mqtt", "wire_profile", "selection", "runtime", "publication"}:
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
        variables = os.environ if environment is None else environment
        mqtt_options["password"] = variables.get("HA_GROWATT_MQTT_PASSWORD", "")
        broker = MqttSettings(**mqtt_options)
        selection = SelectionSettings(**data.get("selection", {}))
        from .outputs import PublicationPolicy

        runtime_options = dict(data.get("runtime", {}))
        if set(runtime_options) - {
            "mode",
            "home_assistant",
            "ha_features",
            "ha_controls",
            "experimental_controls",
            "control_models",
            "hardware",
            "dataloggers",
            "settings_refresh_seconds",
            "buffered_events",
            "minimum_record_bytes",
            "layouts_directory",
            "sniff_interface",
            "api_host",
            "api_port",
            "diagnostic_logging",
        }:
            raise ValueError("Unknown runtime option")
        if "layouts_directory" in runtime_options:
            runtime_options["layouts_directory"] = Path(runtime_options["layouts_directory"])
        from .legacy_config import _integer

        for variable, key in {
            "gmode": "mode",
            "HA_GROWATT_API_HOST": "api_host",
            "HA_GROWATT_API_PORT": "api_port",
        }.items():
            if variable in variables:
                runtime_options[key] = (
                    _integer(variables[variable]) if key == "api_port" else variables[variable]
                )
        if "ggrottport" in variables:
            relay = replace(relay, listen_port=_integer(variables["ggrottport"]))
        policy = (
            PublicationPolicy(**data["publication"])
            if "publication" in data
            else RuntimeOptions().policy
        )
        runtime = RuntimeOptions(**runtime_options, policy=policy)
    except TypeError as error:
        raise ValueError("Missing or unknown configuration option") from error
    return Settings(relay, broker, data["wire_profile"], selection, runtime)
