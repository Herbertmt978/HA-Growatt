"""Modbus polling contract against a small TCP gateway, not a mocked reader."""

import asyncio
from datetime import UTC, datetime

import pytest

from ha_growatt.discovery import sensor_value, sensors_for
from ha_growatt.modbus_receiver import (
    AUTO_PROFILE,
    OPTIONAL_BLOCKS,
    PROFILES,
    ModbusReceiver,
    decode_input_registers,
    detect_input_profile,
)
from ha_growatt.modbus_scan import ReadError


def _u32(words, first, value):
    words[first], words[first + 1] = value >> 16, value & 0xFFFF


def _registers(profile, *, total=12345):
    if profile in {"min-3000-v124", "min-three-string-v124", "tl3-three-phase-v139"}:
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
        if profile in {"min-three-string-v124", "tl3-three-phase-v139"}:
            words.update({address: 0 for address in range(3086, 3109)})
            words[3011], words[3012] = 3600, 41
            _u32(words, 3013, 14760)
            _u32(words, 3063, 31)
            _u32(words, 3065, total + 5)
            words[3086], words[3093], words[3094], words[3095] = 2, 256, 267, 274
            words[3105], words[3106] = 9, 3
            if profile == "tl3-three-phase-v139":
                _u32(words, 3028, 12100)
                words[3030], words[3031] = 2310, 51
                _u32(words, 3032, 11800)
                words[3034], words[3035] = 2320, 49
                _u32(words, 3036, 11500)
                words[3038], words[3039], words[3040] = 4010, 4020, 4030
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


@pytest.mark.parametrize(
    "dtc,has_3000,expected",
    [
        (5200, False, "mic-0-v314"),
        (5200, True, "min-3000-v124"),
        (5201, True, "min-3000-v124"),
        (5100, True, "min-3000-v124"),
    ],
)
def test_auto_identifies_only_supported_device_type_and_input_range(dtc, has_3000, expected):
    class Reader:
        def __init__(self):
            self.calls = []

        async def read(self, kind, first, count):
            self.calls.append((kind, first, count))
            if kind == "holding" and first == 30000:
                return [dtc]
            if kind == "input" and first == 3003 and has_3000:
                return [0]  # An asleep inverter may have no PV voltage.
            raise ReadError("Modbus exception 2", splittable=True)

    async def scenario():
        reader = Reader()
        assert await detect_input_profile(reader) == expected
        assert reader.calls == [("holding", 30000, 1), ("input", 3003, 1)]

    asyncio.run(scenario())


def test_auto_requires_manual_selection_for_unknown_or_ambiguous_family():
    class Reader:
        async def read(self, kind, first, count):
            if kind == "holding" and first == 30000:
                return [5400]  # Shared by MOD and MID; neither has a qualified map here.
            raise ReadError("Modbus exception 2", splittable=True)

    async def scenario():
        with pytest.raises(ValueError, match="device type code"):
            await detect_input_profile(Reader())

    asyncio.run(scenario())


def test_auto_does_not_treat_a_probe_timeout_as_a_mic_identity():
    class Reader:
        async def read(self, kind, first, count):
            if kind == "holding":
                return [5200]
            raise ReadError("TimeoutError")

    async def scenario():
        with pytest.raises(ReadError, match="TimeoutError"):
            await detect_input_profile(Reader())

    asyncio.run(scenario())


def test_auto_can_use_legacy_device_type_register():
    class Reader:
        async def read(self, kind, first, count):
            if kind == "holding" and first == 30000:
                raise ReadError("Modbus exception 2", splittable=True)
            if kind == "holding" and first == 43:
                return [5200]
            if kind == "input" and first == 3003:
                return [0]
            raise AssertionError("Unexpected Modbus request")

    assert asyncio.run(detect_input_profile(Reader())) == "min-3000-v124"


@pytest.mark.parametrize("profile", list(PROFILES))
def test_fast_power_poll_only_reads_power_and_preserves_full_snapshot(profile):
    class Reader:
        def __init__(self, words):
            self.words = words
            self.calls = []

        async def read(self, kind, first, count):
            self.calls.append((kind, first, count))
            return [self.words[first + index] for index in range(count)]

    async def scenario():
        words = _registers(profile)
        receiver = ModbusReceiver(
            host="127.0.0.1",
            identity="FAST",
            profile=profile,
            interval=60,
            fast_power_interval=5,
        )
        reader = Reader(words)
        receiver.reader = reader
        readings = []
        receiver.subscribe(readings.append)
        assert not await receiver.poll_power_once()  # No complete reading yet.
        assert await receiver.poll_once()
        full_snapshot = receiver.snapshots["FAST"]
        full_energy = full_snapshot.telemetry.values["pvenergytotal"]
        reader.calls.clear()
        power_address = 3001 if profile.startswith(("min-", "tl3-")) else 1
        _u32(words, power_address, 10000)
        assert await receiver.poll_power_once()
        assert receiver.snapshots["FAST"] is full_snapshot
        assert receiver.snapshots["FAST"].telemetry.values["pvenergytotal"] == full_energy
        assert receiver.measurements == 1 and receiver.fast_power_polls == 1
        assert readings[-1].partial
        assert "pvenergytotal" not in readings[-1].snapshot.telemetry.values
        assert readings[-1].snapshot.telemetry.values["pvpowerin"] == 10000
        assert all(kind == "input" for kind, _, _ in reader.calls)
        assert not any(first >= 3047 or first in {26, 28, 53, 55} for _, first, _ in reader.calls)
        await receiver.close()

    asyncio.run(scenario())


def test_fast_power_failure_never_replaces_full_reading():
    class Reader:
        def __init__(self, words):
            self.words = words
            self.fail = False

        async def read(self, kind, first, count):
            if self.fail:
                raise ReadError("TimeoutError")
            return [self.words[first + index] for index in range(count)]

    async def scenario():
        receiver = ModbusReceiver(
            host="127.0.0.1",
            identity="FAST",
            profile="mic-0-v314",
            fast_power_interval=5,
        )
        reader = Reader(_registers("mic-0-v314"))
        receiver.reader = reader
        seen = []
        receiver.subscribe(seen.append)
        assert await receiver.poll_once()
        original = receiver.snapshots["FAST"]
        reader.fail = True
        assert not await receiver.poll_power_once()
        assert receiver.connected
        assert receiver.failed_fast_power_polls == 1
        assert receiver.snapshots["FAST"] is original and len(seen) == 1
        await receiver.close()

    asyncio.run(scenario())


def test_fast_and_full_polls_share_one_reader_lock():
    class Reader:
        def __init__(self):
            self.words = _registers("mic-0-v314")
            self.active = 0
            self.peak = 0

        async def read(self, kind, first, count):
            self.active += 1
            self.peak = max(self.peak, self.active)
            await asyncio.sleep(0.001)
            values = [self.words[first + index] for index in range(count)]
            self.active -= 1
            return values

    async def scenario():
        receiver = ModbusReceiver(
            host="127.0.0.1",
            identity="FAST",
            profile="mic-0-v314",
            fast_power_interval=5,
        )
        reader = Reader()
        receiver.reader = reader
        assert await receiver.poll_once()
        assert all(await asyncio.gather(receiver.poll_once(), receiver.poll_power_once()))
        assert reader.peak == 1
        await receiver.close()

    asyncio.run(scenario())


def test_auto_poll_uses_read_only_dtc_and_publishes_resolved_profile():
    async def scenario():
        values = _registers("min-three-string-v124")
        requests = []

        async def reply(reader, writer):
            request = await reader.readexactly(12)
            kind = request[7]
            first = int.from_bytes(request[8:10], "big")
            count = int.from_bytes(request[10:12], "big")
            requests.append((kind, first, count))
            if kind == 3:
                assert first == 30000 and count == 1
                payload = b"\x03\x02" + (5201).to_bytes(2, "big")
                writer.write(
                    request[:4] + (len(payload) + 1).to_bytes(2, "big") + request[6:7] + payload
                )
            else:
                writer.write(_response(request, values))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            receiver = ModbusReceiver(
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
                identity="TEST_AUTO",
                profile=AUTO_PROFILE,
                delay=0.5,
            )
            assert await receiver.poll_once()
            assert receiver.detected_profile == "min-3000-v124"
            assert receiver.snapshots["TEST_AUTO"].telemetry.profile == "modbus-min-3000-v124"
            assert "pv3watt" not in receiver.snapshots["TEST_AUTO"].telemetry.values
            assert requests == [
                (3, 30000, 1),
                (4, 3003, 1),
                (4, 3000, 30),
                (4, 3047, 32),
            ]
            assert await receiver.poll_once()
            assert requests[4:] == requests[2:4]  # Detection runs once per receiver.
            await receiver.close()

    asyncio.run(scenario())


def test_small_gateway_blocks_reassemble_without_partial_publication():
    async def scenario():
        values = _registers("tl3-three-phase-v139")
        requests = []

        async def reply(reader, writer):
            request = await reader.readexactly(12)
            count = int.from_bytes(request[10:12], "big")
            requests.append(count)
            if count > 16:
                writer.write(request[:4] + b"\0\x03" + request[6:7] + b"\x84\x02")
            else:
                writer.write(_response(request, values))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            receiver = ModbusReceiver(
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
                identity="TEST_TL3",
                profile="tl3-three-phase-v139",
                block_words=16,
                delay=0.5,
            )
            assert await receiver.poll_once()
            assert receiver.snapshots["TEST_TL3"].telemetry.values["pvgridvoltage3"] == 2320
            assert requests == [16, 14, 11, 16, 16, 16, 7]
            assert all(count <= 16 for count in requests)
            await receiver.close()

    asyncio.run(scenario())


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
            if profile in {"min-three-string-v124", "tl3-three-phase-v139"}:
                assert telemetry.values["pv3watt"] == 14760
                assert (
                    sensor_value(
                        sensors["pv3watt"], telemetry.values, datetime.now(UTC), telemetry.profile
                    )
                    == 1476
                )
                assert (
                    sensor_value(
                        sensors["epv3total"], telemetry.values, datetime.now(UTC), telemetry.profile
                    )
                    == 1235
                )
                assert sensors["epv3total"].state_class == "total_increasing"
                assert (
                    sensor_value(
                        sensors["pvboosttemperature"],
                        telemetry.values,
                        datetime.now(UTC),
                        telemetry.profile,
                    )
                    == 27.4
                )
                assert sensors["pvfaultcode"].entity_category == "diagnostic"
                if profile == "tl3-three-phase-v139":
                    assert telemetry.values["pvgridvoltage2"] == 2310
                    assert telemetry.values["pvgridapparentpower3"] == 11500
                    assert sensors["pvgridapparentpower3"].unit == "VA"
                    assert "pvgridpower3" not in sensors
                    assert (
                        sensor_value(
                            sensors["pvgridlinevoltage_rs"],
                            telemetry.values,
                            datetime.now(UTC),
                            telemetry.profile,
                        )
                        == 401
                    )
            else:
                assert "pv3watt" not in sensors
            expected_blocks = (*PROFILES[profile], *OPTIONAL_BLOCKS.get(profile, ()))
            assert requests == [(4, first, count) for first, count in expected_blocks]
            assert all(count <= 32 for _function, _first, count in requests)
            await receiver.close()

    asyncio.run(scenario())


def test_v139_off_grid_display_state_is_a_valid_reading():
    words = _registers("tl3-three-phase-v139")
    words[3000] = 0x0502  # Mode 5, off-grid display state 2.
    assert decode_input_registers("tl3-three-phase-v139", words)["pvstatus"] == 2


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


def test_unavailable_optional_min_diagnostics_do_not_lose_core_readings():
    async def scenario():
        values = _registers("min-three-string-v124")

        async def reply(reader, writer):
            request = await reader.readexactly(12)
            first = int.from_bytes(request[8:10], "big")
            if first == 3086:
                writer.write(request[:4] + b"\0\x03" + request[6:7] + b"\x84\x02")
            else:
                writer.write(_response(request, values))
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            receiver = ModbusReceiver(
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
                identity="TEST_MIN_THREE",
                profile="min-three-string-v124",
            )
            assert await receiver.poll_once()
            telemetry = receiver.snapshots["TEST_MIN_THREE"].telemetry
            assert telemetry.values["pv3watt"] == 14760
            assert "pvboosttemperature" not in telemetry.values
            assert receiver.connected and receiver.failed_measurements == 0
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


@pytest.mark.parametrize("kind,function", [("input", 4), ("holding", 3)])
def test_raw_investigation_reads_only_selected_block_without_saving_values(
    tmp_path, kind, function
):
    async def scenario():
        requests = []

        async def reply(reader, writer):
            request = await reader.readexactly(12)
            requests.append(request)
            first = int.from_bytes(request[8:10], "big")
            assert request[7] == function and first == 400 and request[10:12] == b"\0\x03"
            payload = bytes((function, 6)) + b"\0\x07\0\0\xff\xfe"
            writer.write(
                request[:4] + (len(payload) + 1).to_bytes(2, "big") + request[6:7] + payload
            )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            cache = tmp_path / "raw.json"
            receiver = ModbusReceiver(
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
                identity="TEST_RAW",
                profile="investigate-raw",
                investigation_kind=kind,
                investigation_start=400,
                investigation_count=3,
                state_path=str(cache),
            )
            assert await receiver.poll_once()
            assert len(requests) == 1 and receiver.connected
            telemetry = receiver.snapshots["TEST_RAW"].telemetry
            assert telemetry.values == {
                f"raw_{kind}_400": 7,
                f"raw_{kind}_401": 0,
                f"raw_{kind}_402": 65534,
            }
            sensors = sensors_for(
                wire_profile=telemetry.profile, sensor_metadata=telemetry.sensor_metadata
            )
            raw = [sensor for sensor in sensors if sensor.key.startswith("raw_")]
            assert len(raw) == 3
            assert all(
                sensor.entity_category == "diagnostic" and sensor.unit is None for sensor in raw
            )
            assert not cache.exists()
            await receiver.close()

    asyncio.run(scenario())


def test_raw_investigation_honours_gateway_block_limit_and_is_atomic():
    async def scenario():
        requests = []

        async def reply(reader, writer):
            request = await reader.readexactly(12)
            first = int.from_bytes(request[8:10], "big")
            count = int.from_bytes(request[10:12], "big")
            requests.append((first, count))
            if first == 404:
                writer.write(request[:4] + b"\0\x03" + request[6:7] + b"\x84\x02")
            else:
                payload = bytes((4, count * 2)) + b"\0\x07" * count
                writer.write(
                    request[:4] + (len(payload) + 1).to_bytes(2, "big") + request[6:7] + payload
                )
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(reply, "127.0.0.1", 0)
        async with server:
            receiver = ModbusReceiver(
                host="127.0.0.1",
                port=server.sockets[0].getsockname()[1],
                identity="TEST_RAW",
                profile="investigate-raw",
                investigation_start=400,
                investigation_count=9,
                block_words=4,
                delay=0.5,
            )
            assert not await receiver.poll_once()
            assert requests == [(400, 4), (404, 4)]
            assert receiver.snapshots == {}
            await receiver.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("start,count", [(-1, 1), (65535, 2), (0, 33)])
def test_raw_investigation_rejects_out_of_range_blocks(start, count):
    with pytest.raises(ValueError, match="read-only block"):
        ModbusReceiver(
            host="localhost",
            identity="TEST_RAW",
            profile="investigate-raw",
            investigation_start=start,
            investigation_count=count,
        )
