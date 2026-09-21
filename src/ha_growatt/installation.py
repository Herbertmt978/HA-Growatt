"""Read-only installation checks, using the same status as the support page."""

from __future__ import annotations

import json
from importlib.resources import files


def compatibility_catalogue():
    return json.loads(files("ha_growatt").joinpath("hardware_evidence.json").read_text("utf-8"))


def installation_checks(status, *, discovery_enabled=True, features_enabled=True):
    devices = status["devices"]
    fresh = sum(d["readings"] > 0 and d["recent"] and not d["restored"] for d in devices)
    return {
        "listener": bool(status["listener"]),
        "broker": bool(status["mqtt_connected"]),
        "discovery": discovery_enabled,
        "device_status": features_enabled,
        "packets": status["transport"]["device_frames"] > 0,
        "inverters_seen": len(devices),
        "fresh_inverters": fresh,
        "all_seen_inverters_fresh": bool(devices) and fresh == len(devices),
        "recovery": bool(status["recovery_healthy"]),
    }


def mapped_port(info, internal_port):
    """Never present the container's port as the host's published port."""
    network = info.get("network")
    if not isinstance(network, dict):
        return None
    value = network.get(f"{internal_port}/tcp")
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        value = int(value)
    return value if type(value) is int and 1 <= value <= 65535 else None
