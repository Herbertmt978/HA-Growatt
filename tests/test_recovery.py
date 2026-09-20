import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_publisher import broker, packet  # noqa: F401

from ha_growatt.ha_features import HomeAssistantFeatures
from ha_growatt.publisher import MqttSettings, Publisher
from ha_growatt.recovery import ReadingStore, Snapshot


async def restored(client):
    for _ in range(100):
        states = [m for m in client.messages if m[0].endswith("/state")]
        if states:
            return states[-1]
        await asyncio.sleep(0.01)
    raise AssertionError("No restored reading")


def test_quiet_service_and_home_assistant_restart_keep_timestamp(tmp_path, broker):  # noqa: F811
    async def scenario():
        settings = MqttSettings("broker.invalid", state_path=str(tmp_path / "readings.json"))
        first = Publisher(settings)
        first.start()
        await first.publish(packet())
        original = first.snapshots["INVERT0001"].received_at
        await first.close()
        broker.messages.clear()
        second = Publisher(settings)
        features = HomeAssistantFeatures(second, None, None)
        assert features.devices["INVERT0001"].last_seen == float("-inf")
        assert features.devices["INVERT0001"].values == {}
        second.start()
        message = await restored(broker)
        assert json.loads(message[1])["grott_last_push"] == original.isoformat(timespec="seconds")
        broker.messages.clear()
        from types import SimpleNamespace

        broker.on_message(
            broker, None, SimpleNamespace(topic="homeassistant/status", payload=b"online")
        )
        assert json.loads((await restored(broker))[1])["pvpowerout"] == 1000
        await second.close()

    asyncio.run(scenario())


def test_cache_is_saved_during_broker_outage(tmp_path, broker):  # noqa: F811
    async def scenario():
        settings = MqttSettings(
            "broker.invalid", state_path=str(tmp_path / "readings.json"), delivery_seconds=0.01
        )
        publisher = Publisher(settings)
        with pytest.raises(ConnectionError):
            await publisher.publish(packet())
        assert Publisher(settings).snapshots["INVERT0001"].telemetry.values["pvpowerout"] == 1000
        await publisher.publish(replace(packet(), buffered=True))
        assert len(publisher.snapshots) == 1

    asyncio.run(scenario())


def test_scope_corruption_and_old_timestamp(tmp_path):
    path = tmp_path / "readings.json"
    store = ReadingStore(str(path), ("broker", 1883))
    old = datetime.now(UTC) - timedelta(days=1)
    store.save({"INVERT0001": Snapshot(packet(), old)})
    assert store.load()["INVERT0001"].received_at == old
    assert ReadingStore(str(path), ("other", 1883)).load() == {}
    path.write_text("broken")
    publisher = Publisher(MqttSettings("broker", state_path=str(path)))
    assert publisher.cache_error and publisher.snapshots == {}


def test_failed_atomic_save_preserves_previous_snapshot(tmp_path, monkeypatch):
    store = ReadingStore(str(tmp_path / "readings.json"), ())
    initial = {"INVERT0001": Snapshot(packet(), datetime.now(UTC))}
    store.save(initial)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr("ha_growatt.recovery.os.replace", fail)
    with pytest.raises(OSError):
        store.save({})
    assert store.load() == initial
    assert len(list(tmp_path.iterdir())) == 1


def test_clock_correction_does_not_discard_last_reading(tmp_path):
    store = ReadingStore(str(tmp_path / "readings.json"), ())
    before_clock_correction = datetime.now(UTC) + timedelta(minutes=5)
    store.save({"INVERT0001": Snapshot(packet(), before_clock_correction)})
    assert store.load()["INVERT0001"].received_at == before_clock_correction
