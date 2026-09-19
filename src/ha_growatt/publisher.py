"""MQTT publishing with reconnect discovery and bounded delivery waits."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime

import paho.mqtt.client as mqtt

from .discovery import discovery_messages, lineage_topics, state_message, state_topic
from .telemetry import Telemetry

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MqttSettings:
    host: str
    port: int = 1883
    username: str = ""
    password: str = field(default="", repr=False)
    tls: bool = False
    retain_state: bool = False
    delivery_seconds: float = 5
    entity_profile: str = "v0_1_9_standard"
    include_all: bool = False

    def __post_init__(self) -> None:
        if (
            not isinstance(self.host, str)
            or not self.host
            or type(self.port) is not int
            or not 1 <= self.port <= 65535
            or type(self.delivery_seconds) not in {int, float}
            or not math.isfinite(self.delivery_seconds)
            or self.delivery_seconds <= 0
        ):
            raise ValueError("Invalid MQTT endpoint or delivery deadline")
        if any(
            type(value) is not bool for value in (self.tls, self.retain_state, self.include_all)
        ):
            raise ValueError("MQTT switches must be booleans")
        if self.entity_profile not in {"v0_1_9_standard", "all"}:
            raise ValueError("Unknown Home Assistant entity profile")


class Publisher:
    """Own one broker connection; never receive or send device commands."""

    def __init__(self, settings: MqttSettings) -> None:
        self.settings = settings
        self._client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, protocol=mqtt.MQTTv311)
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._generation = 0
        self._announced: dict[str, tuple] = {}
        self._topics: dict[str, set[str]] = {}
        self._pending_cleanup: dict[str, set[str]] = {}
        self._publish_lock = asyncio.Lock()
        self._started = False
        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.reconnect_delay_set(min_delay=1, max_delay=30)
        self._client.max_queued_messages_set(256)
        if settings.username:
            self._client.username_pw_set(settings.username, settings.password)
        if settings.tls:
            self._client.tls_set()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            self._connected.clear()
            return
        with self._lock:
            self._generation += 1
        self._connected.set()
        client.subscribe("homeassistant/status", qos=1)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        self._connected.clear()

    def _on_message(self, client, userdata, message) -> None:
        if message.topic == "homeassistant/status" and message.payload == b"online":
            with self._lock:
                self._generation += 1

    def start(self) -> None:
        if self._started:
            raise RuntimeError("MQTT publisher is already running")
        self._started = True
        self._client.connect_async(self.settings.host, self.settings.port, keepalive=60)
        self._client.loop_start()

    async def _send(self, topic: str, payload: str, retain: bool) -> None:
        if not self._connected.is_set():
            raise ConnectionError("MQTT is not connected")
        receipt = self._client.publish(topic, payload, qos=1, retain=retain)
        if receipt.rc != mqtt.MQTT_ERR_SUCCESS:
            raise ConnectionError("MQTT rejected the publication")
        async with asyncio.timeout(self.settings.delivery_seconds):
            while not receipt.is_published():
                if not self._connected.is_set():
                    raise ConnectionError("MQTT disconnected during publication")
                await asyncio.sleep(0.01)

    async def publish(self, telemetry: Telemetry) -> None:
        identity = telemetry.values.get("pvserial")
        if not isinstance(identity, str):
            raise ValueError("Telemetry requires an inverter identity")
        async with self._publish_lock:
            with self._lock:
                generation = self._generation
            fingerprint = (generation, telemetry.profile)
            if self._announced.get(identity) != fingerprint:
                configs = discovery_messages(
                    identity,
                    profile=self.settings.entity_profile,
                    wire_profile=telemetry.profile,
                    include_all=self.settings.include_all,
                )
                for topic, config in configs.items():
                    await self._send(topic, json.dumps(config, separators=(",", ":")), True)
                previous = self._topics.get(identity, set())
                if telemetry.profile in {"mod-6", "extended-6"}:
                    previous = previous | lineage_topics(identity)
                pending = self._pending_cleanup.setdefault(identity, set())
                pending.update(previous - configs.keys())
                pending.difference_update(configs)
                self._topics[identity] = set(configs)
                self._announced[identity] = fingerprint
            pending = self._pending_cleanup.get(identity, set())
            for topic in sorted(pending):
                try:
                    await self._send(topic, "", True)
                except (ConnectionError, TimeoutError):
                    _LOG.warning("Discovery cleanup incomplete; retrying on the next packet")
                    break
                pending.remove(topic)
            payload = state_message(telemetry.values, datetime.now(UTC))
            await self._send(state_topic(identity), payload, self.settings.retain_state)

    async def close(self) -> None:
        self._client.disconnect()
        await asyncio.to_thread(self._client.loop_stop)
        self._connected.clear()
        self._started = False
