import argparse
import asyncio

import pytest

from ha_growatt.modbus_scan import ModbusReader, ReadError, parse_range, scan


def test_ranges_and_transport_limits():
    assert parse_range("3000:125") == (3000, 125)
    for value in ("3000", "-1:10", "65535:2", "0:513", "0:0"):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_range(value)
    with pytest.raises(ValueError):
        ModbusReader("gateway", 502, 1, 0.1, 3)


def test_wire_uses_only_read_functions_and_validates_reply():
    async def scenario():
        functions = []
        received_at = []

        async def reply(reader, writer):
            request = await reader.readexactly(12)
            functions.append(request[7])
            received_at.append(asyncio.get_running_loop().time())
            count = int.from_bytes(request[10:12], "big")
            values = b"".join(i.to_bytes(2, "big") for i in range(count))
            payload = bytes((request[7], count * 2)) + values
            writer.write(
                request[:4] + (len(payload) + 1).to_bytes(2, "big") + request[6:7] + payload
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            port = server.sockets[0].getsockname()[1]
            client = ModbusReader("127.0.0.1", port, 1, 0.5, 2)
            assert await client.read("input", 3000, 2) == [0, 1]
            assert await client.read("holding", 3000, 1) == [0]
            with pytest.raises(ValueError):
                await client.read("coil", 0, 1)
        assert functions == [4, 3]
        assert received_at[1] - received_at[0] >= 0.45

    asyncio.run(scenario())


def test_failed_blocks_are_split_and_limits_stay_bounded():
    class Reader:
        def __init__(self):
            self.calls = []

        async def read(self, kind, first, count):
            self.calls.append((kind, first, count))
            if first <= 2 < first + count:
                raise ReadError("Unsupported", splittable=True)
            return [first + index for index in range(count)]

    async def scenario():
        reader = Reader()
        report = await scan(reader, [(0, 4)], max_requests=16)
        assert report["private"] and report["transport"] == "direct Modbus TCP"
        assert set(report["results"]["input"]["values"]) == {"0", "1", "3"}
        assert report["results"]["input"]["unavailable"] == [
            {"start": 2, "count": 1, "reason": "Unsupported"}
        ]
        assert {kind for kind, _, _ in reader.calls} == {"input", "holding"}
        limited = await scan(Reader(), [(0, 4)], max_requests=1)
        assert limited["request_limit_reached"]
        assert limited["requests"] == 1
        with pytest.raises(ValueError):
            await scan(reader, [(0, 513)])

    asyncio.run(scenario())


def test_connection_failure_does_not_fan_out_into_more_requests():
    class Offline:
        def __init__(self):
            self.calls = 0

        async def read(self, *_):
            self.calls += 1
            raise ReadError("Gateway unavailable")

    async def scenario():
        reader = Offline()
        result = await scan(reader, [(3000, 32)], kinds=("input",))
        assert reader.calls == 1
        assert result["results"]["input"]["unavailable"] == [
            {"start": 3000, "count": 32, "reason": "Gateway unavailable"}
        ]

    asyncio.run(scenario())


def test_malformed_reply_is_rejected():
    async def scenario():
        async def reply(reader, writer):
            request = await reader.readexactly(12)
            writer.write(request[:4] + b"\x00\x05" + request[6:7] + bytes((4, 2, 0, 1)))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            client = ModbusReader("127.0.0.1", server.sockets[0].getsockname()[1], 1, 0.5, 2)
            with pytest.raises(ReadError, match="Mismatched"):
                await client.read("input", 1, 2)

    asyncio.run(scenario())
