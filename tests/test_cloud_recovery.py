import asyncio

import pytest
from test_fallback import cloud, receive, report

from ha_growatt.protocol import Frame, read_frame
from ha_growatt.relay import Relay, RelaySettings


@pytest.mark.parametrize("protocol", [2, 5, 6])
def test_recovery_requires_heartbeat_then_reconnects_without_duplicate_ack(protocol):
    async def scenario():
        async with cloud() as (port, clients):
            settings = RelaySettings(
                "127.0.0.1",
                port,
                listen_port=0,
                cloud_response_seconds=0.05,
                cloud_recovery_seconds=0.08,
            )
            async with Relay(settings) as relay:
                reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
                upstream, upstream_writer = await clients.get()
                first = report(protocol)
                writer.write(first.to_bytes())
                assert await receive(upstream) == first
                upstream_writer.close()
                assert await receive(reader) == Frame(7, protocol, 1, 4, b"\0")
                probe_reader, probe_writer = await asyncio.wait_for(clients.get(), 2)
                ping = await receive(probe_reader)
                assert ping.function == 22
                # A listening endpoint alone has not ended local collection.
                writer.write(report(protocol, sequence=8).to_bytes())
                assert await receive(reader) == Frame(8, protocol, 1, 4, b"\0")
                probe_writer.write(ping.to_bytes())
                assert await asyncio.wait_for(reader.read(), 2) == b""
                assert relay.stats.recovery_reconnects == 1
                writer.close()
                await writer.wait_closed()
                reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
                upstream, upstream_writer = await clients.get()
                writer.write(first.to_bytes())
                assert await receive(upstream) == first
                upstream_writer.write(Frame(7, protocol, 1, 4, b"\0").to_bytes())
                assert await receive(reader) == Frame(7, protocol, 1, 4, b"\0")
                assert relay.connection("INVERT0001") == "cloud"
                writer.close()
                await writer.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("reply", ["silent", "wrong", "command"])
def test_unhealthy_probe_keeps_local_collection(reply):
    async def scenario():
        async with cloud() as (port, clients):
            async with Relay(
                RelaySettings(
                    "127.0.0.1",
                    port,
                    listen_port=0,
                    cloud_response_seconds=0.02,
                    cloud_recovery_seconds=0.04,
                )
            ) as relay:
                reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
                _, old = await clients.get()
                writer.write(report().to_bytes())
                old.close()
                assert (await receive(reader)).function == 4
                probe_reader, probe_writer = await asyncio.wait_for(clients.get(), 2)
                ping = await receive(probe_reader)
                if reply != "silent":
                    probe_writer.write(
                        Frame(
                            99 if reply == "wrong" else ping.transaction,
                            6,
                            1,
                            22 if reply == "wrong" else 6,
                            ping.payload,
                        ).to_bytes()
                    )
                await asyncio.sleep(0.03)
                writer.write(report(sequence=8).to_bytes())
                assert await receive(reader) == Frame(8, 6, 1, 4, b"\0")
                assert relay.stats.recovery_reconnects == 0
                writer.close()
                await writer.wait_closed()

    asyncio.run(scenario())


def test_recovery_waits_for_commands_and_cancels_on_shutdown():
    async def scenario():
        async with cloud() as (port, clients):
            async with Relay(
                RelaySettings(
                    "127.0.0.1",
                    port,
                    listen_port=0,
                    cloud_recovery_seconds=0.03,
                )
            ) as relay:
                reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
                _, old = await clients.get()
                writer.write(report().to_bytes())
                old.close()
                await receive(reader)
                session = relay._devices["INVERT0001"]
                session.last_command = asyncio.get_running_loop().time()
                probe_reader, probe_writer = await asyncio.wait_for(clients.get(), 2)
                probe_writer.write((await receive(probe_reader)).to_bytes())
                with pytest.raises(TimeoutError):
                    await read_frame(reader, 0.08)
                assert relay.stats.recovery_reconnects == 0
            assert not relay._connections
            writer.close()
            await writer.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), "300"])
def test_invalid_recovery_interval(value):
    with pytest.raises(ValueError):
        RelaySettings("localhost", cloud_recovery_seconds=value)
