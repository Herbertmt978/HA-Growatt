"""Native settings remain unavailable until proved by a live readback."""

import asyncio
import runpy
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ha_growatt.device_protocol import logger_prefix
from ha_growatt.protocol import Frame
from ha_growatt.schedules import Period


class HomeAssistantError(Exception):
    pass


homeassistant = SimpleNamespace(__path__=[])
exceptions = SimpleNamespace(HomeAssistantError=HomeAssistantError)
with patch.dict(
    sys.modules,
    {"homeassistant": homeassistant, "homeassistant.exceptions": exceptions},
):
    DirectControls = runpy.run_path(
        str(Path(__file__).parents[1] / "custom_components/ha_growatt/direct_controls.py")
    )["DirectControls"]


class Transport:
    def __init__(self):
        self.session = 1
        self.registers = {
            3: 75,
            1070: 50,
            1071: 20,
            1090: 50,
            1091: 90,
            1092: 0,
            3047: 50,
            3048: 90,
            3049: 0,
        }
        self.period = Period("23:30", "05:30", True)
        self.commands = []

    def session_key(self, identity):
        return self.session

    async def command(self, identity, function, body, *, before_send=None):
        if before_send:
            before_send()
        self.commands.append((function, body))
        address = body[:4]
        if function == 5 and address[:2] == address[2:]:
            register = int.from_bytes(address[:2], "big")
            response = address + self.registers[register].to_bytes(2, "big")
        elif function == 5:
            response = address + b"".join(word.to_bytes(2, "big") for word in self.period.words())
        elif function == 6:
            register = int.from_bytes(body[:2], "big")
            self.registers[register] = int.from_bytes(body[2:], "big")
            response = body
        else:
            assert function == 16
            self.period = Period(
                f"{body[4]:02d}:{body[5]:02d}",
                f"{body[6]:02d}:{body[7]:02d}",
                bool(body[9]),
            )
            response = address + b"\0"
        return Frame(1, 6, 1, function, logger_prefix("LOGGER0001", 6) + response)


def controls(options):
    transport = Transport()
    receiver = SimpleNamespace(control_transport=transport)
    hub = SimpleNamespace(options=options, receiver=receiver)
    return DirectControls(hub), transport


def reading(profile="classic-6", restored=False):
    return SimpleNamespace(
        identity="INVERT0001",
        restored=restored,
        snapshot=SimpleNamespace(telemetry=SimpleNamespace(profile=profile)),
    )


def test_no_control_without_explicit_enable_and_fresh_readback():
    async def scenario():
        settings, transport = controls({})
        settings.observe(reading())
        assert not settings.states
        assert not transport.commands
        await settings.close()

        settings, transport = controls({"enable_controls": True})
        settings.observe(reading(restored=True))
        assert not settings.states
        settings.observe(reading())
        assert settings.controls("INVERT0001") == ()
        await asyncio.gather(*settings.tasks)
        assert [control.key for control in settings.controls("INVERT0001")] == ["output_limit"]
        await settings.set_control("INVERT0001", "output_limit", 45)
        assert settings.value("INVERT0001", "output_limit") == 45
        assert sum(function == 6 for function, _ in transport.commands) == 1
        transport.session = 2
        settings.check_sessions()
        assert not settings.controls("INVERT0001")
        with pytest.raises(HomeAssistantError):
            await settings.set_control("INVERT0001", "output_limit", 60)
        assert sum(function == 6 for function, _ in transport.commands) == 1
        await settings.close()

    asyncio.run(scenario())


def test_experimental_battery_requires_exact_matching_model_and_profile():
    async def scenario():
        base = {
            "enable_controls": True,
            "experimental_controls": True,
            "control_models": {"INVERT0001": "min_tl_xh"},
        }
        settings, transport = controls(base)
        settings.observe(reading("min-6"))
        await asyncio.gather(*settings.tasks)
        assert [control.key for control in settings.controls("INVERT0001")] == ["output_limit"]
        assert all(int.from_bytes(body[:2], "big") < 3000 for _, body in transport.commands)
        await settings.close()

        settings, transport = controls(base | {"hardware_models": {"INVERT0001": "MIN 2500TL-XH"}})
        settings.observe(reading("min-6"))
        await asyncio.gather(*settings.tasks)
        assert {control.key for control in settings.controls("INVERT0001")} >= {
            "xh_charge_rate",
            "xh_charge_soc",
            "xh_ac_charge",
        }
        await settings.close()

    asyncio.run(scenario())


def test_queued_native_write_is_dropped_after_session_replacement():
    async def scenario():
        settings, transport = controls({"enable_controls": True})
        settings.observe(reading())
        await asyncio.gather(*settings.tasks)
        state = settings.states["INVERT0001"]
        async with state.lock:
            queued = asyncio.create_task(settings.set_control("INVERT0001", "output_limit", 40))
            await asyncio.sleep(0)
            transport.session = 2
        with pytest.raises(HomeAssistantError):
            await queued
        assert all(function != 6 for function, _ in transport.commands)
        await settings.close()

    asyncio.run(scenario())


def test_experimental_schedule_is_one_write_with_verified_readback():
    async def scenario():
        settings, transport = controls(
            {
                "enable_controls": True,
                "experimental_controls": True,
                "control_models": {"INVERT0001": "sph"},
                "hardware_models": {"INVERT0001": "SPH 3000"},
            }
        )
        settings.observe(reading("sph-6"))
        await asyncio.gather(*settings.tasks)
        assert "charge_1" in settings.periods("INVERT0001")
        await settings.set_period("INVERT0001", "charge_1", "start", "22:00")
        assert settings.period("INVERT0001", "charge_1").start == "22:00"
        assert sum(function == 16 for function, _ in transport.commands) == 1
        await settings.close()

    asyncio.run(scenario())
