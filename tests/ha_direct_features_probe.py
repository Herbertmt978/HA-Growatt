"""Disposable Home Assistant check for native Shine controls and diagnostics.

Run in a Home Assistant Core image with /repo mounted read-only, a writable
/config, and PYTHONPATH=/repo/src. All device identities and frames are synthetic.
"""

import asyncio
import json
import socket
from pathlib import Path
from types import MappingProxyType

from homeassistant import bootstrap, loader
from homeassistant.config_entries import SOURCE_USER, ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ha_growatt.device_protocol import logger_prefix
from ha_growatt.protocol import Frame, read_frame

IDENTITY = "INVERT0001"
LOGGER = "LOGGER0001"
CONFIG = Path("/config")


async def until(predicate, *, seconds=30):
    for _ in range(seconds * 10):
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("The disposable Home Assistant state did not appear")


def entity(hass, suffix):
    unique_id = f"ha_growatt_direct_{IDENTITY}_{suffix}"
    for item in er.async_get(hass).entities.values():
        if item.unique_id == unique_id:
            return item.entity_id
    return None


class Datalogger:
    def __init__(self, port, measurement):
        self.port = port
        self.measurement = measurement
        self.registers = {
            3: 75,
            1070: 50,
            1071: 20,
            1090: 50,
            1091: 90,
            1092: 0,
        }
        for start in (1080, 1083, 1086, 1100, 1103, 1106):
            self.registers.update({start: 23 << 8, start + 1: 5 << 8, start + 2: 0})
        self.writes = []
        self.writer = None
        self.task = None

    async def connect(self):
        for _ in range(100):
            try:
                reader, self.writer = await asyncio.open_connection("127.0.0.1", self.port)
                break
            except OSError:
                await asyncio.sleep(0.1)
        else:
            raise AssertionError("The disposable receiver did not open its port")
        self.task = asyncio.create_task(self.respond(reader))
        prefix = logger_prefix(LOGGER, 6)
        announce = Frame(
            20,
            6,
            1,
            3,
            prefix + IDENTITY.encode().ljust(30, b"\0") + bytes(100),
        )
        self.writer.write(announce.to_bytes() + self.measurement.to_bytes())
        await self.writer.drain()

    async def respond(self, reader):
        try:
            while wire := await read_frame(reader, 120):
                request = Frame.from_bytes(wire)
                if request.function not in {5, 6, 16}:
                    continue
                body = request.payload[30:]
                start = int.from_bytes(body[:2], "big")
                if request.function == 5:
                    end = int.from_bytes(body[2:4], "big")
                    reply = body[:4] + b"".join(
                        self.registers[address].to_bytes(2, "big")
                        for address in range(start, end + 1)
                    )
                elif request.function == 6:
                    self.writes.append((start, int.from_bytes(body[2:4], "big")))
                    self.registers[start] = int.from_bytes(body[2:4], "big")
                    reply = body[:4]
                else:
                    end = int.from_bytes(body[2:4], "big")
                    assert len(body) == 4 + (end - start + 1) * 2
                    self.writes.append((start, end))
                    for address in range(start, end + 1):
                        offset = 4 + (address - start) * 2
                        self.registers[address] = int.from_bytes(body[offset : offset + 2], "big")
                    reply = body[:4] + b"\0"
                response = Frame(
                    request.transaction,
                    6,
                    request.unit,
                    request.function,
                    logger_prefix(LOGGER, 6) + reply,
                )
                self.writer.write(response.to_bytes())
                await self.writer.drain()
        except (ConnectionError, OSError, TimeoutError, asyncio.IncompleteReadError):
            pass

    async def close(self):
        if self.writer is not None:
            self.writer.close()
            await self.writer.wait_closed()
        if self.task is not None:
            await asyncio.gather(self.task, return_exceptions=True)


async def main():
    from custom_components.ha_growatt.diagnostics import (
        async_get_config_entry_diagnostics,
        async_get_device_diagnostics,
    )

    fixture = json.loads(
        await asyncio.to_thread(Path("/repo/tests/fixtures/telemetry_cases.json").read_text)
    )
    measurement = Frame.from_bytes(
        bytes.fromhex(
            next(
                case["wire"]
                for case in fixture
                if case["profile"] == "sph-6" and not case["include_all"]
            )
        )
    )
    assert measurement.function == 4
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    hass = HomeAssistant(str(CONFIG))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    assert await bootstrap.async_from_config_dict({"sun": {}}, hass)
    await hass.async_start()
    entry = ConfigEntry(
        domain="ha_growatt",
        title="HA Growatt receiver",
        data={"mode": "direct", "port": port, "forward_cloud": False, "family": "sph"},
        options={"enable_controls": False, "experimental_controls": False},
        source=SOURCE_USER,
        version=1,
        minor_version=1,
        unique_id="ha_growatt_direct",
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
        pref_disable_new_entities=None,
        pref_disable_polling=None,
        disabled_by=None,
    )
    device = None
    try:
        await hass.config_entries.async_add(entry)
        await hass.async_block_till_done()
        assert entry.state.value == "loaded", entry.state
        device = Datalogger(port, measurement)
        await device.connect()
        await until(lambda: entity(hass, "pvpowerout") is not None)
        await until(lambda: hass.states.get(entity(hass, "connected")).state == "on")
        assert entity(hass, "output_limit") is None
        assert device.writes == []

        first_hub = entry.runtime_data
        hass.config_entries.async_update_entry(
            entry,
            options={
                "enable_controls": True,
                "experimental_controls": True,
                "control_models": {IDENTITY: "sph"},
                "hardware_models": {IDENTITY: "MIC 2000TL-X"},
            },
        )
        await until(lambda: entry.runtime_data is not first_hub and entry.state.value == "loaded")
        await device.close()
        device = Datalogger(port, measurement)
        await device.connect()
        await until(lambda: entity(hass, "output_limit") is not None)
        number = entity(hass, "output_limit")
        await until(lambda: hass.states.get(number).state == "75")
        assert entity(hass, "ac_charge") is None
        assert entity(hass, "charge_1_start") is None
        assert device.writes == []
        await hass.services.async_call(
            "number", "set_value", {"entity_id": number, "value": 45}, blocking=True
        )
        await until(lambda: hass.states.get(number).state == "45")
        assert device.writes == [(3, 45)]

        second_hub = entry.runtime_data
        hass.config_entries.async_update_entry(
            entry,
            options={
                "enable_controls": True,
                "experimental_controls": True,
                "control_models": {IDENTITY: "sph"},
                "hardware_models": {IDENTITY: "SPH 3000"},
            },
        )
        await until(lambda: entry.runtime_data is not second_hub and entry.state.value == "loaded")
        await device.close()
        device = Datalogger(port, measurement)
        await device.connect()
        await until(
            lambda: all(
                entity(hass, key)
                for key in ("ac_charge", "charge_1_start", "charge_1_enabled", "refresh_settings")
            )
        )
        switch = entity(hass, "ac_charge")
        text = entity(hass, "charge_1_start")
        schedule = entity(hass, "charge_1_enabled")
        button = entity(hass, "refresh_settings")
        await until(
            lambda: all(
                hass.states.get(item).state not in {"unknown", "unavailable"}
                for item in (switch, text, schedule)
            )
        )
        assert hass.states.get(switch).state == "off"
        assert hass.states.get(text).state == "23:00"
        assert hass.states.get(schedule).state == "off"
        await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
        await until(lambda: hass.states.get(switch).state == "on")
        await hass.services.async_call(
            "text", "set_value", {"entity_id": text, "value": "22:00"}, blocking=True
        )
        await until(lambda: hass.states.get(text).state == "22:00")
        await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
        assert (1092, 1) in device.writes and (1100, 1102) in device.writes

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        inverter = next(
            item
            for item in dr.async_get(hass).devices
            if ("ha_growatt", IDENTITY) in item.identifiers
        )
        device_diagnostics = await async_get_device_diagnostics(hass, entry, inverter)
        encoded = json.dumps({"entry": diagnostics, "device": device_diagnostics})
        assert IDENTITY not in encoded and LOGGER not in encoded
        assert diagnostics["controls_enabled"] is True
        assert "output_limit" in device_diagnostics["controls_available"]
        assert "charge_1" in device_diagnostics["schedules_available"]
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state.value == "not_loaded"
        print("Native Shine setup, control gates, entities, diagnostics and unload: passed")
    finally:
        if device is not None:
            await device.close()
        await hass.async_stop()


if __name__ == "__main__":
    asyncio.run(main())
