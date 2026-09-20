"""Independent bidirectional TCP relay with isolated optional observation."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Literal

from .commands import PERMITTED_RECORDS, may_forward
from .device_protocol import acknowledgement, time_command
from .discovery import validate_identity
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
    cloud_fallback: bool = True
    cloud_response_seconds: float = 15
    command_seconds: float = 10

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
            self.cloud_response_seconds,
            self.command_seconds,
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
        if any(
            type(value) is not bool
            for value in (self.block_commands, self.allow_destination_change, self.cloud_fallback)
        ):
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
    fallback_connections: int = 0
    local_replies: int = 0


@dataclass(eq=False)
class RelaySession:
    writer: asyncio.StreamWriter
    upstream: asyncio.StreamWriter | None = None
    cloud: bool = False
    fell_back: bool = False
    logger: str = ""
    protocol: int = 6
    devices: dict[str, int] = field(default_factory=dict)
    awaiting_cloud: dict[tuple, tuple[Frame, float]] = field(default_factory=dict)
    command_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[int, tuple[Frame, asyncio.Future]] = field(default_factory=dict)
    local_sequences: set[int] = field(default_factory=set)
    cloud_sequences: set[int] = field(default_factory=set)
    translations: dict[int, int] = field(default_factory=dict)
    next_sequence: int = 65535
    last_command: float = 0
    clock_needed: bool = False

    def sequence(self) -> int:
        used = self.local_sequences | self.cloud_sequences
        for _ in range(65535):
            self.next_sequence = self.next_sequence % 65535 + 1
            if self.next_sequence not in used:
                self.local_sequences.add(self.next_sequence)
                return self.next_sequence
        # Keep old sequences reserved against delayed replies. A fresh TCP
        # connection safely resets the namespace without retrying a write.
        self.writer.close()
        raise ConnectionError("Reconnect the datalogger before sending more commands")


def _reply_key(frame: Frame) -> tuple:
    return frame.transaction, frame.protocol, frame.unit, frame.function


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
        self._devices: dict[str, RelaySession] = {}
        self._connections: set[RelaySession] = set()

    @property
    def addresses(self) -> list[tuple]:
        return [sock.getsockname() for sock in self._server.sockets] if self._server else []

    @property
    def active_connections(self) -> int:
        return len(self._sessions)

    @property
    def running(self) -> bool:
        return bool(self._server and self._server.is_serving()) and (
            self._worker is None or not self._worker.done()
        )

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
        session = RelaySession(writer)
        self._connections.add(session)
        flows: list[asyncio.Task[None]] = []
        try:
            try:
                async with asyncio.timeout(self.settings.connection_seconds):
                    cloud_reader, session.upstream = await asyncio.open_connection(
                        self.settings.upstream_host, self.settings.upstream_port
                    )
                session.cloud = True
                self._writers.add(session.upstream)
                flows.append(asyncio.create_task(self._cloud_frames(cloud_reader, session)))
                flows.append(asyncio.create_task(self._cloud_deadline(session)))
            except (OSError, TimeoutError):
                await self._fallback(session)
            await self._device_frames(reader, session)
        except (ProtocolError, ConnectionError, OSError, TimeoutError):
            self.stats.session_failures += 1
            _LOG.warning("Inverter connection ended before a clean close")
        finally:
            session.cloud = False
            for task in flows:
                task.cancel()
            await asyncio.gather(*flows, return_exceptions=True)
            for identity in session.devices:
                if self._devices.get(identity) is session:
                    self._devices.pop(identity)
            for _, future in session.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Datalogger disconnected"))
            self._connections.discard(session)
            for connection in [writer] + ([session.upstream] if session.upstream else []):
                await _close(connection)
                self._writers.discard(connection)

    def _parse(self, wire: bytes) -> Frame | None:
        try:
            return Frame.from_bytes(wire)
        except ProtocolError:
            self.stats.malformed_frames += 1
            return None

    def _blocked(self, wire: bytes, frame: Frame | None, direction: Direction) -> bool:
        blocked = self.settings.block_commands and not may_forward(
            wire, frame, self.settings.permitted_records, self.settings.allow_destination_change
        )
        blocked |= direction == "cloud" and wire[7] in self.settings.blocked_cloud_functions
        if blocked:
            self.stats.blocked_frames += 1
        return blocked

    def _enqueue(self, direction: Direction, frame: Frame | None) -> None:
        if self.observer is None or frame is None:
            return
        if self._queue.full():
            self._queue.get_nowait()
            self._queue.task_done()
            self.stats.dropped_observations += 1
        self._queue.put_nowait((direction, frame))

    def _identify(self, frame: Frame, session: RelaySession) -> None:
        if frame.function not in {3, 4, 22, 80} or len(frame.payload) < 10:
            return
        try:
            logger = frame.payload[:10].decode("ascii")
            validate_identity(logger)
            if session.logger and session.logger != logger:
                raise ProtocolError("Datalogger identity changed during a connection")
            session.logger, session.protocol = logger, frame.protocol
            if frame.function == 3:
                session.clock_needed = True
            width = 30 if frame.protocol == 6 else 10
            if frame.function in {3, 4, 80} and len(frame.payload) >= width + 10:
                identity = frame.payload[width : width + 10].decode("ascii").rstrip("\x00 ")
                validate_identity(identity)
                if identity not in session.devices and len(session.devices) >= 64:
                    return
                previous = self._devices.get(identity)
                if previous is not None and previous is not session:
                    previous.writer.close()
                session.devices[identity] = frame.unit
                self._devices[identity] = session
        except ProtocolError:
            raise
        except (ValueError, UnicodeError):
            # Forwarding does not require us to understand the device identity.
            return

    async def _send_device(
        self, session: RelaySession, wire: bytes, before_send: Callable[[], None] | None = None
    ) -> None:
        async with session.send_lock:
            if before_send:
                before_send()
            session.writer.write(wire)
            async with asyncio.timeout(self.settings.write_seconds):
                await session.writer.drain()

    async def _local_reply(self, session: RelaySession, frame: Frame) -> None:
        if reply := acknowledgement(frame):
            await self._send_device(session, reply.to_bytes())
            self.stats.local_replies += 1
        if frame.function == 3 and session.logger:
            await self._set_clock(session)

    async def _set_clock(self, session: RelaySession) -> None:
        command = time_command(session.logger, session.protocol, session.sequence(), datetime.now())
        await self._send_device(session, command.to_bytes())
        session.clock_needed = False

    async def _fallback(self, session: RelaySession) -> None:
        if session.fell_back:
            return
        session.fell_back = True
        if not self.settings.cloud_fallback:
            session.writer.close()
            raise ConnectionError("Cloud connection unavailable")
        session.cloud = False
        if session.upstream:
            session.upstream.close()
        self.stats.fallback_connections += 1
        _LOG.warning("Growatt cloud unavailable; answering this datalogger locally")
        pending = list(session.awaiting_cloud.values())
        session.awaiting_cloud.clear()
        for frame, _ in pending:
            await self._local_reply(session, frame)
        if session.clock_needed and session.logger:
            await self._set_clock(session)

    async def _cloud_deadline(self, session: RelaySession) -> None:
        while session.cloud:
            await asyncio.sleep(min(0.25, self.settings.cloud_response_seconds))
            if session.awaiting_cloud:
                _, sent = next(iter(session.awaiting_cloud.values()))
                if asyncio.get_running_loop().time() - sent >= self.settings.cloud_response_seconds:
                    await self._fallback(session)

    async def _cloud_frames(self, reader, session: RelaySession) -> None:
        try:
            while session.cloud:
                wire = await read_frame(reader, self.settings.frame_seconds)
                if wire is None:
                    break
                frame = self._parse(wire)
                if not session.cloud:
                    return
                if self._blocked(wire, frame, "cloud"):
                    continue
                if frame:
                    session.awaiting_cloud.pop(_reply_key(frame), None)
                    offset = 30 if frame.protocol == 6 else 10
                    if frame.function == 24 and frame.payload[offset : offset + 2] == b"\x00\x1f":
                        session.clock_needed = False
                    if frame.function in {5, 6, 16, 24, 25}:
                        session.cloud_sequences.add(frame.transaction)
                        if frame.transaction in session.local_sequences:
                            sequence = session.sequence()
                            session.translations[sequence] = frame.transaction
                            wire = replace(frame, transaction=sequence).to_bytes()
                await self._send_device(session, wire)
                self.stats.cloud_frames += 1
                self._enqueue("cloud", frame)
        except (ProtocolError, OSError, TimeoutError):
            pass
        finally:
            if session.cloud and not self._closing and not session.writer.is_closing():
                await self._fallback(session)

    def _command_response(self, session: RelaySession, frame: Frame) -> bool:
        if frame.function not in {5, 6, 16, 24, 25}:
            return False
        if frame.transaction not in session.local_sequences:
            return False
        pending = session.pending.get(frame.transaction)
        if pending:
            command, future = pending
            width = 30 if frame.protocol == 6 else 10
            if (
                frame.protocol == command.protocol
                and frame.unit == command.unit
                and frame.function == command.function
                and frame.payload[: width + 2] == command.payload[: width + 2]
                and not future.done()
            ):
                future.set_result(frame)
        # Late local replies must never reach Growatt or another local request.
        return True

    async def _device_frames(self, reader, session: RelaySession) -> None:
        while True:
            wire = await read_frame(reader, self.settings.frame_seconds)
            if wire is None:
                return
            frame = self._parse(wire)
            if frame:
                self._identify(frame, session)
                if frame.transaction in session.translations and frame.function in {
                    5,
                    6,
                    16,
                    24,
                    25,
                }:
                    frame = replace(frame, transaction=session.translations.pop(frame.transaction))
                    wire = frame.to_bytes()
                elif self._command_response(session, frame):
                    continue
            if self._blocked(wire, frame, "device"):
                continue
            if session.cloud:
                if frame and acknowledgement(frame):
                    if len(session.awaiting_cloud) >= self.settings.queue_size:
                        await self._fallback(session)
                    else:
                        session.awaiting_cloud.setdefault(
                            _reply_key(frame), (frame, asyncio.get_running_loop().time())
                        )
                if session.cloud:
                    try:
                        session.upstream.write(wire)
                        async with asyncio.timeout(self.settings.write_seconds):
                            await session.upstream.drain()
                    except (OSError, TimeoutError):
                        await self._fallback(session)
                        # This record was among those acknowledged by fallback.
                        self.stats.device_frames += 1
                        self._enqueue("device", frame)
                        continue
                elif frame:
                    await self._local_reply(session, frame)
            elif frame:
                await self._local_reply(session, frame)
            self.stats.device_frames += 1
            self._enqueue("device", frame)

    def connection(self, identity: str) -> str:
        session = self._devices.get(identity)
        if session is None or session.writer.is_closing():
            return "disconnected"
        return "cloud" if session.cloud else "local"

    def session_key(self, identity: str) -> int | None:
        session = self._devices.get(identity)
        return id(session) if session is not None and not session.writer.is_closing() else None

    async def command(
        self,
        identity: str,
        function: int,
        body: bytes,
        *,
        before_send: Callable[[], None] | None = None,
    ) -> Frame:
        """Send one correlated command; callers own register validation and read-back."""
        session = self._devices.get(identity)
        if session is None or session.writer.is_closing():
            raise ConnectionError("Datalogger is not connected")

        def check_current() -> None:
            if self._devices.get(identity) is not session or session.writer.is_closing():
                raise ConnectionError("Datalogger disconnected before the command was sent")
            if before_send:
                before_send()

        async with session.command_lock:
            loop = asyncio.get_running_loop()
            await asyncio.sleep(max(0, 1 - (loop.time() - session.last_command)))
            sequence = session.sequence()
            prefix = session.logger.encode().ljust(30 if session.protocol == 6 else 10, b"\0")
            frame = Frame(
                sequence,
                session.protocol,
                1 if function == 24 else session.devices[identity],
                function,
                prefix + body,
            )
            future = loop.create_future()
            session.pending[sequence] = (frame, future)
            try:
                session.last_command = loop.time()
                await self._send_device(session, frame.to_bytes(), check_current)
                async with asyncio.timeout(self.settings.command_seconds):
                    return await future
            finally:
                session.pending.pop(sequence, None)

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
