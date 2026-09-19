import json
from datetime import UTC, datetime

import pytest
from jinja2 import Environment

from ha_growatt.discovery import STANDARD_SENSORS, discovery_messages, state_message, state_topic


def test_existing_sensor_and_device_identity_survives_rebranding():
    messages = discovery_messages("INVERT0001")
    assert len(messages) == 32
    power = messages["homeassistant/sensor/grott/INVERT0001_pvpowerout/config"]
    assert power["unique_id"] == "grott_INVERT0001_pvpowerout"
    assert power["device"]["identifiers"] == ["INVERT0001"]
    assert power["origin"]["name"] == "HA Growatt"
    assert power["state_topic"] == "homeassistant/grott/INVERT0001/state"
    assert power["device_class"] == "power"
    assert power["unit_of_measurement"] == "W"
    assert power["state_class"] == "measurement"


def test_only_freshness_sensor_expires_overnight():
    messages = discovery_messages("INVERT0001")
    expiring = [value for value in messages.values() if "expire_after" in value]
    assert len(expiring) == 1
    assert expiring[0]["device_class"] == "timestamp"
    assert expiring[0]["expire_after"] == 900


@pytest.mark.parametrize("bad", ["", "bad/device", "bad+device", "bad#device", "bad\x00device"])
def test_invalid_identity_cannot_select_other_mqtt_topics(bad):
    with pytest.raises(ValueError):
        state_topic(bad)
    with pytest.raises(ValueError):
        discovery_messages(bad)


def test_energy_metadata_and_scale():
    metadata = {value.key: value for value in STANDARD_SENSORS}
    assert metadata["pvfrequentie"].divisor == 100
    assert metadata["totworktime"].divisor == 7200
    for key in ["pvenergytoday", "pvenergytotal", "epv1total", "epv2total"]:
        assert metadata[key].divisor == 10
        expected = "total_increasing" if key == "pvenergytotal" else "total"
        assert metadata[key].state_class == expected


def test_full_state_is_preserved_without_mutating_the_callers_values():
    values = {"pvserial": "INVERT0001", "pvpowerout": 12500, "extra_sensor": 17}
    encoded = state_message(values, datetime(2026, 9, 19, 12, tzinfo=UTC))
    assert json.loads(encoded) == values | {"grott_last_push": "2026-09-19T12:00:00+00:00"}
    assert "grott_last_push" not in values


def test_invalid_json_numeric_values_and_ambiguous_time_are_rejected():
    with pytest.raises(ValueError):
        state_message({"pvserial": "INVERT0001", "value": float("nan")}, datetime.now(UTC))
    with pytest.raises(ValueError):
        state_message({"pvserial": "INVERT0001"}, datetime(2026, 9, 19))
    with pytest.raises(ValueError):
        state_message({}, datetime.now(UTC))


@pytest.mark.parametrize(
    "key,values,expected",
    [
        ("pvpowerout", {"pac": 12345, "pvpowerout": 99999}, "1234.5"),
        ("pvpowerout", {"pvpowerout": 12345, "pvfrequentie": 5000}, "1234.5"),
        ("pvpowerout", {"pvpowerout": 12345}, ""),
        ("pvfrequentie", {"pvfrequency": 5001, "pvfrequentie": 10}, "50.01"),
        ("pvfrequentie", {"pvfrequentie": 5000}, "50.0"),
        ("pvipmtemperature", {"comboardtemperature": 250, "pvipmtemperature": 9999}, "25.0"),
        ("pvipmtemperature", {"pvipmtemperature": 300, "pvfrequentie": 5000}, "30.0"),
        ("pvipmtemperature", {"pvipmtemperature": 300}, ""),
    ],
)
def test_field_aliases_preserve_measurement_meaning(key, values, expected):
    config = discovery_messages("INVERT0001")[f"homeassistant/sensor/grott/INVERT0001_{key}/config"]
    assert Environment().from_string(config["value_template"]).render(value_json=values) == expected


@pytest.mark.parametrize(
    "wire_profile,profile,include_all,count",
    [
        ("mod-6", "v0_1_9_standard", False, 32),
        ("mod-6", "v0_1_9_standard", True, 32),
        ("mod-6", "all", False, 171),
        ("mod-6", "all", True, 205),
        ("extended-6", "all", False, 32),
        ("extended-6", "all", True, 36),
        ("min-6", "v0_1_9_standard", False, 152),
        ("sph-6", "v0_1_9_standard", False, 69),
    ],
)
def test_observed_discovery_counts(wire_profile, profile, include_all, count):
    messages = discovery_messages(
        "INVERT0001", wire_profile=wire_profile, profile=profile, include_all=include_all
    )
    assert len(messages) == count
    assert len({config["unique_id"] for config in messages.values()}) == count


def test_full_mod_discovery_exposes_actual_ac_power_without_changing_its_scale():
    messages = discovery_messages("INVERT0001", wire_profile="mod-6", profile="all")
    power = messages["homeassistant/sensor/grott/INVERT0001_pvpowerout/config"]
    assert power["unit_of_measurement"] == "W"
    assert (
        Environment().from_string(power["value_template"]).render(value_json={"pac": 12500})
        == "1250.0"
    )
    raw = messages["homeassistant/sensor/grott/INVERT0001_raw_pvpowerout_r3019/config"]
    assert raw["entity_category"] == "diagnostic"
    assert "unit_of_measurement" not in raw
    assert (
        Environment()
        .from_string(raw["value_template"])
        .render(value_json={"pac": 12500, "pvpowerout": 999})
        == "99.9"
    )
