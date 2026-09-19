import asyncio
from contextlib import asynccontextmanager

import pytest

from ha_growatt.protocol import Frame, read_frame
from ha_growatt.relay import Relay, RelaySettings


@asynccontextmanager
async def echo_server():
    handlers = set()

    async def handle(reader, writer):
        task = asyncio.current_task()
        handlers.add(task)
        try:
            while data := await reader.read(65536):
                writer.write(data)
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()
            handlers.discard(task)

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1]
    finally:
        server.close()
        await server.wait_closed()
        for task in list(handlers):
            task.cancel()
        await asyncio.gather(*handlers, return_exceptions=True)


def settings(port, **overrides):
    return RelaySettings(upstream_host="127.0.0.1", upstream_port=port, listen_port=0, **overrides)


async def connect(relay):
    return await asyncio.open_connection("127.0.0.1", relay.addresses[0][1])


async def disconnect(writer):
    writer.close()
    await writer.wait_closed()


def test_fragmented_frames_reach_cloud_and_return_unchanged():
    async def scenario():
        async with echo_server() as port, Relay(settings(port)) as relay:
            reader, writer = await connect(relay)
            first = Frame(1, 6, 1, 4, bytes(100)).to_bytes()
            second = Frame(2, 2, 1, 0x16, b"LOGGER0001").to_bytes()
            for chunk in [first[:3], first[3:12], first[12:] + second]:
                writer.write(chunk)
                await writer.drain()
            assert await read_frame(reader, 2) == first
            assert await read_frame(reader, 2) == second
            assert relay.stats.device_frames == 2
            assert relay.stats.cloud_frames == 2
            await disconnect(writer)
        assert relay.active_connections == 0

    asyncio.run(scenario())


def test_blocked_command_does_not_remove_adjacent_response():
    async def scenario():
        async with echo_server() as port, Relay(settings(port)) as relay:
            reader, writer = await connect(relay)
            blocked = Frame(1, 6, 1, 0x18, b"command").to_bytes()
            accepted = Frame(2, 6, 1, 4, b"\x00").to_bytes()
            writer.write(blocked + accepted)
            await writer.drain()
            assert await read_frame(reader, 2) == accepted
            assert relay.stats.blocked_frames == 1
            await disconnect(writer)

    asyncio.run(scenario())


def test_slow_observation_and_queue_overflow_do_not_stall_forwarding():
    async def scenario():
        began = asyncio.Event()
        release = asyncio.Event()

        async def slow(direction, frame):
            began.set()
            await release.wait()

        async with echo_server() as port, Relay(settings(port, queue_size=1), slow) as relay:
            reader, writer = await connect(relay)
            wire = Frame(1, 6, 1, 4, b"\x00").to_bytes()
            writer.write(wire)
            await writer.drain()
            await asyncio.wait_for(began.wait(), 2)
            writer.write(wire * 20)
            await writer.drain()
            for _ in range(21):
                assert await read_frame(reader, 2) == wire
            assert relay.stats.dropped_observations > 0
            release.set()
            await disconnect(writer)

    asyncio.run(scenario())


def test_observer_exception_is_contained():
    async def scenario():
        called = asyncio.Event()

        async def broken(direction, frame):
            called.set()
            raise ValueError("test observer failure")

        async with echo_server() as port, Relay(settings(port), broken) as relay:
            reader, writer = await connect(relay)
            wire = Frame(1, 2, 1, 4, b"x").to_bytes()
            writer.write(wire)
            await writer.drain()
            assert await read_frame(reader, 2) == wire
            await asyncio.wait_for(called.wait(), 2)
            assert relay.stats.observation_failures > 0
            await disconnect(writer)

    asyncio.run(scenario())


def test_bad_checksum_is_forwarded_but_never_observed():
    async def scenario():
        observed = []

        async def observe(direction, frame):
            observed.append(frame)

        async with echo_server() as port, Relay(settings(port), observe) as relay:
            reader, writer = await connect(relay)
            wire = Frame(1, 6, 1, 4, b"x").to_bytes()
            wire = wire[:-1] + bytes([wire[-1] ^ 1])
            writer.write(wire)
            await writer.drain()
            assert await read_frame(reader, 2) == wire
            assert relay.stats.malformed_frames == 2
            assert not observed
            await disconnect(writer)

    asyncio.run(scenario())


def test_connection_limit_and_shutdown():
    async def scenario():
        async with echo_server() as port:
            relay = Relay(settings(port, max_connections=1))
            await relay.start()
            reader, writer = await connect(relay)
            other_reader, other_writer = await connect(relay)
            assert await asyncio.wait_for(other_reader.read(1), 2) == b""
            assert relay.stats.rejected == 1
            await asyncio.wait_for(relay.close(), 2)
            assert await asyncio.wait_for(reader.read(1), 2) == b""
            assert relay.active_connections == 0
            await disconnect(writer)
            await disconnect(other_writer)
            await relay.close()
            with pytest.raises(RuntimeError):
                await relay.start()

    asyncio.run(scenario())


def test_frame_timeout_closes_incomplete_connection():
    async def scenario():
        async with echo_server() as port, Relay(settings(port, frame_seconds=0.05)) as relay:
            reader, writer = await connect(relay)
            writer.write(bytes.fromhex("0001000600030104"))
            await writer.drain()
            assert await asyncio.wait_for(reader.read(1), 2) == b""
            assert relay.stats.session_failures == 1
            await disconnect(writer)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_connections": 0},
        {"queue_size": 0},
        {"frame_seconds": -1},
        {"blocked_cloud_functions": frozenset({256})},
    ],
)
def test_invalid_settings(overrides):
    with pytest.raises(ValueError):
        settings(5279, **overrides)
