"""Independent bidirectional TCP relay with isolated optional observation."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from .commands import PERMITTED_RECORDS, may_forward
from .protocol import Frame, ProtocolError, read_frame

_LOG = logging.getLogger(__name__)
Direction = Literal["device", "cloud"]
Observer = Callable[[Direction, Frame], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class RelaySettings:
    upstream_host: str
    upstream_port: int = 5279
    listen_host: str = "127.0.0.1"
    listen_port: int = 5279
    connection_seconds: float = 10
    frame_seconds: float = 360
    write_seconds: float = 10
    observation_seconds: float = 10
    max_connections: int = 32
    queue_size: int = 128
    block_commands: bool = True
    allow_destination_change: bool = False
    permitted_records: frozenset[int] = PERMITTED_RECORDS
    blocked_cloud_functions: frozenset[int] = frozenset()

    def __post_init__(self) -> None:
        if any(
            not isinstance(host, str) or not host for host in (self.upstream_host, self.listen_host)
        ):
            raise ValueError("Listener and upstream hosts are required")
        if (
            type(self.upstream_port) is not int
            or type(self.listen_port) is not int
            or not 1 <= self.upstream_port <= 65535
            or not 0 <= self.listen_port <= 65535
        ):
            raise ValueError("Port is outside its valid range")
        deadlines = (
            self.connection_seconds,
            self.frame_seconds,
            self.write_seconds,
            self.observation_seconds,
        )
        if any(
            type(value) not in {int, float} or not math.isfinite(value) or value <= 0
            for value in deadlines
        ):
            raise ValueError("Limits and deadlines must be positive")
        if any(
            type(value) is not int or value <= 0
            for value in (self.max_connections, self.queue_size)
        ):
            raise ValueError("Connection and queue limits must be positive integers")
        if type(self.block_commands) is not bool or type(self.allow_destination_change) is not bool:
            raise ValueError("Command policy switches must be booleans")
        if any(value < 0 or value > 255 for value in self.blocked_cloud_functions):
            raise ValueError("Function codes must fit one byte")
        if any(
            type(value) is not int or not 0 <= value <= 65535 for value in self.permitted_records
        ):
            raise ValueError("Record types must fit two bytes")


@dataclass(slots=True)
class RelayStats:
    accepted: int = 0
    rejected: int = 0
    device_frames: int = 0
    cloud_frames: int = 0
    blocked_frames: int = 0
    malformed_frames: int = 0
    dropped_observations: int = 0
    observation_failures: int = 0
    session_failures: int = 0


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    try:
        async with asyncio.timeout(2):
            await writer.wait_closed()
    except (ConnectionError, OSError, TimeoutError):
        pass


class Relay:
    """Own listener, sessions and a bounded observation worker.

    MQTT or parser delays never stall cloud forwarding. Invalid checksums
    suppress local observations but still forward the original bytes; blocked
    cloud commands are never forwarded, even when their checksum is invalid.
    """

    def __init__(self, settings: RelaySettings, observer: Observer | None = None) -> None:
        self.settings = settings
        self.stats = RelayStats()
        self.observer = observer
        self._queue: asyncio.Queue[tuple[Direction, Frame]] = asyncio.Queue(settings.queue_size)
        self._sessions: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._server: asyncio.Server | None = None
        self._worker: asyncio.Task[None] | None = None
        self._closing = False

    @property
    def addresses(self) -> list[tuple]:
        return [sock.getsockname() for sock in self._server.sockets] if self._server else []

    @property
    def active_connections(self) -> int:
        return len(self._sessions)

    async def start(self) -> None:
        if self._server is not None or self._closing:
            raise RuntimeError("A relay instance can only be started once")
        self._server = await asyncio.start_server(
            self._accept, self.settings.listen_host, self.settings.listen_port
        )
        if self.observer is not None:
            self._worker = asyncio.create_task(self._observe(), name="growatt-observations")

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closing or len(self._sessions) >= self.settings.max_connections:
            self.stats.rejected += 1
            writer.close()
            return
        self.stats.accepted += 1
        self._writers.add(writer)
        task = asyncio.create_task(self._session(reader, writer), name="growatt-session")
        self._sessions.add(task)
        task.add_done_callback(self._sessions.discard)

    async def _session(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        upstream: asyncio.StreamWriter | None = None
        flows: list[asyncio.Task[None]] = []
        try:
            async with asyncio.timeout(self.settings.connection_seconds):
                cloud_reader, upstream = await asyncio.open_connection(
                    self.settings.upstream_host, self.settings.upstream_port
                )
            self._writers.add(upstream)
            flows = [
                asyncio.create_task(self._forward(reader, upstream, "device")),
                asyncio.create_task(self._forward(cloud_reader, writer, "cloud")),
            ]
            done, _ = await asyncio.wait(flows, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
        except (ProtocolError, ConnectionError, OSError, TimeoutError):
            self.stats.session_failures += 1
            _LOG.warning("Inverter connection ended before a clean close")
        finally:
            for task in flows:
                task.cancel()
            await asyncio.gather(*flows, return_exceptions=True)
            for connection in [writer] + ([upstream] if upstream is not None else []):
                await _close(connection)
                self._writers.discard(connection)

    async def _forward(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, direction: Direction
    ) -> None:
        while True:
            wire = await read_frame(reader, self.settings.frame_seconds)
            if wire is None:
                return
            try:
                frame = Frame.from_bytes(wire)
            except ProtocolError:
                frame = None
                self.stats.malformed_frames += 1
            blocked = self.settings.block_commands and not may_forward(
                wire, frame, self.settings.permitted_records, self.settings.allow_destination_change
            )
            blocked |= direction == "cloud" and wire[7] in self.settings.blocked_cloud_functions
            if blocked:
                self.stats.blocked_frames += 1
                continue
            writer.write(wire)
            async with asyncio.timeout(self.settings.write_seconds):
                await writer.drain()
            if direction == "device":
                self.stats.device_frames += 1
            else:
                self.stats.cloud_frames += 1
            if self.observer is None or frame is None:
                continue
            if self._queue.full():
                self._queue.get_nowait()
                self._queue.task_done()
                self.stats.dropped_observations += 1
            self._queue.put_nowait((direction, frame))

    async def _observe(self) -> None:
        assert self.observer is not None
        while True:
            direction, frame = await self._queue.get()
            try:
                async with asyncio.timeout(self.settings.observation_seconds):
                    await self.observer(direction, frame)
            except Exception:
                self.stats.observation_failures += 1
                _LOG.warning("Local telemetry observation failed; forwarding remains active")
            finally:
                self._queue.task_done()

    async def close(self) -> None:
        self._closing = True
        if self._server is not None:
            self._server.close()
        sessions = list(self._sessions)
        for task in sessions:
            task.cancel()
        await asyncio.gather(*sessions, return_exceptions=True)
        # Accepted sockets also need closing when their task was cancelled
        # before entering _session. Server.wait_closed waits for these clients.
        for writer in list(self._writers):
            await _close(writer)
            self._writers.discard(writer)
        if self._server is not None:
            await self._server.wait_closed()
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)

    async def __aenter__(self) -> Relay:
        await self.start()
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()
