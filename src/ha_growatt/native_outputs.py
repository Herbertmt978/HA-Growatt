"""Optional destinations for readings received inside Home Assistant.

Delivery never holds up the datalogger listener. Each destination has its own
bounded queue, and a failure in one destination cannot stop another.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .outputs import (
    HttpWrite,
    InfluxOutput,
    InfluxSettings,
    PublicationPolicy,
    PVOutput,
    PVOutputSettings,
    RawMqttSettings,
    RawPublisher,
    _endpoint,
    send_http,
)
from .telemetry import Telemetry

_LOG = logging.getLogger(__name__)

type Deliver = Callable[[dict, str], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class NativeOutputSettings:
    """Choose destinations without exposing their credentials in repr or status."""

    raw_mqtt: RawMqttSettings | None = field(default=None, repr=False)
    pvoutput: PVOutputSettings | None = field(default=None, repr=False)
    influx: InfluxSettings | None = field(default=None, repr=False)
    http_endpoint: str | None = field(default=None, repr=False)
    policy: PublicationPolicy = PublicationPolicy("auto", False)
    queue_size: int = 32
    shutdown_seconds: float = 30

    def __post_init__(self) -> None:
        if self.http_endpoint is not None:
            _endpoint(self.http_endpoint)
        if type(self.queue_size) is not int or not 1 <= self.queue_size <= 1024:
            raise ValueError("Output queue size must be between 1 and 1024")
        if (
            type(self.shutdown_seconds) not in {int, float}
            or not math.isfinite(self.shutdown_seconds)
            or self.shutdown_seconds <= 0
        ):
            raise ValueError("Output shutdown deadline must be positive")


@dataclass(slots=True)
class _Destination:
    deliver: Deliver
    queue: asyncio.Queue[tuple[dict, str]] | None = None
    task: asyncio.Task[None] | None = None
    enabled: bool = True
    failures: int = 0
    dropped: int = 0


class NativeOutputs:
    """Publish live telemetry to optional app-compatible destinations.

    Call ``start`` on the Home Assistant event loop, then ``await publish`` for
    decoded telemetry. ``publish`` only enqueues and does not wait for a broker
    or HTTP endpoint. Buffered uploads are skipped unless explicitly enabled
    in the publication policy, and even then retain their recorded timestamp
    and buffered marker.
    """

    def __init__(self, settings: NativeOutputSettings) -> None:
        self.settings = settings
        self._destinations: dict[str, _Destination] = {}
        self._raw: RawPublisher | None = None
        self._started = False
        self._closed = False
        self.rejected = 0
        if settings.raw_mqtt is not None:
            raw = self._raw = RawPublisher(settings.raw_mqtt)

            async def deliver_mqtt(message: dict, profile: str) -> None:
                # The native receiver provides Telemetry rather than its Frame.
                # Meter profiles are sufficient to preserve the app's topic choice.
                await raw.publish_message(message, meter=profile.startswith("meter-"))

            self._destinations["raw_mqtt"] = _Destination(deliver_mqtt)
        if settings.pvoutput is not None:
            pv = PVOutput(settings.pvoutput)

            async def deliver_pvoutput(message: dict, _profile: str) -> None:
                for request in pv.requests(message):
                    await asyncio.to_thread(send_http, request)

            self._destinations["pvoutput"] = _Destination(deliver_pvoutput)
        if settings.influx is not None:
            influx = InfluxOutput(settings.influx, settings.policy.timezone)

            async def deliver_influx(message: dict, _profile: str) -> None:
                await asyncio.to_thread(influx.publish, message)

            self._destinations["influx"] = _Destination(deliver_influx)
        if settings.http_endpoint is not None:
            endpoint = settings.http_endpoint

            async def deliver_http(message: dict, _profile: str) -> None:
                # Match the app's HTTP extension, which posts a JSON string
                # containing the JSON reading rather than a JSON object.
                encoded = json.dumps(message, allow_nan=False)
                write = HttpWrite(
                    endpoint,
                    json.dumps(encoded).encode(),
                    {"Content-Type": "application/json"},
                )
                await asyncio.to_thread(send_http, write)

            self._destinations["http"] = _Destination(deliver_http)

    def start(self) -> None:
        if self._started or self._closed:
            raise RuntimeError("Native outputs cannot be started again")
        asyncio.get_running_loop()
        self._started = True
        if self._raw is not None:
            try:
                self._raw.start()
            except Exception:
                self._destinations["raw_mqtt"].failures += 1
                self._destinations["raw_mqtt"].enabled = False
                _LOG.warning("Native raw MQTT output could not start")
        for name, destination in self._destinations.items():
            if not destination.enabled:
                continue
            destination.queue = asyncio.Queue(maxsize=self.settings.queue_size)
            destination.task = asyncio.create_task(
                self._consume(name, destination), name=f"ha-growatt-native-{name}"
            )

    async def publish(self, telemetry: Telemetry) -> None:
        if not self._started or self._closed:
            raise RuntimeError("Native outputs are not running")
        try:
            message = self.settings.policy.message(telemetry)
        except (TypeError, ValueError):
            self.rejected += 1
            return
        if message is None:
            return
        for destination in self._destinations.values():
            if not destination.enabled:
                continue
            queue = destination.queue
            if queue is None:
                continue
            if queue.full():
                queue.get_nowait()
                queue.task_done()
                destination.dropped += 1
            queue.put_nowait((message, telemetry.profile))

    async def _consume(self, name: str, destination: _Destination) -> None:
        queue = destination.queue
        assert queue is not None
        while True:
            message, profile = await queue.get()
            try:
                await destination.deliver(message, profile)
            except Exception:
                destination.failures += 1
                _LOG.warning("Native %s output delivery failed", name)
            finally:
                queue.task_done()

    def status(self) -> dict[str, dict[str, int]]:
        """Return counts only; never include endpoints, identities or credentials."""
        return {
            name: {
                "queued": destination.queue.qsize() if destination.queue else 0,
                "failures": destination.failures,
                "dropped": destination.dropped,
            }
            for name, destination in self._destinations.items()
        }

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._started:
                try:
                    async with asyncio.timeout(self.settings.shutdown_seconds):
                        await asyncio.gather(
                            *(
                                destination.queue.join()
                                for destination in self._destinations.values()
                                if destination.queue is not None
                            )
                        )
                except TimeoutError:
                    _LOG.warning("Native output shutdown deadline reached")
        finally:
            for destination in self._destinations.values():
                if destination.task is not None:
                    destination.task.cancel()
            await asyncio.gather(
                *(
                    destination.task
                    for destination in self._destinations.values()
                    if destination.task is not None
                ),
                return_exceptions=True,
            )
            if self._raw is not None:
                try:
                    await self._raw.close()
                except Exception:
                    _LOG.warning("Native raw MQTT output could not close cleanly")
            self._started = False
