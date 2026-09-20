import asyncio

import pytest
from test_fallback import cloud, receive, report

from ha_growatt.device_protocol import logger_prefix
from ha_growatt.protocol import Frame, read_frame
from ha_growatt.relay import Relay, RelaySession, RelaySettings


@pytest.mark.parametrize("protocol", [2, 5, 6])
def test_local_and_cloud_register_requests_with_same_sequence_stay_separate(protocol):
    async def scenario():
        async with cloud() as (port, clients):
            async with Relay(RelaySettings("127.0.0.1", port, listen_port=0)) as relay:
                reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
                cloud_reader, cloud_writer = await clients.get()
                writer.write(report(protocol).to_bytes())
                await receive(cloud_reader)
                cloud_writer.write(Frame(7, protocol, 1, 4, b"\0").to_bytes())
                await receive(reader)
                task = asyncio.create_task(relay.command("INVERT0001", 5, b"\0\3\0\3"))
                local = await receive(reader)
                # The cloud happens to choose our active sequence and register.
                cloud_request = Frame(local.transaction, protocol, 1, 5, local.payload)
                cloud_writer.write(cloud_request.to_bytes())
                forwarded = await receive(reader)
                assert forwarded.transaction != local.transaction
                prefix = logger_prefix("LOGGER0001", protocol)
                writer.write(
                    Frame(
                        forwarded.transaction, protocol, 1, 5, prefix + b"\0\3\0\3\0\x42"
                    ).to_bytes()
                )
                response = await receive(cloud_reader)
                assert response.transaction == cloud_request.transaction
                assert not task.done()
                # An unrelated register cannot complete the local request.
                writer.write(
                    Frame(local.transaction, protocol, 1, 5, prefix + b"\0\4\0\4\0\x32").to_bytes()
                )
                await asyncio.sleep(0.02)
                assert not task.done()
                valid = Frame(local.transaction, protocol, 1, 5, prefix + b"\0\3\0\3\0\x32")
                writer.write(valid.to_bytes())
                assert await asyncio.wait_for(task, 2) == valid
                with pytest.raises(TimeoutError):
                    await read_frame(cloud_reader, 0.03)
                writer.close()
                await writer.wait_closed()

    asyncio.run(scenario())


@pytest.mark.parametrize("lock_name", ["command_lock", "send_lock"])
def test_expired_write_waiting_for_transport_is_not_sent(lock_name):
    async def scenario():
        async with cloud() as (port, clients):
            async with Relay(RelaySettings("127.0.0.1", port, listen_port=0)) as relay:
                reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
                cloud_reader, cloud_writer = await clients.get()
                writer.write(report().to_bytes())
                await receive(cloud_reader)
                cloud_writer.write(Frame(7, 6, 1, 4, b"\0").to_bytes())
                await receive(reader)
                lock = getattr(relay._devices["INVERT0001"], lock_name)
                expired = False

                def before_send():
                    if expired:
                        raise ValueError("Command expired")

                async with lock:
                    command = asyncio.create_task(
                        relay.command("INVERT0001", 6, b"\0\3\0\x32", before_send=before_send)
                    )
                    await asyncio.sleep(0.02)
                    assert not command.done()
                    expired = True
                with pytest.raises(ValueError, match="expired"):
                    await command
                with pytest.raises(TimeoutError):
                    await read_frame(reader, 0.03)
                assert not relay._devices["INVERT0001"].pending
                writer.close()
                await writer.wait_closed()

    asyncio.run(scenario())


def test_exhausted_sequence_space_closes_connection_without_reusing_old_sequences():
    class Writer:
        closed = False

        def close(self):
            self.closed = True

    writer = Writer()
    session = RelaySession(writer)
    session.local_sequences.update(range(1, 32768))
    session.cloud_sequences.update(range(32768, 65536))
    with pytest.raises(ConnectionError, match="Reconnect"):
        session.sequence()
    assert writer.closed


def test_loss_after_announce_ack_still_supplies_clock_locally():
    async def scenario():
        async with cloud() as (port, clients):
            async with Relay(RelaySettings("127.0.0.1", port, listen_port=0)) as relay:
                reader, writer = await asyncio.open_connection(*relay.addresses[0][:2])
                cloud_reader, cloud_writer = await clients.get()
                writer.write(report(function=3).to_bytes())
                await receive(cloud_reader)
                cloud_writer.write(Frame(7, 6, 1, 3, b"\0").to_bytes())
                assert (await receive(reader)).function == 3
                cloud_writer.close()
                await cloud_writer.wait_closed()
                assert (await receive(reader)).function == 24
                writer.write(report(sequence=8).to_bytes())
                assert await receive(reader) == Frame(8, 6, 1, 4, b"\0")
                writer.close()
                await writer.wait_closed()

    asyncio.run(scenario())
