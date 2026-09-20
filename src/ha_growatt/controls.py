"""Documented holding registers, scoped to observed inverter families."""

from collections.abc import Callable
from dataclasses import dataclass

from .protocol import Frame

GRID_PROFILES = frozenset(
    {
        "classic-2",
        "classic-5",
        "classic-6",
        "extended-5",
        "extended-6",
        "sph-5",
        "sph-6",
        "spa-6",
        "min-6",
        "mod-6",
        "tl3-6",
    }
)
STORAGE_PROFILES = frozenset({"sph-5", "sph-6", "spa-6"})


@dataclass(frozen=True)
class Control:
    key: str
    label: str
    register: int
    profiles: frozenset[str]
    switch: bool = False

    def validate(self, value: int) -> None:
        if type(value) is not int or not 0 <= value <= (1 if self.switch else 100):
            raise ValueError("Setting is outside its documented range")

    def display(self, value: int) -> int:
        # Growatt documents 255 as unrestricted output. The HA percentage
        # control displays full output; writing 100 still writes exactly 100.
        if self.register == 3 and value == 255:
            return 100
        self.validate(value)
        return value


# Manufacturer Modbus V1.24: holding table pages 9 and 32–33.
# Priority 1044 is read-only in that specification, so it is not offered here.
CONTROLS = (
    Control("output_limit", "Output power limit", 3, GRID_PROFILES),
    Control("discharge_rate", "Grid-first discharge power", 1070, STORAGE_PROFILES),
    Control("discharge_soc", "Grid-first minimum battery charge", 1071, STORAGE_PROFILES),
    Control("charge_rate", "Battery-first charge power", 1090, STORAGE_PROFILES),
    Control("charge_soc", "Battery-first charge limit", 1091, STORAGE_PROFILES),
    Control("ac_charge", "Battery-first AC charging", 1092, STORAGE_PROFILES, switch=True),
)


def controls_for(profile: str) -> tuple[Control, ...]:
    return tuple(control for control in CONTROLS if profile in control.profiles)


def response_body(frame: Frame) -> bytes:
    return frame.payload[30 if frame.protocol == 6 else 10 :]


async def read_setting(transport, identity: str, control: Control) -> int:
    address = control.register.to_bytes(2, "big")
    response = await transport.command(identity, 5, address * 2)
    body = response_body(response)
    if len(body) != 6 or body[:4] != address * 2:
        raise ValueError("The inverter did not return the requested register")
    value = int.from_bytes(body[4:], "big")
    control.display(value)
    return value


async def write_setting(
    transport,
    identity: str,
    control: Control,
    value: int,
    *,
    before_send: Callable[[], None] | None = None,
) -> int:
    control.validate(value)
    address = control.register.to_bytes(2, "big")
    response = await transport.command(
        identity, 6, address + value.to_bytes(2, "big"), before_send=before_send
    )
    body = response_body(response)
    # Devices reply with either an echoed word or a one-byte result.
    if body not in {address + value.to_bytes(2, "big"), address + b"\0"}:
        raise ValueError("The inverter rejected the setting")
    applied = await read_setting(transport, identity, control)
    if applied != value:
        raise ValueError("The inverter did not apply the setting")
    return applied
