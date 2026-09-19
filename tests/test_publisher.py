import asyncio
import json
from types import SimpleNamespace

import pytest

from ha_growatt.publisher import MqttSettings, Publisher
from ha_growatt.telemetry import Telemetry


class BrokerClient:
    def __init__(self, *args, **kwargs):
        self.messages = []
        self.subscriptions = []
        self.acknowledge = True
        self.result = 0
        self.stopped = False

    def reconnect_delay_set(self, **kwargs):
        pass

    def max_queued_messages_set(self, *args):
        pass

    def username_pw_set(self, *args):
        self.credentials = args

    def tls_set(self):
        self.tls = True

    def connect_async(self, *args, **kwargs):
        self.on_connect(self, None, None, SimpleNamespace(is_failure=False), None)

    def loop_start(self):
        pass

    def subscribe(self, topic, qos):
        self.subscriptions.append(topic)

    def publish(self, topic, payload, qos, retain):
        self.messages.append((topic, payload, qos, retain))
        return SimpleNamespace(rc=self.result, is_published=lambda: self.acknowledge)

    def disconnect(self):
        self.on_disconnect(self, None, None, None, None)

    def loop_stop(self):
        self.stopped = True


@pytest.fixture
def broker(monkeypatch):
    client = BrokerClient()
    monkeypatch.setattr("ha_growatt.publisher.mqtt.Client", lambda *a, **k: client)
    return client


def packet():
    return Telemetry({"pvserial": "INVERT0001", "pvpowerout": 1000}, None, "classic-6")


def test_discovery_before_state_and_reannouncement_after_birth_and_reconnect(broker):
    async def scenario():
        publisher = Publisher(MqttSettings("broker.invalid"))
        publisher.start()
        await publisher.publish(packet())
        assert len(broker.messages) == 33
        assert all(m[2:] == (1, True) for m in broker.messages[:32])
        assert broker.messages[-1][2:] == (1, False)
        assert json.loads(broker.messages[-1][1])["pvpowerout"] == 1000
        await publisher.publish(packet())
        assert len(broker.messages) == 34
        broker.on_message(
            broker, None, SimpleNamespace(topic="homeassistant/status", payload=b"online")
        )
        await publisher.publish(packet())
        assert len(broker.messages) == 67
        broker.disconnect()
        with pytest.raises(ConnectionError):
            await publisher.publish(packet())
        broker.connect_async()
        await publisher.publish(packet())
        assert len(broker.messages) == 100
        assert set(broker.subscriptions) == {"homeassistant/status"}
        await publisher.close()
        assert broker.stopped

    asyncio.run(scenario())


def test_failed_discovery_is_retried_without_claiming_it_was_announced(broker):
    async def scenario():
        publisher = Publisher(MqttSettings("broker.invalid", delivery_seconds=0.02))
        publisher.start()
        broker.acknowledge = False
        with pytest.raises(TimeoutError):
            await publisher.publish(packet())
        assert len(broker.messages) == 1
        broker.acknowledge = True
        await publisher.publish(packet())
        assert len(broker.messages) == 34
        await publisher.close()

    asyncio.run(scenario())


def test_broker_failure_and_password_redaction(broker):
    async def scenario():
        settings = MqttSettings("broker.invalid", username="reader", password="test-only", tls=True)
        assert "test-only" not in repr(settings)
        publisher = Publisher(settings)
        publisher.start()
        broker.result = 4
        with pytest.raises(ConnectionError):
            await publisher.publish(packet())
        assert broker.tls
        await publisher.close()

    asyncio.run(scenario())


def test_generic_packet_cleans_known_mod_extras_but_preserves_other_devices(broker):
    async def scenario():
        publisher = Publisher(MqttSettings("broker.invalid"))
        publisher.start()
        await publisher.publish(Telemetry(packet().values, None, "extended-6"))
        configs = [m for m in broker.messages if m[0].endswith("/config") and m[1]]
        tombstones = [m for m in broker.messages if m[1] == ""]
        assert len(configs) == 32
        assert any("_raw_pvpowerout_r3019/config" in m[0] for m in tombstones)
        assert all(m[2:] == (1, True) for m in tombstones)
        assert all("/INVERT0001_" in m[0] for m in tombstones)
        assert not any("_pvpowerout/config" in m[0] for m in tombstones)
        count = len(broker.messages)
        await publisher.publish(Telemetry(packet().values, None, "extended-6"))
        assert len(broker.messages) == count + 1
        await publisher.close()

    asyncio.run(scenario())


def test_cleanup_failure_keeps_state_delivery_and_retries_pending_topics(broker):
    async def scenario():
        publisher = Publisher(MqttSettings("broker.invalid"))
        publisher.start()
        send = publisher._send
        rejected = []

        async def fail_cleanup(topic, payload, retain):
            if payload == "":
                rejected.append(topic)
                raise TimeoutError
            await send(topic, payload, retain)

        publisher._send = fail_cleanup
        telemetry = Telemetry(packet().values, None, "mod-6")
        await publisher.publish(telemetry)
        assert len(rejected) == 1
        assert broker.messages[-1][0].endswith("/state")
        publisher._send = send
        await publisher.publish(telemetry)
        assert any(m[0] == rejected[0] and m[1] == "" for m in broker.messages)
        assert broker.messages[-1][0].endswith("/state")
        await publisher.close()

    asyncio.run(scenario())
