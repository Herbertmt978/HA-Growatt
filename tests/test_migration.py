from datetime import UTC, datetime

from test_publisher import packet

from ha_growatt.discovery import discovery_messages
from ha_growatt.migration import migration_preview
from ha_growatt.publisher import MqttSettings
from ha_growatt.recovery import Snapshot


def inputs():
    snapshots = {"INVERT0001": Snapshot(packet(), datetime.now(UTC))}
    configs = discovery_messages("INVERT0001", wire_profile="classic-6")
    config = configs["homeassistant/sensor/grott/INVERT0001_pvenergytotal/config"]
    entity = "sensor.existing_solar_total"
    inventory = {
        "entities": [{"entity_id": entity, "unique_id": config["unique_id"], "platform": "mqtt"}],
        "states": [
            {
                "entity_id": entity,
                "attributes": {
                    "unit_of_measurement": "kWh",
                    "device_class": "energy",
                    "state_class": "total_increasing",
                },
            }
        ],
        "statistics": [{"statistic_id": entity, "unit_of_measurement": "kWh"}],
        "energy": {"energy_sources": [{"type": "solar", "stat_energy_from": entity}]},
    }
    return snapshots, inventory


def total(rows):
    return next(row for row in rows if row["unique_id"].endswith("_pvenergytotal"))


def test_exact_identity_keeps_entity_and_statistics_with_energy_selected():
    snapshots, inventory = inputs()
    result = migration_preview(snapshots, MqttSettings("broker"), inventory)
    row = total(result["entities"])
    assert row["status"] == "preserved"
    assert row["has_statistics"] and row["used_in_energy"] and row["energy_eligible"]
    assert result["read_only"]


def test_unit_conflict_prevents_energy_recommendation():
    snapshots, inventory = inputs()
    inventory["statistics"][0]["unit_of_measurement"] = "Wh"
    row = total(migration_preview(snapshots, MqttSettings("broker"), inventory)["entities"])
    assert row["status"] == "review" and not row["energy_eligible"]
    assert "Stored statistics use a different unit" in row["issues"]


def test_missing_live_metadata_requires_review():
    snapshots, inventory = inputs()
    inventory["states"] = []
    row = total(migration_preview(snapshots, MqttSettings("broker"), inventory)["entities"])
    assert row["status"] == "review" and not row["energy_eligible"]


def test_other_integration_id_is_only_a_candidate_and_other_inverter_is_excluded():
    snapshots, inventory = inputs()
    entry = inventory["entities"][0]
    entry.update(platform="grott", unique_id="grott_INVERT0001_pvenergytotal")
    row = total(migration_preview(snapshots, MqttSettings("broker"), inventory)["entities"])
    assert row["status"] == "review" and row["candidates"] == [entry["entity_id"]]
    entry["unique_id"] = "grott_OTHER00001_pvenergytotal"
    row = total(migration_preview(snapshots, MqttSettings("broker"), inventory)["entities"])
    assert row["status"] == "new" and not row["candidates"]
