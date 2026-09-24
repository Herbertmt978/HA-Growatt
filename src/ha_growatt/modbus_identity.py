"""Keep a Modbus entry attached to the connection it was created for."""

from collections.abc import Mapping
from typing import Any

# A user label is not proof of inverter identity. None of the currently
# supported register profiles supplies a verified, common serial read, so a
# changed endpoint must use a separate Home Assistant entry and identity.
_CONNECTION_DEFAULTS = {
    "transport": "tcp",
    "udp_framing": "socket",
    "baudrate": 9600,
    "parity": "N",
    "stopbits": 1,
    "port": 502,
    "unit": 1,
}
_CONNECTION_FIELDS = ("host", *_CONNECTION_DEFAULTS)


def modbus_connection_changed(current: Mapping[str, Any], proposed: Mapping[str, Any]) -> bool:
    """Return whether a proposed edit could select a different inverter."""
    return any(
        current.get(field, _CONNECTION_DEFAULTS.get(field))
        != proposed.get(field, _CONNECTION_DEFAULTS.get(field))
        for field in _CONNECTION_FIELDS
    )
