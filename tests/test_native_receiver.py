"""Contract tests for the receiver used by the one-part HA installation."""

import asyncio
import json
import socket
from datetime import UTC, datetime
from pathlib import Path

import pytest
from jinja2 import Environment

from ha_growatt.discovery import discovery_messages, sensor_value, sensors_for
from ha_growatt.native_receiver import NativeReceiver
from ha_growatt.protocol import Frame
from ha_growatt.recovery import ReadingStore, Snapshot
from ha_growatt.selection import FamilyDecoder, SelectionSettings


def frame():
    cases = json.loads((Path(__file__).parent / "fixtures/telemetry_cases.json").read_text())
    case = next(
        item for item in cases if item["profile"] == "classic-6" and not item["include_all"]
    )
    return Frame.from_bytes(bytes.fromhex(case["wire"]))


def test_native_receiver_restores_overnight_values_without_presenting_them_as_live(
    tmp_path, monkeypatch
):
    class Transport:
        def __init__(self, settings, observer, **kwargs):
            self.running = False

        async def __aenter__(self):
            self.running = True
            return self

        async def __aexit__(self, *_exc):
            self.running = False

    monkeypatch.setattr("ha_growatt.native_receiver.Relay", Transport)
    source = frame()
    telemetry = FamilyDecoder(SelectionSettings()).decode(source)
    stamp = datetime(2026, 9, 19, 12, tzinfo=UTC)
    path = tmp_path / "readings.json"
    ReadingStore(str(path), ("native", 5279, True, "default")).save(
        {"INVERT0001": Snapshot(telemetry, stamp)}
    )

    async def scenario():
        receiver = NativeReceiver(state_path=str(path))
        await receiver.start()
        assert receiver.running
        restored = []
        unsubscribe = receiver.subscribe(restored.append)
        assert [(item.identity, item.restored, item.snapshot.received_at) for item in restored] == [
            ("INVERT0001", True, stamp)
        ]
        await receiver.observe("cloud", source)
        assert len(restored) == 1
        await receiver.observe("device", source)
        assert len(restored) == 2
        assert restored[-1].restored is False
        assert restored[-1].snapshot.telemetry.values["pvpowerout"] == 4
        assert receiver.measurements == 1
        assert (
            ReadingStore(str(path), ("native", 5279, True, "default"))
            .load()["INVERT0001"]
            .received_at
            == restored[-1].snapshot.received_at
        )
        unsubscribe()
        await receiver.observe("device", source)
        assert len(restored) == 2
        await receiver.close()
        assert not receiver.running

    asyncio.run(scenario())


def test_family_change_does_not_restore_old_layout_readings(tmp_path, monkeypatch):
    class Transport:
        running = True

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            pass

    monkeypatch.setattr("ha_growatt.native_receiver.Relay", Transport)
    source = frame()
    telemetry = FamilyDecoder(SelectionSettings()).decode(source)
    path = tmp_path / "readings.json"
    ReadingStore(str(path), ("native", 5279, True, "default")).save(
        {"INVERT0001": Snapshot(telemetry, datetime(2026, 9, 19, 12, tzinfo=UTC))}
    )

    async def scenario():
        receiver = NativeReceiver(state_path=str(path), family="min")
        await receiver.start()
        assert receiver.snapshots == {}
        await receiver.close()

    asyncio.run(scenario())


def test_failing_subscriber_does_not_block_other_readings():
    async def scenario():
        receiver = NativeReceiver()
        delivered = []
        receiver.subscribe(delivered.append)

        def fail(_reading):
            raise RuntimeError("Private subscriber detail")

        receiver.subscribe(fail)
        await receiver.observe("device", frame())
        assert len(delivered) == 1
        assert receiver.measurements == 1

    asyncio.run(scenario())


def test_native_receiver_accepts_a_real_local_tcp_measurement():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    async def scenario():
        receiver = NativeReceiver(port=port, forward_cloud=False)
        arrived = asyncio.Event()
        readings = []

        def receive(reading):
            readings.append(reading)
            arrived.set()

        receiver.subscribe(receive)
        await receiver.start()
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                writer.write(frame().to_bytes())
                await writer.drain()
                await asyncio.wait_for(arrived.wait(), 3)
                assert readings[0].identity == "INVERT0001"
                assert receiver.snapshots["INVERT0001"].telemetry.values["pvpowerout"] == 4
            finally:
                writer.close()
                await writer.wait_closed()
        finally:
            await receiver.close()

    asyncio.run(scenario())


def test_native_receiver_separates_buffered_and_failed_frames():
    async def scenario():
        receiver = NativeReceiver()
        live = []
        buffered = []
        receiver.subscribe(live.append)
        receiver.subscribe_buffered(buffered.append)
        source = frame()
        await receiver.observe("device", source)
        first = receiver.snapshots["INVERT0001"]
        delayed = Frame(source.transaction, source.protocol, source.unit, 80, source.payload)
        await receiver.observe("device", delayed)
        assert len(buffered) == 1
        assert buffered[0].buffered
        assert len(live) == 1
        assert receiver.snapshots["INVERT0001"] is first
        assert receiver.buffered_records == 1
        assert receiver.measurements == 1

        payload = bytearray(source.payload)
        payload[30:40] = b"bad/topic!"
        await receiver.observe(
            "device", Frame(source.transaction, source.protocol, source.unit, 4, bytes(payload))
        )
        assert len(live) == 1
        assert receiver.failed_measurements == 1
        await receiver.observe(
            "device", Frame(source.transaction, source.protocol, source.unit, 3, bytes(payload))
        )
        assert receiver.announcements == 1
        assert receiver.announcement_warnings == 1
        assert receiver.failed_measurements == 1
        await receiver.observe("device", Frame(1, 6, 1, 4, b"short"))
        await receiver.observe("device", Frame(1, 6, 1, 2, source.payload))
        assert receiver.failed_measurements == 1
        assert len(live) == 1

    asyncio.run(scenario())


def test_live_packet_before_platform_subscription_is_replayed_as_live():
    async def scenario():
        receiver = NativeReceiver()
        await receiver.observe("device", frame())
        replayed = []
        receiver.subscribe(replayed.append)
        assert len(replayed) == 1
        assert replayed[0].identity == "INVERT0001"
        assert replayed[0].restored is False

    asyncio.run(scenario())


def test_optional_output_receives_live_and_buffered_without_blocking_readings():
    async def scenario():
        seen = []

        async def output(telemetry):
            seen.append(telemetry.buffered)
            raise ConnectionError("Optional destination unavailable")

        receiver = NativeReceiver(on_telemetry=output)
        live = []
        buffered = []
        receiver.subscribe(live.append)
        receiver.subscribe_buffered(buffered.append)
        source = frame()
        await receiver.observe("device", source)
        await receiver.observe(
            "device", Frame(source.transaction, source.protocol, source.unit, 80, source.payload)
        )
        assert seen == [False, True]
        assert len(live) == len(buffered) == 1
        assert receiver.measurements == receiver.buffered_records == 1

    asyncio.run(scenario())


def test_native_receiver_can_share_unknown_frame_evidence_without_packet_contents():
    async def scenario():
        receiver = NativeReceiver()
        receiver.private_capture.start()
        source = frame()
        payload = bytearray(source.payload)
        payload[30:40] = b"bad/topic!"
        unknown = Frame(source.transaction, source.protocol, source.unit, 4, bytes(payload))
        await receiver.observe("device", unknown)
        report = receiver.private_capture.export_shareable(receiver.decoder)
        assert report["records"][0]["result"] == "decode_failed"
        assert report["undecoded_layouts"][0]["frames"] == 1
        assert report["undecoded_layouts"][0]["candidate_profiles"] == []
        assert report["undecoded_layouts"][0]["changing_words"] == []
        encoded = json.dumps(report)
        assert "bad/topic!" not in encoded
        assert source.payload[:10].decode() not in encoded
        assert source.to_bytes().hex() not in encoded
        await receiver.close()
        assert receiver.private_capture.export()["frames"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize("wire_profile", ["classic-6", "mod-6"])
def test_native_sensor_values_match_existing_mqtt_discovery_templates(wire_profile):
    fixture = Path(__file__).parent / "fixtures/telemetry_cases.json"
    cases = json.loads(fixture.read_text())
    case = next(
        item for item in cases if item["profile"] == wire_profile and not item["include_all"]
    )
    values = case["expected"]
    received_at = datetime(2026, 9, 19, 12, tzinfo=UTC)
    sensors = sensors_for(wire_profile=wire_profile)
    configurations = discovery_messages("INVERT0001", wire_profile=wire_profile)
    for sensor in sensors:
        actual = sensor_value(sensor, values, received_at, wire_profile)
        if sensor.key == "grott_last_push":
            assert actual == received_at
            continue
        if (
            sensor.source
            or sensor.key in values
            or sensor.key in {"pvpowerout", "pvfrequentie", "pvipmtemperature"}
        ):
            config = configurations[f"homeassistant/sensor/grott/INVERT0001_{sensor.key}/config"]
            rendered = Environment().from_string(config["value_template"]).render(value_json=values)
            assert str(actual) == rendered, sensor.key
