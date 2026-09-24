"""A changed Modbus endpoint must not silently reuse an inverter history."""

import pytest

from ha_growatt.modbus_identity import modbus_connection_changed


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("host", "other-gateway.local"),
        ("port", 1502),
        ("unit", 2),
        ("transport", "udp"),
        ("udp_framing", "rtu"),
        ("baudrate", 19200),
        ("parity", "E"),
        ("stopbits", 2),
    ],
)
def test_endpoint_changes_require_a_new_entry(field, value):
    original = {"host": "gateway.local", "unit": 1}
    assert modbus_connection_changed(original, original | {field: value})


def test_polling_and_profile_tuning_preserve_the_same_connection():
    original = {"host": "gateway.local", "unit": 1, "profile": "mic-0-v314"}
    assert not modbus_connection_changed(original, original | {"interval": 45})
    assert not modbus_connection_changed(original, original | {"profile": "auto"})


def test_existing_entries_without_new_transport_fields_remain_compatible():
    original = {"host": "gateway.local", "port": 502, "unit": 1}
    assert not modbus_connection_changed(original, original | {"transport": "tcp"})
