"""Standalone receiver commands match one current datalogger session."""

import asyncio

import pytest

from ha_growatt.device_protocol import logger_prefix
from ha_growatt.protocol import Frame, read_frame
from ha_growatt.relay import RelaySettings
from ha_growatt.server import Server


async def received(reader):
    return Frame.from_bytes(await read_frame(reader, 2))


def test_standalone_register_command_matches_sequence_function_and_address():
    async def scenario():
        async with Server(
            RelaySettings("unused.invalid", listen_port=0), api_port=0, response_seconds=0.2
        ) as server:
            reader, writer = await asyncio.open_connection(*server.address[:2])
            prefix = logger_prefix("LOGGER0001", 6)
            announce = Frame(10, 6, 1, 3, prefix + b"INVERT0001".ljust(30, b"\0") + bytes(100))
            writer.write(announce.to_bytes())
            await writer.drain()
            await received(reader)
            await received(reader)
            first_session = server.session_key("INVERT0001")
            assert first_session is not None
            task = asyncio.create_task(server.command("INVERT0001", 5, b"\0\3\0\3"))
            request = await received(reader)
            wrong = Frame(request.transaction, 6, 1, 5, prefix + b"\0\4\0\4\0\x32")
            writer.write(wrong.to_bytes())
            await writer.drain()
            await asyncio.sleep(0.01)
            assert not task.done()
            right = Frame(request.transaction, 6, 1, 5, prefix + b"\0\3\0\3\0\x32")
            writer.write(right.to_bytes())
            await writer.drain()
            assert await task == right
            assert not next(iter(server._sessions)).command_pending
            period_body = b"\x04L\x04N\x16\0\x06\0\0\x01"
            period_task = asyncio.create_task(server.command("INVERT0001", 16, period_body))
            period_request = await received(reader)
            period_reply = Frame(
                period_request.transaction, 6, 1, 16, prefix + period_body[:4] + b"\0"
            )
            writer.write(period_reply.to_bytes())
            await writer.drain()
            assert await period_task == period_reply
            with pytest.raises(ValueError):
                await server.command("INVERT0001", 16, b"arbitrary")
            writer.close()
            await writer.wait_closed()

    asyncio.run(scenario())


def test_standalone_command_expiring_behind_session_lock_never_sends():
    async def scenario():
        async with Server(
            RelaySettings("unused.invalid", listen_port=0), api_port=0, response_seconds=0.2
        ) as server:
            reader, writer = await asyncio.open_connection(*server.address[:2])
            prefix = logger_prefix("LOGGER0001", 6)
            writer.write(
                Frame(10, 6, 1, 3, prefix + b"INVERT0001".ljust(30, b"\0") + bytes(100)).to_bytes()
            )
            await writer.drain()
            await received(reader)
            await received(reader)
            session = next(iter(server._sessions))
            expired = False

            def before_send():
                if expired:
                    raise ValueError("Expired")

            async with session.lock:
                task = asyncio.create_task(
                    server.command("INVERT0001", 6, b"\0\3\0\x32", before_send=before_send)
                )
                await asyncio.sleep(0.01)
                expired = True
            with pytest.raises(ValueError, match="Expired"):
                await task
            with pytest.raises(TimeoutError):
                await read_frame(reader, 0.03)
            assert not session.command_pending
            writer.close()
            await writer.wait_closed()

    asyncio.run(scenario())
