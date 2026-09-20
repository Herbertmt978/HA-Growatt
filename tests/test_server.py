import asyncio
import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from ha_growatt.protocol import Frame, read_frame
from ha_growatt.relay import RelaySettings
from ha_growatt.server import Server, acknowledgement, logger_prefix, register_command, time_command

FIXTURES = Path(__file__).parent / "fixtures"
WIRE_CASES = json.loads((FIXTURES / "server_wire_cases.json").read_text())
API_CASES = json.loads((FIXTURES / "server_api_cases.json").read_text())


@pytest.mark.parametrize("case", WIRE_CASES)
def test_observed_protocol_acknowledgements(case):
    frame = Frame.from_bytes(bytes.fromhex(case["request"]))
    actual = []
    if response := acknowledgement(frame):
        actual.append(response.to_bytes().hex())
    if frame.function == 3:
        actual.append(
            time_command("LOGGER0001", frame.protocol, 1, datetime(2026, 9, 20, 10))
            .to_bytes()
            .hex()
        )
    assert actual == case["outputs"]


@pytest.mark.parametrize("case", API_CASES)
def test_observed_http_register_commands(case):
    path = urlsplit(case["path"])
    options = {key: value[-1] for key, value in parse_qs(path.query).items()}
    actual = register_command(
        "LOGGER0001", case["protocol"], 1, 1, case["method"], path.path.strip("/"), options
    )
    assert [actual.to_bytes().hex()] == case["wires"]


async def _get_frame(reader):
    return Frame.from_bytes(await read_frame(reader, 2))


@pytest.mark.parametrize("protocol", [2, 5, 6])
def test_native_sockets_registers_fragmentation_and_reconnection(protocol):
    async def scenario():
        observed = []

        async def observer(direction, frame):
            observed.append(frame)

        settings = RelaySettings("unused.invalid", listen_port=0)
        async with Server(settings, observer, api_port=0, response_seconds=0.2) as server:
            reader, writer = await asyncio.open_connection(*server.address[:2])
            width = 30 if protocol == 6 else 10
            payload = logger_prefix("LOGGER0001", protocol) + b"INVERT0001".ljust(width, b"\0")
            payload += bytes([26, 9, 20, 10, 0, 0]) + bytes(100)
            announce = Frame(20, protocol, 1, 3, payload)
            wire = announce.to_bytes()
            writer.write(wire[:5])
            await writer.drain()
            writer.write(wire[5:])
            await writer.drain()
            assert await _get_frame(reader) == Frame(20, protocol, 1, 3, b"\0")
            assert (await _get_frame(reader)).function == 24
            assert "INVERT0001" in server.registry()["LOGGER0001"]
            request = asyncio.create_task(
                server.api(
                    "GET", "/inverter?inverter=INVERT0001&command=register&register=31&format=dec"
                )
            )
            command = await _get_frame(reader)
            assert command.function == 5
            writer.write(
                Frame(
                    command.transaction,
                    protocol,
                    1,
                    5,
                    logger_prefix("LOGGER0001", protocol) + b"\x00\x1f\x00\x1f\x00\x7b",
                ).to_bytes()
            )
            await writer.drain()
            assert await request == (200, '{"value": 123}')
            status, body = await server.api("GET", "/inverter?command=regall")
            assert status == 200 and json.loads(body) == {"001f": {"value": "007b"}}
            request = asyncio.create_task(
                server.api(
                    "PUT",
                    "/inverter?inverter=INVERT0001&command=multiregister&startregister=10&endregister=12&value=000100020003",
                )
            )
            command = await _get_frame(reader)
            assert command.function == 16
            writer.write(
                Frame(
                    command.transaction,
                    protocol,
                    1,
                    16,
                    logger_prefix("LOGGER0001", protocol) + b"\x00\x0a\x00\x0c\x00",
                ).to_bytes()
            )
            await writer.drain()
            assert await request == (200, "OK")
            http_reader, http_writer = await asyncio.open_connection(*server.api_address[:2])
            http_writer.write(b"GET /datalogger HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await http_writer.drain()
            response = await asyncio.wait_for(http_reader.read(), 2)
            assert response.startswith(b"HTTP/1.1 200")
            assert "LOGGER0001" in json.loads(response.split(b"\r\n\r\n", 1)[1])
            http_writer.close()
            await http_writer.wait_closed()
            new_reader, new_writer = await asyncio.open_connection(*server.address[:2])
            new_writer.write(announce.to_bytes())
            await new_writer.drain()
            await _get_frame(new_reader)
            await _get_frame(new_reader)
            assert await asyncio.wait_for(reader.read(), 2) == b""
            writer.close()
            await writer.wait_closed()
            assert len(server.registry()) == 1
            assert observed
            new_writer.close()
            await new_writer.wait_closed()

    asyncio.run(scenario())


def test_server_shutdown_closes_incomplete_http_requests():
    async def scenario():
        async with Server(RelaySettings("unused.invalid", listen_port=0), api_port=0) as server:
            reader, writer = await asyncio.open_connection(*server.api_address[:2])
            writer.write(b"GET /")
            await writer.drain()
        try:
            assert await asyncio.wait_for(reader.read(), 1) == b""
        except ConnectionResetError:
            # Linux may reset a socket closed with an unread partial request.
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionResetError:
            pass

    asyncio.run(scenario())
