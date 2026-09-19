"""Exercise the real Paho client against a small loopback MQTT peer."""

import asyncio
import json

from ha_growatt.publisher import MqttSettings, Publisher
from ha_growatt.telemetry import Telemetry


async def read_packet(reader):
    header = (await reader.readexactly(1))[0]
    length = 0
    multiplier = 1
    for _ in range(4):
        digit = (await reader.readexactly(1))[0]
        length += (digit & 127) * multiplier
        if not digit & 128:
            return header, await reader.readexactly(length)
        multiplier *= 128
    raise AssertionError("Invalid MQTT packet length")


def test_real_mqtt_handshake_qos_and_discovery_state_order():
    async def scenario():
        publications = []
        connected = asyncio.Event()
        finished = asyncio.Event()
        errors = []

        async def broker(reader, writer):
            try:
                header, body = await read_packet(reader)
                assert header == 0x10
                assert body[:7] == b"\x00\x04MQTT\x04"
                writer.write(b"\x20\x02\x00\x00")
                await writer.drain()
                header, body = await read_packet(reader)
                assert header == 0x82
                assert b"homeassistant/status" in body
                writer.write(b"\x90\x03" + body[:2] + b"\x01")
                await writer.drain()
                connected.set()
                while True:
                    header, body = await read_packet(reader)
                    if header == 0xE0:
                        break
                    assert header >> 4 == 3
                    assert (header >> 1) & 3 == 1
                    size = int.from_bytes(body[:2], "big")
                    topic = body[2 : 2 + size].decode()
                    identifier = body[2 + size : 4 + size]
                    publications.append((topic, body[4 + size :], bool(header & 1)))
                    writer.write(b"\x40\x02" + identifier)
                    await writer.drain()
            except Exception as error:
                errors.append(error)
            finally:
                writer.close()
                await writer.wait_closed()
                finished.set()

        server = await asyncio.start_server(broker, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        publisher = Publisher(MqttSettings("127.0.0.1", port=port))
        publisher.start()
        try:
            async with asyncio.timeout(5):
                await connected.wait()
                await publisher.publish(
                    Telemetry({"pvserial": "INVERT0001", "pvpowerout": 25000}, None, "classic-6")
                )
        finally:
            await publisher.close()
            async with asyncio.timeout(5):
                await finished.wait()
            server.close()
            await server.wait_closed()
        assert not errors
        assert len(publications) == 33
        assert all(topic.endswith("/config") and retain for topic, _, retain in publications[:32])
        topic, payload, retain = publications[-1]
        assert topic == "homeassistant/grott/INVERT0001/state"
        assert not retain
        assert json.loads(payload)["pvpowerout"] == 25000

    asyncio.run(scenario())
