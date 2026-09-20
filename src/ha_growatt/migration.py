"""Read-only identity and Energy checks; never rename entities or statistics."""

from __future__ import annotations

import re

from .discovery import discovery_messages

# Candidate names are hints only. Identity, metadata and history ownership
# still need checking; a similar label never permits an automatic migration.
_ALIASES = {
    "pvenergytotal": ("output_energy_total", "total_energy", "lifetime_energy_output"),
    "pvenergytoday": ("output_energy_today", "energy_today", "energy_today_output"),
    "pvpowerout": ("output_power", "ac_power", "power_output"),
    "pvpowerin": ("input_power", "pv_power"),
    "etouser_tot": ("energy_to_user_total", "grid_import_energy_total"),
    "etogrid_tot": ("energy_to_grid_total", "grid_export_energy_total"),
}


def migration_preview(snapshots: dict, mqtt, inventory: dict) -> dict:
    entities = inventory.get("entities", [])
    states = {item["entity_id"]: item for item in inventory.get("states", [])}
    statistics = {item["statistic_id"]: item for item in inventory.get("statistics", [])}
    devices = {item["id"]: item for item in inventory.get("devices", [])}
    energy = inventory.get("energy", {})
    energy_ids = set()

    def collect(value):
        if isinstance(value, dict):
            for child in value.values():
                collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)
        elif isinstance(value, str) and value.startswith("sensor."):
            energy_ids.add(value)

    collect(energy)
    rows = []
    for identity, snapshot in snapshots.items():
        telemetry = snapshot.telemetry
        configs = discovery_messages(
            identity,
            profile=mqtt.entity_profile,
            wire_profile=telemetry.profile,
            include_all=mqtt.include_all,
            sensor_metadata=telemetry.sensor_metadata,
        )
        for topic, config in configs.items():
            key = topic.rsplit("/", 2)[1].removeprefix(identity + "_")
            exact = [
                entry
                for entry in entities
                if entry.get("platform") == "mqtt" and entry.get("unique_id") == config["unique_id"]
            ]
            candidates = []
            if not exact:
                for entry in entities:
                    device = devices.get(entry.get("device_id"), {})
                    ids = [str(entry.get("unique_id", ""))] + [
                        str(part) for pair in device.get("identifiers", []) for part in pair
                    ]
                    same_device = any(
                        re.search(
                            r"(?:^|[^A-Za-z0-9])" + re.escape(identity) + r"(?:$|[^A-Za-z0-9])",
                            value,
                        )
                        for value in ids
                    )
                    names = (key,) + _ALIASES.get(key, ())
                    if same_device and any(
                        str(entry.get("unique_id", "")).endswith("_" + name)
                        or entry.get("entity_id", "").endswith("_" + name)
                        for name in names
                    ):
                        candidates.append(entry)
            source = (
                exact[0] if len(exact) == 1 else candidates[0] if len(candidates) == 1 else None
            )
            entity_id = source["entity_id"] if source else None
            attributes = states.get(entity_id, {}).get("attributes", {})
            statistic = statistics.get(entity_id, {})
            issues = []
            if source and entity_id not in states:
                issues.append("Current entity metadata is unavailable; check after it loads")
            for attribute in ("unit_of_measurement", "device_class", "state_class"):
                if attribute in attributes and attributes[attribute] != config.get(attribute):
                    issues.append(attribute + " differs")
            stat_unit = statistic.get("unit_of_measurement")
            if stat_unit and stat_unit != config.get("unit_of_measurement"):
                issues.append("Stored statistics use a different unit")
            if source and source.get("disabled_by"):
                issues.append("Entity is disabled")
            status = "preserved" if len(exact) == 1 and not issues else "review"
            if not exact and not candidates:
                status = "new"
            if len(exact) > 1 or len(candidates) > 1:
                issues.append("More than one identity matches; choose the source manually")
            if candidates:
                issues.append(
                    "Different integration identity; history is not transferred automatically"
                )
            energy_ready = (
                config.get("device_class") == "energy"
                and config.get("state_class") in {"total", "total_increasing"}
                and config.get("unit_of_measurement") in {"Wh", "kWh", "MWh"}
                and not issues
            )
            rows.append(
                {
                    "inverter": identity,
                    "measurement": config["name"],
                    "unique_id": config["unique_id"],
                    "existing_entity": entity_id,
                    "status": status,
                    "issues": issues,
                    "has_statistics": entity_id in statistics,
                    "energy_eligible": energy_ready,
                    "used_in_energy": entity_id in energy_ids,
                    "recommended_solar_total": energy_ready and key == "pvenergytotal",
                    "candidates": [item["entity_id"] for item in candidates],
                }
            )
    return {
        "read_only": True,
        "entities": rows,
        "summary": {
            key: sum(row["status"] == key for row in rows) for key in ("preserved", "review", "new")
        },
        "guidance": "Preserved identities keep their existing history. "
        "Review other mappings before "
        "removing an old integration. Choose the inverter total energy once per inverter; "
        "do not also count its individual PV totals. "
        "This preview does not change history or Energy settings.",
    }
