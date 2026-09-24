"""Disposable Home Assistant acceptance check for direct Modbus TCP.

Run twice with the same empty /config volume. The first run uses a local fake
gateway and creates two entries through the UI flow. Set
HA_GROWATT_MODBUS_RESTORE=1 for the second run, with no gateway, to check
overnight recovery and unload. Never point this probe at a live HA config.
"""

from __future__ import annotations

import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from homeassistant import bootstrap, loader
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from custom_components.ha_growatt.binary_sensor import NativeConnected
from custom_components.ha_growatt.diagnostics import async_get_config_entry_diagnostics

CONFIG = Path("/config")
SAVED = CONFIG / "modbus_probe_expected.json"
DEVICES = (
    ("TEST_MIN", 1, "min-3000-v124"),
    ("TEST_MIC", 2, "mic-0-v314"),
)


def put_u32(words: dict[int, int], first: int, value: int) -> None:
    words[first], words[first + 1] = value >> 16, value & 0xFFFF


def readings(unit: int) -> dict[int, int]:
    if unit in {1, 4}:
        words = {address: 0 for address in range(3000, 3079)}
        words[3000] = 1
        put_u32(words, 3001, 20450)
        words[3003], words[3004] = 3450, 59
        put_u32(words, 3005, 20355)
        put_u32(words, 3023, 19990)
        words[3025], words[3026], words[3027] = 5002, 2310, 87
        put_u32(words, 3049, 74)
        put_u32(words, 3051, 12345)
        put_u32(words, 3053, 12365)
        put_u32(words, 3055, 72)
        put_u32(words, 3057, 12355)
        if unit == 4:
            words.update({address: 0 for address in range(3086, 3109)})
            words[3011], words[3012] = 3600, 41
            put_u32(words, 3013, 14760)
            put_u32(words, 3063, 31)
            put_u32(words, 3065, 12350)
            words[3093], words[3094], words[3095] = 256, 267, 274
            words[3105], words[3106] = 9, 3
        return words
    words = {address: 0 for address in range(58)}
    words[0] = 1
    put_u32(words, 1, 20450)
    words[3], words[4] = 3450, 59
    put_u32(words, 5, 20355)
    put_u32(words, 11, 19990)
    words[13], words[14], words[15] = 5002, 2310, 87
    put_u32(words, 26, 74)
    put_u32(words, 28, 12345)
    words[32], words[41] = 254, 271
    put_u32(words, 48, 72)
    put_u32(words, 50, 12355)
    put_u32(words, 56, 12365)
    return words


async def gateway_reply(reader, writer):
    try:
        request = await reader.readexactly(12)
        assert request[7] in {3, 4}, "The receiver sent a Modbus write"
        unit = request[6]
        assert unit in {1, 2, 3, 4}
        first = int.from_bytes(request[8:10], "big")
        count = int.from_bytes(request[10:12], "big")
        assert 1 <= count <= 32
        if unit == 3:
            assert request[7] == 3 and first == 400 and count == 3
            words = {400: 7, 401: 0, 402: 65534}
        else:
            assert request[7] == 4
            words = readings(unit)
        payload = bytes((request[7], count * 2)) + b"".join(
            words[first + offset].to_bytes(2, "big") for offset in range(count)
        )
        reply = request[:4] + (len(payload) + 1).to_bytes(2, "big") + request[6:7] + payload
        writer.write(reply)
        await writer.drain()
    finally:
        writer.close()


async def until(predicate, *, seconds=20):
    for _ in range(seconds * 10):
        if predicate():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("Home Assistant did not reach the expected Modbus state")


async def boot():
    hass = HomeAssistant(str(CONFIG))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    assert await bootstrap.async_from_config_dict({"sun": {}}, hass)
    await hass.async_start()
    return hass


async def create_entry(hass, identity, unit, profile, port, **extra):
    flow = await hass.config_entries.flow.async_init("ha_growatt", context={"source": SOURCE_USER})
    assert "modbus" in flow["menu_options"]
    form = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "modbus"}
    )
    assert form["step_id"] == "modbus", form
    result = await hass.config_entries.flow.async_configure(
        form["flow_id"],
        {
            "host": "127.0.0.1",
            "port": port,
            "unit": unit,
            "identity": identity,
            "profile": profile,
            "interval": 30,
            **extra,
        },
    )
    assert result["type"] == "create_entry", result
    await until(
        lambda: any(
            entry.data.get("identity") == identity
            and getattr(entry.state, "value", entry.state) == "loaded"
            for entry in hass.config_entries.async_entries("ha_growatt")
        )
    )
    return next(
        entry
        for entry in hass.config_entries.async_entries("ha_growatt")
        if entry.data.get("identity") == identity
    )


def entry_entities(hass, entry):
    return {
        entity.unique_id: entity.entity_id
        for entity in er.async_get(hass).entities.values()
        if entity.config_entry_id == entry.entry_id
    }


async def fresh_run():
    server = await asyncio.start_server(gateway_reply, "127.0.0.1", 0)
    async with server:
        port = server.sockets[0].getsockname()[1]
        hass = await boot()
        try:
            entries = [
                await create_entry(hass, identity, unit, profile, port)
                for identity, unit, profile in DEVICES
            ]
            await until(lambda: all(entry.runtime_data.receiver.connected for entry in entries))
            await until(lambda: all(len(entry_entities(hass, entry)) >= 15 for entry in entries))
            await hass.async_block_till_done()
            expected = {}
            for entry in entries:
                identity = entry.data["identity"]
                entities = entry_entities(hass, entry)
                assert len(entities) >= 15, (identity, entities)
                assert all(
                    unique.startswith(f"ha_growatt_modbus_{identity}_") for unique in entities
                )
                output_id = entities[f"ha_growatt_modbus_{identity}_pvpowerout"]
                connected_id = entities[f"ha_growatt_modbus_{identity}_connected"]
                assert float(hass.states.get(output_id).state) == 1999.0
                assert hass.states.get(connected_id).state == "on"
                devices = [
                    device
                    for device in dr.async_get(hass).devices
                    if ("ha_growatt", f"ha_growatt_modbus_{identity}") in device.identifiers
                ]
                assert len(devices) == 1
                report = await async_get_config_entry_diagnostics(hass, entry)
                assert report["gateway_connected"] is True
                assert report["observations"]["measurements"] >= 1
                encoded = json.dumps(report)
                assert identity not in encoded and "127.0.0.1" not in encoded
                hub = entry.runtime_data
                hub.receiver.interval = 3600
                connected = NativeConnected(identity, "modbus", hub)
                connected.last_live = hub.receiver.snapshots[identity].received_at
                connected.refresh(connected.last_live + timedelta(minutes=30))
                assert connected.is_on and hub.stale_minutes >= 61
                hub.receiver.connected = False
                connected.refresh(connected.last_live + timedelta(minutes=30))
                assert not connected.is_on
                hub.receiver.connected = True
                hub.receiver.interval = 30
                expected[identity] = entities
            assert set(expected[DEVICES[0][0]]).isdisjoint(expected[DEVICES[1][0]])
            raw_entry = await create_entry(
                hass,
                "TEST_RAW",
                3,
                "investigate-raw",
                port,
                investigation_kind="holding",
                investigation_start=400,
                investigation_count=3,
            )
            await until(lambda: raw_entry.runtime_data.receiver.connected)
            await until(lambda: len(entry_entities(hass, raw_entry)) >= 4)
            raw_entities = [
                entity
                for entity in er.async_get(hass).entities.values()
                if entity.config_entry_id == raw_entry.entry_id
                and "_raw_holding_" in entity.unique_id
            ]
            assert len(raw_entities) == 3
            assert all(entity.disabled_by is not None for entity in raw_entities)
            assert all(hass.states.get(entity.entity_id) is None for entity in raw_entities)
            assert not (CONFIG / ".storage" / "ha_growatt_modbus_TEST_RAW.json").exists()
            report = await async_get_config_entry_diagnostics(hass, raw_entry)
            assert report["restart_recovery_available"] is False
            assert "65534" not in json.dumps(report)
            assert await hass.config_entries.async_remove(raw_entry.entry_id)
            three_string_entry = await create_entry(
                hass, "TEST_MIN3", 4, "min-three-string-v124", port
            )
            await until(lambda: three_string_entry.runtime_data.receiver.connected)
            await until(
                lambda: (
                    "ha_growatt_modbus_TEST_MIN3_pv3watt"
                    in entry_entities(hass, three_string_entry)
                )
            )
            three_string_entities = entry_entities(hass, three_string_entry)
            assert (
                float(
                    hass.states.get(
                        three_string_entities["ha_growatt_modbus_TEST_MIN3_pv3watt"]
                    ).state
                )
                == 1476
            )
            assert (
                float(
                    hass.states.get(
                        three_string_entities["ha_growatt_modbus_TEST_MIN3_epv3total"]
                    ).state
                )
                == 1235
            )
            assert "ha_growatt_modbus_TEST_MIN3_pvfaultcode" in three_string_entities
            assert await hass.config_entries.async_remove(three_string_entry.entry_id)
            offline = await asyncio.start_server(
                lambda reader, writer: writer.close(), "127.0.0.1", 0
            )
            async with offline:
                offline_entry = await create_entry(
                    hass,
                    "TEST_OFFLINE",
                    3,
                    "mic-0-v314",
                    offline.sockets[0].getsockname()[1],
                )
                await until(
                    lambda: (
                        "ha_growatt_modbus_TEST_OFFLINE_connected"
                        in entry_entities(hass, offline_entry)
                    )
                )
                offline_entities = entry_entities(hass, offline_entry)
                assert len(offline_entities) == 1
                assert (
                    hass.states.get(
                        offline_entities["ha_growatt_modbus_TEST_OFFLINE_connected"]
                    ).state
                    == "off"
                )
                assert await hass.config_entries.async_remove(offline_entry.entry_id)
            hub = entries[0].runtime_data
            now = dt_util.utcnow()
            hass.states.async_set("sun.sun", "above_horizon")
            hub.receiver.connected = False
            hub.receiver.failed_measurements = 1
            hub.started = now - timedelta(minutes=3)
            with patch(
                "custom_components.ha_growatt.modbus.get_astral_event_date", return_value=None
            ):
                hub.options["daylight_alerts"] = False
                hub.evaluate(now)
                assert not any(key.endswith("_unavailable") for key in hub.current_issues)
                hub.options["daylight_alerts"] = True
                hub.started = now
                hub.evaluate(now)
                assert not any(key.endswith("_unavailable") for key in hub.current_issues)
                hub.started = now - timedelta(minutes=3)
                hub.evaluate(now)
                assert any(key.endswith("_unavailable") for key in hub.current_issues)
                hub.receiver.connected = True
                hub.receiver.failed_measurements = 0
                hub.evaluate(now)
                assert not any(key.endswith("_unavailable") for key in hub.current_issues)
            SAVED.write_text(json.dumps(expected, sort_keys=True), encoding="utf-8")
            print("Modbus UI flow, MIN/MIC, three-string and raw diagnostics passed")
        finally:
            await hass.async_stop()


async def restore_run():
    expected = json.loads(SAVED.read_text(encoding="utf-8"))
    hass = await boot()
    try:
        entries = hass.config_entries.async_entries("ha_growatt")
        assert len(entries) == 2
        await until(
            lambda: all(getattr(entry.state, "value", entry.state) == "loaded" for entry in entries)
        )
        await until(lambda: all(entry_entities(hass, entry) for entry in entries))
        for entry in entries:
            identity = entry.data["identity"]
            entities = entry_entities(hass, entry)
            assert entities == expected[identity], (identity, entities)
            output_id = entities[f"ha_growatt_modbus_{identity}_pvpowerout"]
            connected_id = entities[f"ha_growatt_modbus_{identity}_connected"]
            assert float(hass.states.get(output_id).state) == 1999.0
            assert hass.states.get(connected_id).state == "off"
            assert not entry.runtime_data.receiver.connected
            report = await async_get_config_entry_diagnostics(hass, entry)
            assert not report["gateway_connected"]
            assert identity not in json.dumps(report)
            receiver = entry.runtime_data.receiver
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert not receiver.running
            await hass.async_block_till_done()
            remaining = hass.states.get(output_id)
            assert remaining is None or remaining.state == "unavailable", remaining
        print(
            "Quiet restart preserved readings and entity IDs; Connected stayed off; unload passed"
        )
    finally:
        await hass.async_stop()


if __name__ == "__main__":
    asyncio.run(
        restore_run() if os.environ.get("HA_GROWATT_MODBUS_RESTORE") == "1" else fresh_run()
    )
