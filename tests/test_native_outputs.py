"""Output delivery in the Home Assistant-only receiver."""

import asyncio
import json
import logging
from datetime import datetime

import pytest

from ha_growatt import native_outputs
from ha_growatt.native_outputs import NativeOutputs, NativeOutputSettings
from ha_growatt.outputs import (
    InfluxSettings,
    PublicationPolicy,
    PVOutputSettings,
    RawMqttSettings,
)
from ha_growatt.publisher import MqttSettings
from ha_growatt.telemetry import Telemetry


def _reading(*, buffered=False, profile="classic-6", stamp=None):
    return Telemetry(
        {
            "pvserial": "INVERT0001",
            "pvpowerout": 1200,
            "pvgridvoltage": 2300,
            "pvenergytoday": 20,
        },
        stamp or datetime(2026, 9, 23, 10, 30),
        profile,
        buffered,
    )


def test_existing_output_formats_are_reused_without_adding_private_fields(monkeypatch):
    async def scenario():
        mqtt = []
        requests = []
        influx = []

        class Raw:
            def __init__(self, settings):
                self.settings = settings

            def start(self):
                pass

            async def publish_message(self, message, *, meter=False):
                mqtt.append((message, meter))

            async def close(self):
                pass

        monkeypatch.setattr(native_outputs, "RawPublisher", Raw)
        monkeypatch.setattr(native_outputs, "send_http", requests.append)
        monkeypatch.setattr(
            native_outputs.InfluxOutput, "publish", lambda self, message: influx.append(message)
        )
        outputs = NativeOutputs(
            NativeOutputSettings(
                raw_mqtt=RawMqttSettings(MqttSettings("broker.invalid")),
                pvoutput=PVOutputSettings("synthetic-key", default_system="12345"),
                influx=InfluxSettings(version=2, token="synthetic-token"),
                http_endpoint="http://collector.invalid/reading",
                policy=PublicationPolicy("auto", False),
            )
        )
        outputs.start()
        await outputs.publish(_reading())
        await outputs.close()

        assert mqtt == [
            (
                {
                    "device": "INVERT0001",
                    "time": "2026-09-23T10:30:00",
                    "buffered": "no",
                    "values": _reading().values,
                },
                False,
            )
        ]
        assert influx == [mqtt[0][0]]
        assert len(requests) == 2
        pv = next(request for request in requests if "pvoutput.org" in request.url)
        assert pv.headers["X-Pvoutput-Apikey"] == "synthetic-key"
        assert pv.headers["X-Pvoutput-SystemId"] == "12345"
        http = next(request for request in requests if "collector.invalid" in request.url)
        assert json.loads(json.loads(http.body)) == mqtt[0][0]
        assert http.headers == {"Content-Type": "application/json"}
        assert outputs.status() == {
            name: {"queued": 0, "failures": 0, "dropped": 0}
            for name in ("raw_mqtt", "pvoutput", "influx", "http")
        }

    asyncio.run(scenario())


def test_buffered_uploads_require_opt_in_and_a_recorded_timestamp(monkeypatch):
    async def scenario():
        observed = []

        class Raw:
            def __init__(self, _settings):
                pass

            def start(self):
                pass

            async def publish_message(self, message, *, meter=False):
                observed.append((message, meter))

            async def close(self):
                pass

        monkeypatch.setattr(native_outputs, "RawPublisher", Raw)
        settings = RawMqttSettings(MqttSettings("broker.invalid"))
        default = NativeOutputs(NativeOutputSettings(raw_mqtt=settings))
        default.start()
        await default.publish(_reading(buffered=True))
        await default.close()
        assert observed == []

        enabled = NativeOutputs(
            NativeOutputSettings(
                raw_mqtt=settings,
                policy=PublicationPolicy("auto", True),
            )
        )
        enabled.start()
        await enabled.publish(_reading(buffered=True, stamp=datetime(2026, 9, 22, 10, 30)))
        await enabled.publish(Telemetry({"pvserial": "INVERT0001"}, None, "meter-6", buffered=True))
        await enabled.close()
        assert len(observed) == 1
        assert observed[0][0]["buffered"] == "yes"
        assert observed[0][0]["time"] == "2026-09-22T10:30:00"

    asyncio.run(scenario())


def test_one_slow_or_failing_output_cannot_hold_up_another(monkeypatch, caplog):
    async def scenario():
        monkeypatch.setattr(native_outputs, "send_http", lambda _request: None)
        outputs = NativeOutputs(
            NativeOutputSettings(
                pvoutput=PVOutputSettings("key", default_system="1", interval_minutes=0),
                http_endpoint="http://collector.invalid/reading",
                queue_size=1,
            )
        )
        entered, release = asyncio.Event(), asyncio.Event()
        delivered = []

        async def slow(_message, _profile):
            entered.set()
            await release.wait()
            raise OSError("synthetic credential must not be logged")

        async def working(message, _profile):
            delivered.append(message["device"])

        outputs._destinations["http"].deliver = slow
        outputs._destinations["pvoutput"].deliver = working
        outputs.start()
        try:
            await outputs.publish(_reading())
            await asyncio.wait_for(entered.wait(), 1)
            for _ in range(4):
                await outputs.publish(_reading())
                await asyncio.sleep(0)
            assert len(delivered) == 5
            assert outputs.status()["http"]["dropped"] == 3
        finally:
            release.set()
            await outputs.close()
        assert outputs.status()["http"]["failures"] == 2

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert "synthetic credential" not in caplog.text


def test_shutdown_is_bounded_and_logs_no_exception_details(caplog):
    async def scenario():
        outputs = NativeOutputs(
            NativeOutputSettings(
                http_endpoint="http://collector.invalid/secret-token",
                queue_size=1,
                shutdown_seconds=0.01,
            )
        )
        entered = asyncio.Event()

        async def stalled(_message, _profile):
            entered.set()
            await asyncio.Event().wait()

        outputs._destinations["http"].deliver = stalled
        outputs.start()
        await outputs.publish(_reading())
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(outputs.close(), 1)
        assert outputs._destinations["http"].task.done()
        assert "secret-token" not in repr(outputs.settings)
        assert "secret-token" not in json.dumps(outputs.status())

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert "Native output shutdown deadline reached" in caplog.text
    assert "secret-token" not in caplog.text


@pytest.mark.parametrize("size", [0, -1, 1025, True])
def test_queue_size_is_bounded(size):
    with pytest.raises(ValueError, match="queue size"):
        NativeOutputSettings(queue_size=size)


def test_http_endpoint_rejects_embedded_credentials():
    with pytest.raises(ValueError, match="credentials"):
        NativeOutputSettings(http_endpoint="https://user:secret@collector.invalid/reading")


def test_mqtt_start_failure_does_not_stop_other_destinations(monkeypatch):
    async def scenario():
        delivered = []

        class Raw:
            def __init__(self, _settings):
                pass

            def start(self):
                raise OSError("synthetic broker password")

            async def close(self):
                pass

        monkeypatch.setattr(native_outputs, "RawPublisher", Raw)
        outputs = NativeOutputs(
            NativeOutputSettings(
                raw_mqtt=RawMqttSettings(MqttSettings("broker.invalid")),
                http_endpoint="http://collector.invalid/reading",
            )
        )

        async def working(message, _profile):
            delivered.append(message)

        outputs._destinations["http"].deliver = working
        outputs.start()
        await outputs.publish(_reading())
        await outputs.close()
        assert len(delivered) == 1
        assert outputs.status()["raw_mqtt"] == {"queued": 0, "failures": 1, "dropped": 0}

    asyncio.run(scenario())


def test_meter_profile_uses_raw_mqtt_meter_topic(monkeypatch):
    async def scenario():
        published = []

        class Raw:
            def __init__(self, _settings):
                pass

            def start(self):
                pass

            async def publish_message(self, message, *, meter=False):
                published.append((message, meter))

            async def close(self):
                pass

        monkeypatch.setattr(native_outputs, "RawPublisher", Raw)
        outputs = NativeOutputs(
            NativeOutputSettings(raw_mqtt=RawMqttSettings(MqttSettings("broker.invalid")))
        )
        outputs.start()
        await outputs.publish(_reading(profile="meter-6"))
        await outputs.close()
        assert len(published) == 1
        assert published[0][1] is True
        assert "profile" not in published[0][0]

    asyncio.run(scenario())
