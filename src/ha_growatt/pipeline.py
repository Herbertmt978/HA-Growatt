"""Keep each optional output independent of forwarding and other destinations."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass

from .diagnostics import ObservationStats, SupportCapture
from .extensions import ExtensionProcess, layout_context
from .outputs import InfluxOutput, PVOutput, RawPublisher, send_http
from .protocol import Frame, ProtocolError, mask_payload
from .publisher import Publisher
from .settings import Settings
from .telemetry import Telemetry

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Reading:
    telemetry: Telemetry
    frame: Frame
    message: dict | None


class Pipeline:
    def __init__(self, settings: Settings, *, ha_publisher=Publisher) -> None:
        self.settings = settings
        self.decoder = settings.decoder()
        self._publishers = []
        self._outputs = {}
        self._workers = []
        self._queues = {}
        self.failures = 0
        self.dropped = 0
        self._extension = None
        self.ha = None
        self.features = None
        self.observations = ObservationStats()
        self.capture = SupportCapture()
        options = settings.runtime
        if options.home_assistant:
            ha = self.ha = ha_publisher(settings.mqtt)
            self._publishers.append(ha)

            async def home_assistant(reading):
                if not reading.telemetry.buffered:
                    await ha.publish(reading.telemetry)
                elif options.buffered_events:
                    telemetry = reading.telemetry
                    identity = telemetry.device_id or telemetry.values.get("pvserial")
                    if identity and telemetry.recorded_at:
                        await ha._send(
                            "ha_growatt/events/buffered",
                            json.dumps(
                                {
                                    "schema": 1,
                                    "inverter": identity,
                                    "recorded_at": telemetry.recorded_at.isoformat(),
                                    "profile": telemetry.profile,
                                    "values": {
                                        key: value
                                        for key, value in telemetry.values.items()
                                        if type(value) is int
                                    },
                                }
                            ),
                            False,
                        )

            self._outputs["Home Assistant"] = home_assistant
        if options.raw_mqtt:
            raw = RawPublisher(options.raw_mqtt)
            self._publishers.append(raw)

            async def mqtt(reading):
                if reading.message:
                    await raw.publish_message(
                        reading.message, meter=reading.frame.function in {27, 32}
                    )

            self._outputs["raw MQTT"] = mqtt
        if options.pvoutput:
            pv = PVOutput(options.pvoutput)

            async def pvoutput(reading):
                if reading.message:
                    for request in pv.requests(reading.message):
                        await asyncio.to_thread(send_http, request)

            self._outputs["PVOutput"] = pvoutput
        if options.influx:
            database = InfluxOutput(options.influx, options.policy.timezone)

            async def influx(reading):
                if reading.message:
                    await asyncio.to_thread(database.publish, reading.message)

            self._outputs["InfluxDB"] = influx
        if options.extension:
            extension = self._extension = ExtensionProcess(
                options.extension,
                options.extension_options,
                options.extension_context | {"verbose": options.verbose},
            )

            async def custom(reading):
                if reading.message:
                    frame = reading.frame
                    wire = frame.to_bytes()
                    packet = (
                        wire[:8]
                        + (mask_payload(wire[8:]) if frame.protocol in {5, 6} else wire[8:])
                    ).hex()
                    context = layout_context(self.decoder, reading.telemetry)
                    await asyncio.to_thread(extension.publish, reading.message, packet, context)

            self._outputs["extension"] = custom

    def bind_transport(self, transport) -> None:
        if self.ha is not None and self.settings.runtime.ha_features:
            from .ha_features import HomeAssistantFeatures
            from .relay import Relay

            if isinstance(transport, Relay):
                self.features = HomeAssistantFeatures(
                    self.ha,
                    transport,
                    self,
                    controls=self.settings.runtime.ha_controls,
                    experimental=self.settings.runtime.experimental_controls,
                    models=self.settings.runtime.control_models,
                    hardware=self.settings.runtime.hardware,
                    refresh_seconds=self.settings.runtime.settings_refresh_seconds,
                )

    def start(self) -> None:
        if self.features:
            self.features.start()
        for publisher in self._publishers:
            publisher.start()
        for name, output in self._outputs.items():
            queue = asyncio.Queue(self.settings.relay.queue_size)
            self._queues[name] = queue
            self._workers.append(asyncio.create_task(self._consume(name, queue, output)))

    async def _consume(self, name, queue, output) -> None:
        while True:
            reading = await queue.get()
            try:
                await output(reading)
            except Exception:
                self.failures += 1
                _LOG.warning("%s delivery failed", name)
            finally:
                queue.task_done()

    async def observe(self, direction: str, frame: Frame) -> None:
        if direction != "device":
            return
        if self.settings.runtime.diagnostic_logging:
            _LOG.info(
                "Packet protocol=%s record=%02x%02x bytes=%s raw=%s",
                frame.protocol,
                frame.unit,
                frame.function,
                len(frame.to_bytes()),
                frame.to_bytes().hex(),
            )
        if self.settings.runtime.trace:
            _LOG.info(
                "Record received: protocol=%s unit=%s function=%s bytes=%s",
                frame.protocol,
                frame.unit,
                frame.function,
                len(frame.to_bytes()),
            )
        if frame.function not in {3, 4, 27, 32, 80}:
            self.capture.record(frame, "protocol")
            return
        if len(frame.to_bytes()) < self.settings.runtime.minimum_record_bytes:
            self.capture.record(frame, "below_minimum_length")
            return
        if frame.function == 3:
            self.observations.announcements += 1
        try:
            telemetry = self.decoder.decode(frame)
        except ProtocolError:
            if frame.function == 3:
                self.observations.announcement_warnings += 1
                self.capture.record(frame, "announcement_not_measurement")
            else:
                self.observations.failed_measurements += 1
                self.capture.record(frame, "measurement_decode_failed")
            return
        if frame.function != 3:
            self.observations.measurements += 1
            self.observations.incomplete_fields += telemetry.decode_errors
        if telemetry.buffered:
            self.observations.buffered_records += 1
        self.capture.record(frame, "decoded", telemetry)
        if self.features and not telemetry.buffered and frame.function in {4, 80}:
            self.features.remember(telemetry)
        _LOG.debug(
            "Decoded %s fields using %s; incomplete fields=%s",
            len(telemetry.values),
            telemetry.profile,
            telemetry.decode_errors,
        )
        reading = Reading(telemetry, frame, self.settings.runtime.policy.message(telemetry))
        for queue in self._queues.values():
            if queue.full():
                queue.get_nowait()
                queue.task_done()
                self.dropped += 1
            queue.put_nowait(reading)

    async def close(self) -> None:
        if self.features:
            await self.features.close()
        try:
            async with asyncio.timeout(10):
                await asyncio.gather(*(queue.join() for queue in self._queues.values()))
        except TimeoutError:
            _LOG.warning("Output shutdown deadline reached")
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        await asyncio.gather(*(publisher.close() for publisher in self._publishers))
        if self._extension:
            await asyncio.to_thread(self._extension.close)
