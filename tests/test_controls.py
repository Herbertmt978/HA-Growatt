import asyncio
from types import SimpleNamespace

import pytest

from ha_growatt.controls import CONTROLS, controls_for, read_setting, write_setting
from ha_growatt.device_protocol import logger_prefix
from ha_growatt.protocol import Frame


class Inverter:
    def __init__(self, value=75):
        self.value = value
        self.requests = []
        self.discard = False
        self.reject = False
        self.wrong_register = False
        self.lose_readback = False

    async def command(self, identity, function, body, *, before_send=None):
        if before_send:
            before_send()
        self.requests.append((function, body))
        address = body[:2]
        if function == 5:
            if self.lose_readback and len(self.requests) > 1:
                raise TimeoutError
            reply = b"\0\x04" * 2 if self.wrong_register else address * 2
            reply += self.value.to_bytes(2, "big")
        else:
            reply = address + (b"\x01" if self.reject else b"\0")
            if not self.discard and not self.reject:
                self.value = int.from_bytes(body[2:], "big")
        return Frame(1, 6, 1, function, logger_prefix("LOGGER0001", 6) + reply)


def test_read_write_then_read_back_matches_real_register_envelopes():
    async def scenario():
        inverter = Inverter()
        control = CONTROLS[0]
        assert await read_setting(inverter, "INVERT0001", control) == 75
        assert await write_setting(inverter, "INVERT0001", control, 45) == 45
        assert inverter.requests == [(5, b"\0\3\0\3"), (6, b"\0\3\0-"), (5, b"\0\3\0\3")]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["discard", "reject", "wrong_register", "lose_readback"])
def test_unconfirmed_or_rejected_setting_is_never_success(fault):
    async def scenario():
        inverter = Inverter()
        setattr(inverter, fault, True)
        with pytest.raises((ValueError, TimeoutError)):
            await write_setting(inverter, "INVERT0001", CONTROLS[0], 45)
        assert sum(function == 6 for function, _ in inverter.requests) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [-1, 101, 255, True, 2.5, "75"])
def test_invalid_values_do_not_send_any_command(value):
    async def scenario():
        inverter = Inverter()
        with pytest.raises(ValueError):
            await write_setting(inverter, "INVERT0001", CONTROLS[0], value)
        assert not inverter.requests

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "profile", ["spf-5", "spf-6", "meter-6", "meter-log-6", "custom:T06X", "unknown"]
)
def test_unsupported_profiles_have_no_write_controls(profile):
    assert not controls_for(profile)


def test_storage_settings_are_not_exposed_on_mod_or_min():
    assert [c.key for c in controls_for("mod-6")] == ["output_limit"]
    assert [c.key for c in controls_for("min-6")] == ["output_limit"]
    assert len(controls_for("sph-6")) == 6
    assert not any(c.register == 1044 for c in CONTROLS)


def test_unrestricted_power_is_displayed_as_full_output():
    assert CONTROLS[0].display(255) == 100


def test_ha_command_readback_result_and_no_optimistic_value():
    from ha_growatt.ha_features import HomeAssistantFeatures
    from ha_growatt.telemetry import Telemetry

    class Publisher:
        generation = 0

        async def _send(self, *args):
            pass

    async def scenario():
        inverter = Inverter()
        inverter.connection = lambda _: "local"
        inverter.session_key = lambda _: 1
        inverter.stats = SimpleNamespace(fallback_connections=1)
        features = HomeAssistantFeatures(Publisher(), inverter, SimpleNamespace(failures=0))
        features.remember(Telemetry({"pvserial": "INVERT0001"}, None, "mod-6"))
        device = features.devices["INVERT0001"]
        await features.refresh(device)
        assert device.values == {"output_limit": 75}
        await features.execute("ha_growatt/INVERT0001/command/output_limit", b"45.0")
        assert device.values == {"output_limit": 45}
        assert device.command_result.endswith("applied and verified")
        count = len(inverter.requests)
        for invalid in (b"NaN", b"Infinity", b"1e99999999", b"3.5", b"-1", b"101", b"\xff"):
            await features.execute("ha_growatt/INVERT0001/command/output_limit", invalid)
        assert len(inverter.requests) == count
        assert device.values == {"output_limit": 45}
        await features.execute(
            "ha_growatt/INVERT0001/command/output_limit",
            b"40",
            received_at=asyncio.get_running_loop().time() - 16,
        )
        assert len(inverter.requests) == count
        assert "expired" in device.command_result
        inverter.discard = True
        await features.execute("ha_growatt/INVERT0001/command/output_limit", b"30")
        assert not device.values
        assert "did not apply" in device.command_result
        # An unavailable setting does not accept another write until refreshed.
        count = len(inverter.requests)
        await features.execute("ha_growatt/INVERT0001/command/output_limit", b"25")
        assert len(inverter.requests) == count

    asyncio.run(scenario())
