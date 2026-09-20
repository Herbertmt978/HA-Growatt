import asyncio
from contextlib import asynccontextmanager

import pytest

from ha_growatt.device_protocol import logger_prefix
from ha_growatt.protocol import Frame, read_frame
from ha_growatt.relay import Relay, RelaySettings


def report(protocol=6, function=4, sequence=7):
    width = 30 if protocol == 6 else 10
    return Frame(
        sequence,
        protocol,
        1,
        function,
        logger_prefix("LOGGER0001", protocol)
        + b"INVERT0001".ljust(width, b"\0")
        + bytes([26, 9, 20, 12, 0, 0])
        + bytes(100),
    )


async def receive(reader):
    return Frame.from_bytes(await read_frame(reader, 2))


@asynccontextmanager
async def cloud():
    clients = asyncio.Queue()
    tasks = set()

    async def accept(reader, writer):
        task = asyncio.current_task()
        tasks.add(task)
        try:
            await clients.put((reader, writer))
            await asyncio.Event().wait()
        finally:
            writer.close()
            await writer.wait_closed()
            tasks.discard(task)

    server = await asyncio.start_server(accept, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], clients
    finally:
        server.close()
        for task in list(tasks):
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await server.wait_closed()


@pytest.mark.parametrize("protocol", [2, 5, 6])
def test_unreachable_cloud_acknowledges_locally_and_observes(protocol):
    async def scenario():
        listener = await asyncio.start_server(lambda *_: None, "127.0.0.1", 0)
        port = listener.sockets[0].getsockname()[1]
        listener.close()
        await listener.wait_closed()
        observed = asyncio.Queue()

        async def observe(direction, frame):
            await observed.put((direction, frame))

        async with Relay(
            RelaySettings("127.0.0.1", port, listen_port=0, connection_seconds=0.05), observe
        ) as relay:
            reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
            announce = report(protocol, 3)
            writer.write(announce.to_bytes())
            assert await receive(reader) == Frame(7, protocol, 1, 3, b"\0")
            assert (await receive(reader)).function == 24
            assert await asyncio.wait_for(observed.get(), 2) == ("device", announce)
            assert relay.connection("INVERT0001") == "local"
            ping = Frame(8, protocol, 1, 22, logger_prefix("LOGGER0001", protocol))
            writer.write(ping.to_bytes())
            assert await receive(reader) == ping
            writer.close()
            await writer.wait_closed()
        assert not relay._connections

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["disconnect", "silent"])
def test_mid_session_failure_answers_outstanding_record_once(failure):
    async def scenario():
        async with cloud() as (port, clients):
            settings = RelaySettings("127.0.0.1", port, listen_port=0, cloud_response_seconds=0.08)
            async with Relay(settings) as relay:
                reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
                upstream_reader, upstream_writer = await clients.get()
                first = report()
                writer.write(first.to_bytes())
                assert await receive(upstream_reader) == first
                ack = Frame(7, 6, 1, 4, b"\0")
                upstream_writer.write(ack.to_bytes())
                assert await receive(reader) == ack
                second = report(sequence=8)
                writer.write(second.to_bytes())
                assert await receive(upstream_reader) == second
                if failure == "disconnect":
                    upstream_writer.close()
                    await upstream_writer.wait_closed()
                assert await receive(reader) == Frame(8, 6, 1, 4, b"\0")
                assert relay.stats.fallback_connections == 1
                assert relay.connection("INVERT0001") == "local"
                writer.write(report(sequence=9).to_bytes())
                assert await receive(reader) == Frame(9, 6, 1, 4, b"\0")
                with pytest.raises(TimeoutError):
                    await read_frame(reader, 0.03)
                writer.close()
                await writer.wait_closed()
                # A fresh connection tries the cloud again.
                reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
                upstream_reader, upstream_writer = await clients.get()
                writer.write(first.to_bytes())
                assert await receive(upstream_reader) == first
                upstream_writer.write(ack.to_bytes())
                assert await receive(reader) == ack
                assert relay.connection("INVERT0001") == "cloud"
                writer.close()
                await writer.wait_closed()

    asyncio.run(scenario())


def test_fallback_can_be_disabled_and_other_sessions_stay_independent():
    async def scenario():
        async with cloud() as (port, clients):
            settings = RelaySettings("127.0.0.1", port, listen_port=0, cloud_fallback=False)
            async with Relay(settings) as relay:
                first_reader, first_writer = await asyncio.open_connection(*relay.addresses[0][:2])
                _, first_cloud = await clients.get()
                second_reader, second_writer = await asyncio.open_connection(
                    *relay.addresses[0][:2]
                )
                second_cloud_reader, second_cloud_writer = await clients.get()
                first_cloud.close()
                await first_cloud.wait_closed()
                assert await asyncio.wait_for(first_reader.read(1), 2) == b""
                record = report()
                second_writer.write(record.to_bytes())
                assert await receive(second_cloud_reader) == record
                ack = Frame(7, 6, 1, 4, b"\0")
                second_cloud_writer.write(ack.to_bytes())
                assert await receive(second_reader) == ack
                assert relay.stats.local_replies == 0
                for writer in (first_writer, second_writer):
                    writer.close()
                    await writer.wait_closed()

    asyncio.run(scenario())
