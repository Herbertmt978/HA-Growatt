"""Read existing proxy/HA settings; reject paths the bridge cannot yet run."""

from __future__ import annotations

import ast
import configparser
import json
import os
from dataclasses import replace
from pathlib import Path

from .publisher import MqttSettings
from .relay import RelaySettings
from .selection import SelectionSettings


def _boolean(value: object) -> bool:
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in {
        "true",
        "false",
        "yes",
        "no",
        "1",
        "0",
        "on",
        "off",
    }:
        return value.lower() in {"true", "yes", "1", "on"}
    raise ValueError("A configuration switch must be true or false")


def _integer(value: object) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise ValueError("A numeric configuration option must be an integer")


def _mapping(value: object) -> dict:
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return value
        raise ValueError("Configuration mapping keys must be text")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError, TypeError, RecursionError):
                raise ValueError(
                    "A configuration mapping must be a JSON object or dictionary"
                ) from None
        if isinstance(parsed, dict) and all(isinstance(key, str) for key in parsed):
            return parsed
    raise ValueError("A configuration mapping must be a JSON object or dictionary")


def _selection(get) -> SelectionSettings:
    if _mapping(get("Generic", "invtypemap", "ginvtypemap", "{}")):
        raise ValueError("Per-device family mappings are not yet supported")
    return SelectionSettings(
        family=get("Generic", "invtype", "ginvtype", "default"),
        strict=_boolean(get("Generic", "layout_strict", "glayoutstrict", False)),
        automatic=_boolean(get("Generic", "layout_auto_family", "glayoutautofamily", True)),
        minimum_score=_integer(get("Generic", "layout_min_score", "glayoutminscore", 20)),
    )


def _require_supported(get) -> None:
    if get("Generic", "mode", "gmode", "proxy") != "proxy":
        raise ValueError("Only proxy mode is available in this development version")
    if get("Generic", "time", "gtime", "auto") != "server":
        raise ValueError(
            "Inverter timestamp policy is not yet supported; keep using the existing service"
        )
    if _boolean(get("Generic", "sendbuf", "gsendbuf", True)):
        raise ValueError(
            "Buffered publication is not yet supported; keep using the existing service"
        )
    if not _boolean(get("MQTT", "nomqtt", "gnomqtt", False)):
        raise ValueError("Native MQTT output is not yet supported")
    if (
        not _boolean(get("extension", "extension", "gextension", False))
        or get("extension", "extname", "gextname", "") != "grottext.ha"
    ):
        raise ValueError("This configuration loader requires Home Assistant MQTT discovery")
    for section, key, variable in (
        ("Generic", "compat", "gcompat"),
        ("PVOutput", "pvoutput", "gpvoutput"),
        ("influx", "influx", "ginflux"),
    ):
        if _boolean(get(section, key, variable, False)):
            raise ValueError(f"The enabled {key} option is not yet supported")
    for key, variable, default in (
        ("minrecl", "gminrecl", 100),
        ("valueoffset", "gvalueoffset", 6),
    ):
        if _integer(get("Generic", key, variable, default)) != default:
            raise ValueError(f"A custom {key} option is not yet supported")


def _mqtt(options: dict) -> MqttSettings:
    allowed = {
        "ha_mqtt_host",
        "ha_mqtt_port",
        "ha_mqtt_user",
        "ha_mqtt_password",
        "ha_mqtt_retain",
        "ha_entity_profile",
    }
    if set(options) - allowed:
        raise ValueError("The Home Assistant extension contains an unsupported option")
    if any(
        not isinstance(options.get(key, ""), str)
        for key in ("ha_mqtt_host", "ha_mqtt_user", "ha_mqtt_password")
    ):
        raise ValueError("MQTT host and credentials must be text")
    return MqttSettings(
        host=options.get("ha_mqtt_host", "localhost"),
        port=_integer(options.get("ha_mqtt_port", 1883)),
        username=options.get("ha_mqtt_user", ""),
        password=options.get("ha_mqtt_password", ""),
        retain_state=_boolean(options.get("ha_mqtt_retain", False)),
        entity_profile=options.get("ha_entity_profile", "v0_1_9_standard"),
    )


def _addon(path: Path) -> tuple[RelaySettings, MqttSettings, SelectionSettings]:
    options = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(options, dict):
        raise ValueError("Home Assistant app options must be an object")
    allowed = {
        "mode",
        "blockcmd",
        "time",
        "sendbuf",
        "invtype",
        "layout_strict",
        "layout_auto_family",
        "diagnostic_logging",
        "ha_plugin",
        "ha_entity_profile",
        "mqtt_host",
        "mqtt_port",
        "mqtt_user",
        "mqtt_password",
        "mqtt_retain",
    }
    if set(options) - allowed:
        raise ValueError("The Home Assistant app contains an unsupported option")
    if options.get("mode", "proxy") != "proxy" or not _boolean(options.get("ha_plugin", True)):
        raise ValueError("Only proxy mode with Home Assistant discovery is available")
    if options.get("time", "server") != "server" or _boolean(options.get("sendbuf", False)):
        raise ValueError("Inverter timestamps and buffered publication are not yet supported")
    mqtt = _mqtt(
        {
            "ha_mqtt_host": options.get("mqtt_host", "core-mosquitto"),
            "ha_mqtt_port": options.get("mqtt_port", 1883),
            "ha_mqtt_user": options.get("mqtt_user", ""),
            "ha_mqtt_password": options.get("mqtt_password", ""),
            "ha_mqtt_retain": options.get("mqtt_retain", False),
            "ha_entity_profile": options.get("ha_entity_profile", "v0_1_9_standard"),
        }
    )
    return (
        RelaySettings(
            "server.growatt.com",
            listen_host="0.0.0.0",
            block_commands=_boolean(options.get("blockcmd", True)),
        ),
        mqtt,
        SelectionSettings(
            family=options.get("invtype", "default"),
            strict=_boolean(options.get("layout_strict", False)),
            automatic=_boolean(options.get("layout_auto_family", True)),
        ),
    )


def load_legacy_options(path: Path) -> tuple[RelaySettings, MqttSettings, SelectionSettings]:
    if path.suffix.lower() == ".json":
        return _addon(path)
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read_string(path.read_text(encoding="utf-8"))
    except configparser.Error:
        raise ValueError("The INI configuration could not be read") from None
    if set(config.sections()) - {"Generic", "Growatt", "MQTT", "extension", "PVOutput", "influx"}:
        raise ValueError("The INI configuration contains an unsupported section")
    generic_options = {
        "mode",
        "ip",
        "port",
        "blockcmd",
        "noipf",
        "time",
        "sendbuf",
        "compat",
        "valueoffset",
        "inverterid",
        "invtype",
        "layout_strict",
        "layout_auto_family",
        "layout_min_score",
        "diagnostic_logging",
        "verbose",
        "minrecl",
        "decrypt",
        "includeall",
        "invtypemap",
    }
    if config.has_section("Generic") and set(config.options("Generic")) - generic_options:
        raise ValueError("The INI configuration contains an unsupported Generic option")

    def get(section, key, variable, default):
        return os.environ.get(variable, config.get(section, key, fallback=default))

    _require_supported(get)
    relay = RelaySettings(
        upstream_host=get("Growatt", "ip", "ggrowattip", "server.growatt.com"),
        upstream_port=_integer(get("Growatt", "port", "ggrowattport", 5279)),
        listen_host=get("Generic", "ip", "ggrottip", "0.0.0.0"),
        listen_port=_integer(get("Generic", "port", "ggrottport", 5279)),
        block_commands=_boolean(get("Generic", "blockcmd", "gblockcmd", True)),
        allow_destination_change=_boolean(get("Generic", "noipf", "gnoipf", False)),
    )
    mqtt = _mqtt(_mapping(get("extension", "extvar", "gextvar", "{}")))
    mqtt = replace(mqtt, include_all=_boolean(get("Generic", "includeall", "gincludeall", False)))
    return relay, mqtt, _selection(get)
