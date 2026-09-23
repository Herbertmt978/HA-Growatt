"""Modbus polling contract against a small TCP gateway, not a mocked reader."""

import asyncio
from datetime import UTC, datetime

import pytest

from ha_growatt.discovery import sensor_value, sensors_for
from ha_growatt.modbus_receiver import PROFILES, ModbusReceiver, decode_input_registers


def _u32(words, first, value):
    words[first], words[first + 1] = value >> 16, value & 0xFFFF


def _registers(profile, *, total=12345):
    if profile == "min-3000-v124":
        words = {address: 0 for address in range(3000, 3079)}
        words[3000] = 1
        _u32(words, 3001, 20450)
        words[3003], words[3004] = 3450, 59
        _u32(words, 3005, 20355)
        words[3007], words[3008] = 0, 0
        _u32(words, 3023, 19990)
        words[3025], words[3026], words[3027] = 5002, 2310, 87
        _u32(words, 3049, 74)
        _u32(words, 3051, total)
        _u32(words, 3053, total + 20)
        _u32(words, 3055, 72)
        _u32(words, 3057, total + 10)
    elif profile == "mic-0-v314":
        words = {address: 0 for address in range(58)}
        words[0] = 1
        _u32(words, 1, 20450)
        words[3], words[4] = 3450, 59
        _u32(words, 5, 20355)
        _u32(words, 11, 19990)
        words[13], words[14], words[15] = 5002, 2310, 87
        _u32(words, 26, 74)
        _u32(words, 28, total)
        words[32], words[41] = 254, 271
        _u32(words, 48, 72)
        _u32(words, 50, total + 10)
        _u32(words, 56, total + 20)
    else:
        words = {address: 0 for address in range(67)}
        words[0] = 1
        _u32(words, 1, 20450)
        words[3], words[4] = 3450, 59
        _u32(words, 5, 20355)
        _u32(words, 35, 19990)
        words[37], words[38], words[39] = 5002, 2310, 87
        _u32(words, 53, 74)
        _u32(words, 55, total)
        _u32(words, 59, 72)
        _u32(words, 61, total + 10)
    return words


def _response(request, values, *, broken=False):
    assert request[2:4] == b"\0\0"
    assert request[7] == 4  # Input-register reads only; no control functions.
    first = int.from_bytes(request[8:10], "big")
    count = int.from_bytes(request[10:12], "big")
    payload = bytes((4, count * 2)) + b"".join(
        values[first + offset].to_bytes(2, "big") for offset in range(count)
    )
    transaction = b"\xff\xff" if broken else request[:2]
    return (
        transaction + request[2:4] + (len(payload) + 1).to_bytes(2, "big") + request[6:7] + payload
    )


@pytest.mark.parametrize("profile", list(PROFILES))
def test_real_tcp_poll_decodes_core_readings_and_uses_only_input_reads(profile):
    async def scenario():
        values = _registers(profile)
        requests = []

        async def reply(reader, writer):
            request = await reader.readexactly(12)
            requests.append(
                (
                    request[7],
                    int.from_bytes(request[8:10], "big"),
                    int.from_bytes(request[10:12], "big"),
                )
            )
            writer.write(_response(request, values))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            receiver = ModbusReceiver(
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
                identity="TEST_INVERTER",
                profile=profile,
                interval=30,
                timeout=1,
            )
            seen = []
            receiver.subscribe(seen.append)
            assert await receiver.poll_once()
            assert receiver.connected and receiver.measurements == 1
            assert receiver.failed_measurements == 0
            assert len(seen) == 1 and not seen[0].restored
            assert seen[0].identity == "TEST_INVERTER"
            late = []
            receiver.subscribe(late.append)
            assert len(late) == 1 and not late[0].restored
            telemetry = seen[0].snapshot.telemetry
            assert telemetry.values["pvpowerout"] == 19990
            assert telemetry.values["pvenergytotal"] == 12345
            assert telemetry.values["pvfrequentie"] == 5002
            assert "pvserial" not in telemetry.values  # Explicit ID is not a read serial.
            sensors = {
                sensor.key: sensor
                for sensor in sensors_for(
                    wire_profile=telemetry.profile,
                    sensor_metadata=telemetry.sensor_metadata,
                )
            }
            assert (
                sensor_value(
                    sensors["pvpowerout"], telemetry.values, datetime.now(UTC), telemetry.profile
                )
                == 1999
            )
            assert (
                sensor_value(
                    sensors["pvenergytotal"], telemetry.values, datetime.now(UTC), telemetry.profile
                )
                == 1234.5
            )
            assert "pvgridpower" not in sensors  # A VA register is not reported as W.
            if profile == "mic-0-v314":
                assert "pv2voltage" not in sensors  # MIC has one string.
                assert (
                    sensor_value(
                        sensors["pvtemperature"],
                        telemetry.values,
                        datetime.now(UTC),
                        telemetry.profile,
                    )
                    == 25.4
                )
            assert requests == [(4, first, count) for first, count in PROFILES[profile]]
            assert all(count <= 32 for _function, _first, count in requests)
            await receiver.close()

    asyncio.run(scenario())


def test_malformed_response_does_not_publish_partial_values_then_reconnects():
    async def scenario():
        values = _registers("min-3000-v124")
        requests = 0

        async def reply(reader, writer):
            nonlocal requests
            request = await reader.readexactly(12)
            requests += 1
            writer.write(_response(request, values, broken=requests == 2))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            receiver = ModbusReceiver(
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
                identity="TEST_INVERTER",
                profile="min-3000-v124",
                timeout=1,
            )
            seen = []
            receiver.subscribe(seen.append)
            assert not await receiver.poll_once()
            assert not receiver.connected and receiver.failed_measurements == 1
            assert receiver.snapshots == {} and seen == []
            assert await receiver.poll_once()
            assert receiver.connected and receiver.last_error is None
            assert len(seen) == 1 and requests == 4
            await receiver.close()

    asyncio.run(scenario())


def test_timeout_keeps_saved_overnight_reading_stale_and_close_cancels_poll(tmp_path):
    async def scenario():
        values = _registers("legacy-0-v124")
        hold = False
        accepted = asyncio.Event()

        async def reply(reader, writer):
            request = await reader.readexactly(12)
            accepted.set()
            if hold:
                try:
                    await reader.read()
                finally:
                    writer.close()
                return
            writer.write(_response(request, values))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            kwargs = dict(
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
                identity="TEST_INVERTER",
                profile="legacy-0-v124",
                state_path=str(tmp_path / "last-reading.json"),
                interval=30,
                timeout=0.5,
            )
            first = ModbusReceiver(**kwargs)
            assert await first.poll_once()
            original = first.snapshots["TEST_INVERTER"].received_at
            await first.close()

            hold = True
            accepted.clear()
            recovered = ModbusReceiver(**kwargs)
            await recovered.start()
            assert recovered.running
            seen = []
            recovered.subscribe(seen.append)
            assert len(seen) == 1 and seen[0].restored
            assert seen[0].snapshot.received_at == original
            await asyncio.wait_for(accepted.wait(), 2)
            await asyncio.sleep(0.6)
            assert not recovered.connected and recovered.failed_measurements == 1
            assert recovered.snapshots["TEST_INVERTER"].received_at == original
            assert len(seen) == 1
            await recovered.close()
            assert not recovered.running

            accepted.clear()
            closing = ModbusReceiver(**kwargs)
            await closing.start()
            await asyncio.wait_for(accepted.wait(), 2)
            await asyncio.wait_for(closing.close(), 1)
            assert not closing.running

    asyncio.run(scenario())


def test_rejects_wrong_layout_and_backward_lifetime_energy():
    async def scenario():
        values = _registers("min-3000-v124")
        call = 0

        async def reply(reader, writer):
            nonlocal call
            request = await reader.readexactly(12)
            call += 1
            if call > 2:
                _u32(values, 3051, 100)
            writer.write(_response(request, values))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            receiver = ModbusReceiver(
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
                identity="TEST_INVERTER",
                profile="min-3000-v124",
            )
            assert await receiver.poll_once()
            first = receiver.snapshots["TEST_INVERTER"]
            assert not await receiver.poll_once()
            assert receiver.snapshots["TEST_INVERTER"] is first
            assert receiver.measurements == 1 and receiver.failed_measurements == 1
            await receiver.close()

    asyncio.run(scenario())
    wrong = _registers("min-3000-v124")
    wrong[3000] = 0xFF
    with pytest.raises(ValueError, match="layout"):
        decode_input_registers("min-3000-v124", wrong)
    with pytest.raises(ValueError, match="no identifying"):
        decode_input_registers(
            "min-3000-v124",
            {
                address: 0
                for first, count in PROFILES["min-3000-v124"]
                for address in range(first, first + count)
            },
        )
    with pytest.raises(ValueError, match="profile"):
        ModbusReceiver(host="localhost", identity="TEST_INVERTER", profile="unsupported")
    with pytest.raises(ValueError, match="interval"):
        ModbusReceiver(host="localhost", identity="TEST_INVERTER", profile="mic-0-v314", interval=1)
