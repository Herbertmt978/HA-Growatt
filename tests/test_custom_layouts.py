import json

import pytest
from jinja2 import Environment

from ha_growatt.custom_layouts import CustomLayout, load_layouts
from ha_growatt.discovery import discovery_messages
from ha_growatt.protocol import Frame
from ha_growatt.selection import FamilyDecoder, SelectionSettings


def description():
    return {
        "decrypt": {"value": "True"},
        "date": {"value": 136},
        "datalogserial": {"value": 16, "length": 10, "type": "text"},
        "pvserial": {"value": 76, "length": 10, "type": "text"},
        "test_power": {"value": 156, "length": 4, "type": "numx", "divide": 10},
        "extra": {"value": 164, "length": 2, "type": "num", "incl": "no"},
    }


def synthetic():
    payload = bytearray(100)
    payload[:10] = b"LOGGER0001"
    payload[30:40] = b"INVERT0001"
    payload[60:66] = bytes([26, 9, 20, 10, 30, 0])
    payload[70:74] = (-1234).to_bytes(4, "big", signed=True)
    payload[74:76] = (456).to_bytes(2, "big")
    return Frame(1, 6, 1, 4, bytes(payload))


def test_custom_layout_file_overrides_matching_wire_layout(tmp_path):
    (tmp_path / "test-layout.json").write_text(json.dumps({"T060104": description()}))
    layouts = load_layouts(tmp_path)
    decoder = FamilyDecoder(SelectionSettings(strict=True), custom_layouts=layouts)
    telemetry = decoder.decode(synthetic())
    assert telemetry.profile == "custom:T060104"
    assert telemetry.values == {
        "datalogserial": "LOGGER0001",
        "pvserial": "INVERT0001",
        "test_power": -1234,
    }
    assert telemetry.recorded_at.isoformat() == "2026-09-20T10:30:00"
    configs = discovery_messages(
        "INVERT0001", wire_profile=telemetry.profile, sensor_metadata=telemetry.sensor_metadata
    )
    power = configs["homeassistant/sensor/grott/INVERT0001_test_power/config"]
    assert (
        Environment()
        .from_string(power["value_template"])
        .render(value_json=telemetry.values)
        .strip()
        == "-123.4"
    )
    assert layouts["T060104"].decode(synthetic(), include_all=True).values["extra"] == 456


def test_per_device_family_setting_selects_custom_family():
    layouts = {"T06NNNNMAX": CustomLayout("T06NNNNMAX", description())}
    decoder = FamilyDecoder(
        SelectionSettings(strict=True, device_families={"INVERT0001": "max"}),
        custom_layouts=layouts,
    )
    assert decoder.decode(synthetic()).profile == "custom:T06NNNNMAX"


@pytest.mark.parametrize(
    "definition",
    [{"x": {"value": -1}}, {"x": {"type": "execute"}}, {"x": {"value": "__import__('os')"}}],
)
def test_custom_definitions_are_data_only(definition):
    with pytest.raises(ValueError):
        CustomLayout("T060104", definition)
