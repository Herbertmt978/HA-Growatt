"""Run a Growatt listener for a Home Assistant integration without MQTT."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from .discovery import validate_identity
from .packet_health import PacketHealth, PrivateCapture
from .protocol import Frame, ProtocolError
from .recovery import MAX_DEVICES, ReadingStore, Snapshot
from .relay import Relay, RelaySettings
from .selection import FamilyDecoder, SelectionSettings
from .server import Server
from .telemetry import Telemetry
from .unknown_shine import UnknownShineFormats

_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class NativeReading:
    identity: str
    snapshot: Snapshot
    restored: bool = False
    partial: bool = False


class NativeReceiver:
    """Own one TCP listener and deliver decoded readings to HA callbacks.

    The app and this receiver use the same protocol and relay code. Only the
    destination differs: this class publishes to native HA entities instead of
    MQTT. A buffered upload is never presented as a current sensor value.
    """

    def __init__(
        self,
        *,
        port: int = 5279,
        forward_cloud: bool = True,
        state_path: str = "",
        family: str = "default",
        unknown_diagnostics: bool = False,
        on_telemetry: Callable[[Telemetry], Awaitable[None]] | None = None,
    ) -> None:
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("The datalogger port must be between 1024 and 65535")
        if type(forward_cloud) is not bool:
            raise ValueError("Cloud forwarding must be on or off")
        if type(unknown_diagnostics) is not bool:
            raise ValueError("Unknown Shine diagnostics must be on or off")
        self.port = port
        self.forward_cloud = forward_cloud
        self.decoder = FamilyDecoder(SelectionSettings(family=family))
        self.on_telemetry = on_telemetry
        self.packet_health = PacketHealth()
        self.private_capture = PrivateCapture()
        self.unknown_formats = UnknownShineFormats() if unknown_diagnostics else None
        self.snapshots: dict[str, Snapshot] = {}
        self._live_snapshots: set[str] = set()
        self._listeners: set[Callable[[NativeReading], None]] = set()
        self._buffered_listeners: set[Callable[[Telemetry], None]] = set()
        self._store = (
            ReadingStore(state_path, ("native", port, forward_cloud, family))
            if state_path
            else None
        )
        self._transport: Relay | Server | None = None
        self.announcements = 0
        self.announcement_warnings = 0
        self.measurements = 0
        self.failed_measurements = 0
        self.incomplete_fields = 0
        self.buffered_records = 0
        self.cache_error = False

    @property
    def running(self) -> bool:
        return bool(self._transport and self._transport.running)

    @property
    def control_transport(self) -> Relay | Server | None:
        """Return only the currently running, session-aware command transport."""
        return self._transport if self.running else None

    def subscribe(self, listener: Callable[[NativeReading], None]) -> Callable[[], None]:
        self._listeners.add(listener)
        for identity, snapshot in self.snapshots.items():
            listener(
                NativeReading(identity, snapshot, restored=identity not in self._live_snapshots)
            )
        return lambda: self._listeners.discard(listener)

    def subscribe_buffered(self, listener: Callable[[Telemetry], None]) -> Callable[[], None]:
        self._buffered_listeners.add(listener)
        return lambda: self._buffered_listeners.discard(listener)

    async def start(self) -> None:
        if self._transport is not None:
            raise RuntimeError("The receiver is already started")
        if self._store is not None:
            try:
                self.snapshots = await asyncio.to_thread(self._store.load)
            except (OSError, ValueError, KeyError, TypeError):
                self.cache_error = True
                _LOG.warning("Saved native readings could not be restored")
        settings = RelaySettings(
            upstream_host="server.growatt.com",
            listen_host="0.0.0.0",
            listen_port=self.port,
        )
        transport: Relay | Server
        if self.forward_cloud:
            transport = Relay(settings, self.observe)
        else:
            transport = Server(settings, self.observe, api_host="127.0.0.1", api_port=0)
        await transport.__aenter__()
        self._transport = transport

    async def observe(self, direction: str, frame: Frame) -> None:
        if direction != "device":
            return
        self.private_capture.record(frame)
        if frame.function not in {3, 4, 27, 32, 80}:
            return
        if len(frame.to_bytes()) < 100:
            return
        if frame.function == 3:
            self.announcements += 1
        try:
            telemetry = self.decoder.decode(frame)
        except (ProtocolError, ValueError, UnicodeError):
            if self.unknown_formats is not None:
                self.unknown_formats.observe(frame)
            self.packet_health.observe(frame)
            if frame.function == 3:
                self.announcement_warnings += 1
            else:
                self.failed_measurements += 1
            return
        self.packet_health.observe(frame, telemetry)
        if self.unknown_formats is not None:
            self.unknown_formats.observe(frame, telemetry)
        if self.on_telemetry is not None:
            try:
                async with asyncio.timeout(0.5):
                    await self.on_telemetry(telemetry)
            except Exception:
                _LOG.warning("A native telemetry output failed; receiver traffic continues")
        if telemetry.buffered:
            self.buffered_records += 1
            for listener in tuple(self._buffered_listeners):
                try:
                    listener(telemetry)
                except Exception:
                    _LOG.warning("A native buffered-reading listener failed")
            return
        identity = telemetry.device_id or telemetry.values.get("pvserial")
        if not isinstance(identity, str):
            return
        try:
            validate_identity(identity)
        except ValueError:
            return
        if identity not in self.snapshots and len(self.snapshots) >= MAX_DEVICES:
            return
        snapshot = Snapshot(telemetry, datetime.now(UTC))
        self.snapshots[identity] = snapshot
        self._live_snapshots.add(identity)
        if frame.function != 3:
            self.measurements += 1
        self.incomplete_fields += telemetry.decode_errors
        if self._store is not None:
            try:
                await asyncio.to_thread(self._store.save, dict(self.snapshots))
                self.cache_error = False
            except (OSError, ValueError):
                self.cache_error = True
                _LOG.warning("Native readings are live, but restart recovery could not be saved")
        reading = NativeReading(identity, snapshot)
        for listener in tuple(self._listeners):
            try:
                listener(reading)
            except Exception:
                _LOG.warning("A native reading listener failed")

    async def close(self) -> None:
        self.private_capture.clear()
        transport = self._transport
        self._transport = None
        if transport is not None:
            await transport.__aexit__(None, None, None)
        self._listeners.clear()
        self._buffered_listeners.clear()
        self._live_snapshots.clear()
