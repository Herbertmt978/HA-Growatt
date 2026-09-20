"""Isolated HA/MQTT acceptance probe; run only with synthetic configuration.

Requires a Home Assistant image, a broker named growatt-features-mqtt,
PYTHONPATH pointing at src, and writable /config. Never use a live HA config.
"""

import asyncio
import json
import logging
import sqlite3
from pathlib import Path

import aiohttp
from homeassistant import bootstrap, loader
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from ha_growatt.controls import response_body
from ha_growatt.device_protocol import acknowledgement, logger_prefix
from ha_growatt.pipeline import Pipeline
from ha_growatt.protocol import Frame, read_frame
from ha_growatt.publisher import MqttSettings, Publisher
from ha_growatt.relay import Relay, RelaySettings
from ha_growatt.selection import SelectionSettings
from ha_growatt.settings import Settings
from ha_growatt.telemetry import Telemetry

logging.basicConfig(level=logging.WARNING)
IDENTITY = "QUALIFY001"
CONFIG = Path("/config")
BASE = Path(__file__).parent
CONF = {
    "homeassistant": {
        "name": "Growatt feature qualification",
        "latitude": 0,
        "longitude": 0,
        "elevation": 0,
        "unit_system": "metric",
        "time_zone": "UTC",
        "country": "GB",
        "currency": "GBP",
    },
    "recorder": {"commit_interval": 1},
    "http": {"server_host": "0.0.0.0", "server_port": 18124},
    "api": {},
    "mqtt": {},
}


async def until(test):
    for _ in range(450):
        if test():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("Home Assistant did not reach the expected state")


def entities(hass):
    return {
        entry.unique_id: entry.entity_id
        for entry in er.async_get(hass).entities.values()
        if entry.platform == "mqtt"
    }


async def boot():
    await asyncio.to_thread(CONFIG.mkdir, exist_ok=True)
    (CONFIG / "configuration.yaml").write_text(json.dumps(CONF))
    hass = HomeAssistant(str(CONFIG))
    loader.async_setup(hass)
    hass.config.skip_pip = True
    assert await bootstrap.async_from_config_dict(CONF, hass)
    if not hass.config_entries.async_entries("mqtt"):
        result = await hass.config_entries.flow.async_init("mqtt", context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                "broker": "growatt-features-mqtt",
                "port": 1883,
                "protocol": "5",
                "username": "",
                "password": "",
                "other_settings": {"set_client_cert": False, "set_ca_cert": "off"},
            },
        )
        assert result["type"] == "create_entry"
    await hass.async_start()
    await until(
        lambda: all(
            entry.state.value == "loaded" for entry in hass.config_entries.async_entries("mqtt")
        )
    )
    async with aiohttp.ClientSession() as client:
        async with client.get("http://127.0.0.1:18124/api/") as response:
            assert response.status == 401  # The API is serving and requires authentication.
    return hass


class SyntheticInverter:
    value = 75
    discard = False

    def __init__(self, logger="TESTLOG001"):
        self.logger = logger
        self.registers = {1070: 50, 1071: 20, 1090: 50, 1091: 90, 1092: 0}
        self.tasks = set()
        self.writer = None
        self.writes = []
        self.acks = 0
        self.silent_cloud = False

    async def cloud(self, reader, writer):
        task = asyncio.current_task()
        self.tasks.add(task)
        try:
            while wire := await read_frame(reader, 30):
                if not self.silent_cloud:
                    if ack := acknowledgement(Frame.from_bytes(wire)):
                        writer.write(ack.to_bytes())
                        await writer.drain()
        except (OSError, TimeoutError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.tasks.discard(task)

    async def device(self, reader):
        try:
            while wire := await read_frame(reader, 30):
                request = Frame.from_bytes(wire)
                if request.function == 4:
                    self.acks += 1
                    continue
                body = response_body(request)
                register = int.from_bytes(body[:2], "big")
                if request.function == 5:
                    assert body[:2] == body[2:]
                    value = self.value if register == 3 else self.registers[register]
                    reply = body + value.to_bytes(2, "big")
                elif request.function == 6:
                    value = int.from_bytes(body[2:], "big")
                    self.writes.append(value)
                    if not self.discard:
                        if register == 3:
                            self.value = value
                        else:
                            self.registers[register] = value
                    reply = body[:2] + b"\0"
                elif request.function == 24:
                    reply = b"\0\x1f\0"
                else:
                    raise AssertionError(f"Unexpected command {request.function}")
                response = Frame(
                    request.transaction,
                    6,
                    request.unit,
                    request.function,
                    logger_prefix(self.logger, 6) + reply,
                )
                self.writer.write(response.to_bytes())
                await self.writer.drain()
        except (OSError, TimeoutError):
            pass


async def main():
    hass = await boot()
    mqtt = MqttSettings("growatt-features-mqtt")
    case = next(
        case
        for case in json.loads((BASE / "fixtures/telemetry_cases.json").read_text())
        if case["profile"] == "mod-6" and not case["include_all"]
    )
    # Establish the existing 32 telemetry entities before enabling extra features.
    original = Publisher(mqtt)
    original.start()
    values = case["expected"] | {"pvserial": IDENTITY, "datalogserial": "TESTLOG001"}
    await original.publish(Telemetry(values, None, "mod-6"))
    await until(lambda: len(entities(hass)) >= 32)
    before = {
        key: value for key, value in entities(hass).items() if key.startswith(f"grott_{IDENTITY}_")
    }
    assert len(before) == 32
    await original.close()
    simulator = SyntheticInverter()
    cloud = await asyncio.start_server(simulator.cloud, "127.0.0.1", 0)
    relay_settings = RelaySettings(
        "127.0.0.1", cloud.sockets[0].getsockname()[1], listen_port=0, cloud_response_seconds=0.3
    )
    selection = SelectionSettings(
        strict=True, device_families={IDENTITY: "mod", "QUALIFY002": "sph"}
    )
    pipeline = Pipeline(Settings(relay_settings, mqtt, "auto", selection))
    relay = Relay(relay_settings, pipeline.observe)
    pipeline.bind_transport(relay)
    pipeline.start()
    device_task = None
    storage_task = None
    storage = SyntheticInverter("TESTLOG002")
    states_seen = []
    report = {}
    try:
        async with relay:
            reader, simulator.writer = await asyncio.open_connection(*relay.addresses[0][:2])
            device_task = asyncio.create_task(simulator.device(reader))
            frame = Frame.from_bytes(bytes.fromhex(case["wire"]))
            payload = bytearray(frame.payload)
            payload[:10] = b"TESTLOG001"
            payload[30:40] = IDENTITY.encode()
            frame = Frame(100, 6, frame.unit, 4, bytes(payload))
            simulator.writer.write(frame.to_bytes())
            await simulator.writer.drain()
            await until(lambda: f"ha_growatt_{IDENTITY}_output_limit" in entities(hass))
            ids = entities(hass)
            number = ids[f"ha_growatt_{IDENTITY}_output_limit"]
            connected = ids[f"ha_growatt_{IDENTITY}_connected"]
            connection = ids[f"ha_growatt_{IDENTITY}_connection"]
            result = ids[f"ha_growatt_{IDENTITY}_command_result"]
            refresh = ids[f"ha_growatt_{IDENTITY}_refresh"]
            await until(lambda: hass.states.get(number) and hass.states.get(number).state == "75")
            assert hass.states.get(connected).state == "on"
            assert hass.states.get(connection).state == "cloud"
            assert before == {
                key: value for key, value in ids.items() if key.startswith(f"grott_{IDENTITY}_")
            }
            report["existing_entities_and_discovery"] = "passed"

            def changed(event):
                if event.data.get("entity_id") == number and event.data.get("new_state"):
                    states_seen.append(event.data["new_state"].state)

            remove = hass.bus.async_listen("state_changed", changed)
            await hass.services.async_call(
                "number", "set_value", {"entity_id": number, "value": 42}, blocking=True
            )
            await until(lambda: hass.states.get(number).state == "42")
            assert simulator.writes == [42]
            assert "verified" in hass.states.get(result).state
            simulator.discard = True
            await hass.services.async_call(
                "number", "set_value", {"entity_id": number, "value": 30}, blocking=True
            )
            await until(lambda: "did not apply" in hass.states.get(result).state)
            assert "30" not in states_seen
            simulator.discard = False
            simulator.value = 85
            await hass.services.async_call("button", "press", {"entity_id": refresh}, blocking=True)
            await until(lambda: hass.states.get(number).state == "85")
            report["controls_readback_rejection_and_refresh"] = "passed"
            remove()

            simulator.silent_cloud = True
            simulator.writer.write(Frame(101, 6, frame.unit, 4, bytes(payload)).to_bytes())
            await until(lambda: relay.connection(IDENTITY) == "local")
            await until(lambda: hass.states.get(connection).state == "local")
            assert simulator.acks >= 2
            assert hass.states.get(connected).state == "on"
            await hass.services.async_call(
                "number", "set_value", {"entity_id": number, "value": 60}, blocking=True
            )
            await until(lambda: hass.states.get(number).state == "60")
            report["cloud_fallback_readings_and_controls"] = "passed"

            # A separate synthetic SPH proves the actual MQTT switch platform
            # uses the documented register and the same read-back path.
            storage_case = next(
                item
                for item in json.loads((BASE / "fixtures/telemetry_cases.json").read_text())
                if item["profile"] == "sph-6" and not item["include_all"]
            )
            storage_frame = Frame.from_bytes(bytes.fromhex(storage_case["wire"]))
            storage_payload = bytearray(storage_frame.payload)
            storage_payload[:10] = b"TESTLOG002"
            storage_payload[30:40] = b"QUALIFY002"
            storage_reader, storage.writer = await asyncio.open_connection(*relay.addresses[0][:2])
            storage_task = asyncio.create_task(storage.device(storage_reader))
            storage.writer.write(
                Frame(110, 6, storage_frame.unit, 4, bytes(storage_payload)).to_bytes()
            )
            await until(lambda: "ha_growatt_QUALIFY002_ac_charge" in entities(hass))
            switch = entities(hass)["ha_growatt_QUALIFY002_ac_charge"]
            await until(lambda: hass.states.get(switch) and hass.states.get(switch).state == "off")
            await hass.services.async_call(
                "switch", "turn_on", {"entity_id": switch}, blocking=True
            )
            await until(lambda: hass.states.get(switch).state == "on")
            assert storage.registers[1092] == 1
            ids = entities(hass)
            report["storage_switch_readback"] = "passed"

            device = pipeline.features.devices[IDENTITY]
            device.last_seen -= 901
            await pipeline.features.publish_status(device)
            await until(lambda: hass.states.get(connected).state == "off")
            assert hass.states.get(before[f"grott_{IDENTITY}_pvpowerout"]).state not in {
                "unknown",
                "unavailable",
            }
            device.last_seen = asyncio.get_running_loop().time()
            entry = hass.config_entries.async_entries("mqtt")[0]
            assert await hass.config_entries.async_reload(entry.entry_id)
            await until(lambda: hass.states.get(connected).state == "on")
            assert ids == entities(hass)
            report["freshness_and_mqtt_reload"] = "passed"
            saved = CONFIG / "feature-identities.json"
            if saved.exists():
                assert json.loads(saved.read_text()) == ids
                report["core_and_broker_restart_identities"] = "passed"
            saved.write_text(json.dumps(ids))
            simulator.writer.close()
            await simulator.writer.wait_closed()
            await until(lambda: hass.states.get(number).state == "unavailable")
            report["disconnected_controls_unavailable"] = "passed"
    finally:
        if storage.writer:
            storage.writer.close()
            await storage.writer.wait_closed()
        if storage_task:
            storage_task.cancel()
            await asyncio.gather(storage_task, return_exceptions=True)
        if device_task:
            device_task.cancel()
            await asyncio.gather(device_task, return_exceptions=True)
        await pipeline.close()
        cloud.close()
        for task in list(simulator.tasks):
            task.cancel()
        await asyncio.gather(*simulator.tasks, return_exceptions=True)
        await cloud.wait_closed()
        await hass.async_stop()
    with sqlite3.connect(CONFIG / "home-assistant_v2.db") as database:
        metadata = dict(
            database.execute(
                "select entity_id, metadata_id from states_meta where entity_id in ("
                + ",".join("?" for _ in before)
                + ")",
                list(before.values()),
            )
        )
    assert len(metadata) == 32
    history = CONFIG / "feature-history.json"
    if history.exists():
        assert json.loads(history.read_text()) == metadata
        report["telemetry_history_metadata_preserved"] = "passed"
    history.write_text(json.dumps(metadata))
    (CONFIG / "feature-result.json").write_text(json.dumps(report))
    print(json.dumps(report), flush=True)


asyncio.run(main())
