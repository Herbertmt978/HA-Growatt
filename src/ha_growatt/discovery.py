"""Build MQTT discovery while preserving existing Home Assistant identity."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from . import __version__
from .profiles import output_fields, wire_profiles

_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


@dataclass(frozen=True, slots=True)
class Sensor:
    key: str
    label: str
    divisor: int = 1
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = None
    source: str | None = None
    entity_category: str | None = None
    icon: str | None = None


def _standard_sensors() -> tuple[Sensor, ...]:
    result = [
        Sensor("datalogserial", "Datalogger serial"),
        Sensor("pvserial", "Serial"),
        Sensor("pvstatus", "State"),
        Sensor("pvpowerin", "PV Input (Actual)", 10, "W", "power", "measurement"),
    ]
    for index in (1, 2):
        for suffix, label, unit, category in (
            ("voltage", "Voltage", "V", "voltage"),
            ("current", "Current", "A", "current"),
            ("watt", "Watt", "W", "power"),
        ):
            result.append(
                Sensor(
                    f"pv{index}{suffix}", f"PV{index} {label}", 10, unit, category, "measurement"
                )
            )
    result.extend(
        [
            Sensor("pvpowerout", "PV Output (Actual)", 10, "W", "power", "measurement"),
            Sensor("pvfrequentie", "Grid frequency", 100, "Hz", "frequency", "measurement"),
        ]
    )
    for index in (1, 2, 3):
        suffix = str(index) if index > 1 else ""
        for quantity, unit in (("voltage", "V"), ("current", "A"), ("power", "W")):
            result.append(
                Sensor(
                    f"pvgrid{quantity}{suffix}",
                    f"Phase {index} {quantity}",
                    10,
                    unit,
                    quantity,
                    "measurement",
                )
            )
    result.append(Sensor("totworktime", "Working time", 7200, "h", "duration"))
    for key, label in (
        ("pvenergytoday", "Generated energy (Today)"),
        ("pvenergytotal", "Generated energy (Total)"),
        ("epvtotal", "Generated PV energy (Total)"),
        ("epv1today", "Solar PV1 production"),
        ("epv1total", "Solar PV1 production (Total)"),
        ("epv2today", "Solar PV2 production"),
        ("epv2total", "Solar PV2 production (Total)"),
    ):
        state_class = "total_increasing" if key == "pvenergytotal" else "total"
        result.append(Sensor(key, label, 10, "kWh", "energy", state_class))
    for key, label in (
        ("pvtemperature", "Inverter temperature"),
        ("pvipmtemperature", "IPM temperature"),
    ):
        result.append(Sensor(key, label, 10, "°C", "temperature", "measurement"))
    result.append(Sensor("grott_last_push", "Last data push", device_class="timestamp"))
    return tuple(result)


STANDARD_SENSORS = _standard_sensors()


def validate_identity(identity: str) -> None:
    if not _SAFE_ID.fullmatch(identity):
        raise ValueError("Device identity is not safe for an MQTT topic")


def state_topic(identity: str) -> str:
    validate_identity(identity)
    return f"homeassistant/grott/{identity}/state"


def discovery_messages(
    identity: str,
    *,
    profile: str = "v0_1_9_standard",
    wire_profile: str | None = None,
    include_all: bool = False,
    sensor_metadata: dict | None = None,
) -> dict[str, dict]:
    """Return retained config messages; no network operation takes place."""
    validate_identity(identity)
    if profile not in {"v0_1_9_standard", "all"}:
        raise ValueError("Unknown Home Assistant entity profile")
    sensors = STANDARD_SENSORS
    standard = wire_profile is None or (
        profile == "v0_1_9_standard"
        and wire_profile in {"mod-6", "extended-6", "custom:T06NNNNXMOD", "custom:T06NNNNX"}
    )
    if not standard:
        schema = (
            {"sensors": sensor_metadata}
            if sensor_metadata is not None
            else wire_profiles()[wire_profile]
        )
        available = (
            {v["source"] for v in sensor_metadata.values()}
            if sensor_metadata is not None
            else output_fields(wire_profile, include_all)
        )
        sensors = tuple(
            Sensor(key, **({"label": key} | metadata))
            for key, metadata in sorted(schema["sensors"].items())
            if metadata["source"] in available
        ) + (STANDARD_SENSORS[-1],)
    result = {}
    for sensor in sensors:
        template = "{{ value_json[" + json.dumps(sensor.source or sensor.key) + "]"
        if sensor.divisor != 1:
            template += f" | float / {sensor.divisor}"
        template += " }}"
        aliases = {
            "pvpowerout": 'value_json.get("pac", value_json.get("pvpowerout") '
            'if "pvfrequentie" in value_json else none)',
            "pvfrequentie": 'value_json.get("pvfrequency", value_json.get("pvfrequentie"))',
            "pvipmtemperature": 'value_json.get("comboardtemperature", '
            'value_json.get("pvipmtemperature") if "pvfrequentie" in value_json else none)',
        }
        if (standard or wire_profile in {"mod-6", "custom:T06NNNNXMOD"}) and sensor.key in aliases:
            template = (
                "{% set reading = " + aliases[sensor.key] + " %}"
                "{% if reading is not none %}{{ reading | float / "
                + str(sensor.divisor)
                + " }}{% endif %}"
            )
        config = {
            "name": f"{identity} {sensor.label}",
            "unique_id": f"grott_{identity}_{sensor.key}",
            "state_topic": state_topic(identity),
            "value_template": template,
            "origin": {
                "name": "HA Growatt",
                "sw_version": __version__,
                "support_url": "https://github.com/Herbertmt978/HA-Growatt/issues",
            },
            "device": {"identifiers": [identity], "name": identity, "manufacturer": "Growatt"},
        }
        if sensor.unit:
            config["unit_of_measurement"] = sensor.unit
        if sensor.device_class:
            config["device_class"] = sensor.device_class
        if sensor.state_class:
            config["state_class"] = sensor.state_class
        if sensor.entity_category:
            config["entity_category"] = sensor.entity_category
        icon = sensor.icon
        if standard:
            icon = wire_profiles()["extended-6"]["sensors"].get(sensor.key, {}).get("icon")
        if icon:
            config["icon"] = icon
        if sensor.key == "grott_last_push":
            config["expire_after"] = 900
        result[f"homeassistant/sensor/grott/{identity}_{sensor.key}/config"] = config
    return result


def lineage_topics(identity: str) -> set[str]:
    """Known generic/MOD topics only; never enumerate unrelated broker data."""
    validate_identity(identity)
    keys = set(wire_profiles()["mod-6"]["sensors"]) | set(wire_profiles()["extended-6"]["sensors"])
    return {f"homeassistant/sensor/grott/{identity}_{key}/config" for key in keys}


def state_message(values: dict, received_at: datetime, identity: str | None = None) -> str:
    """Serialise raw telemetry using the established scalar field names."""
    if received_at.tzinfo is None or received_at.utcoffset() is None:
        raise ValueError("Receipt time must include a timezone")
    identity = identity or values.get("pvserial")
    if not isinstance(identity, str):
        raise ValueError("Telemetry has no inverter identity")
    validate_identity(identity)
    data = dict(values)
    data["grott_last_push"] = received_at.isoformat(timespec="seconds")
    return json.dumps(data, separators=(",", ":"), allow_nan=False)
