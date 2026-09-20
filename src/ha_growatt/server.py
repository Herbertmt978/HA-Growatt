"""Standalone datalogger server and compatible local register API."""

from __future__ import annotations

import asyncio
import json
import socket
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import parse_qs, urlsplit

from .discovery import validate_identity
from .protocol import Frame, ProtocolError, read_frame
from .relay import Observer, RelaySettings, _close


def logger_prefix(identity: str, protocol: int) -> bytes:
    validate_identity(identity)
    encoded = identity.encode("ascii")
    if len(encoded) != 10:
        raise ValueError("A datalogger identity must contain ten characters")
    return encoded.ljust(30 if protocol == 6 else 10, b"\0")


def time_command(identity: str, protocol: int, sequence: int, now: datetime) -> Frame:
    stamp = now.strftime("%Y-%m-%d %H:%M:%S").encode("ascii")
    payload = (
        logger_prefix(identity, protocol) + b"\x00\x1f" + len(stamp).to_bytes(2, "big") + stamp
    )
    return Frame(sequence, protocol, 1, 24, payload)


def acknowledgement(frame: Frame) -> Frame | None:
    if frame.function == 22:
        return frame
    if frame.function in {3, 4, 27, 32, 80}:
        return Frame(frame.transaction, frame.protocol, frame.unit, frame.function, b"\0")
    return None


def register_command(
    logger: str,
    protocol: int,
    unit: int,
    sequence: int,
    method: str,
    target: str,
    options: dict[str, str],
    now: datetime | None = None,
) -> Frame:
    command = options.get("command", "")
    prefix = logger_prefix(logger, protocol)
    if method == "PUT" and command == "datetime":
        if target != "datalogger":
            raise ValueError("datetime command not allowed for inverter")
        return time_command(logger, protocol, sequence, now or datetime.now())
    if method == "PUT" and command == "multiregister" and target == "inverter":
        start, end = int(options["startregister"]), int(options["endregister"])
        if not 0 <= start <= end <= 65535:
            raise ValueError("Invalid register range")
        values = bytes.fromhex(options["value"])
        if len(values) != (end - start + 1) * 2:
            raise ValueError("Register range does not match the supplied values")
        return Frame(
            sequence,
            protocol,
            unit,
            16,
            prefix + start.to_bytes(2, "big") + end.to_bytes(2, "big") + values,
        )
    if command != "register" or method not in {"GET", "PUT"}:
        raise ValueError("no valid command entered")
    register = int(options["register"])
    if not 0 <= register <= 65535:
        raise ValueError("Invalid register")
    address = register.to_bytes(2, "big")
    if method == "GET":
        return Frame(
            sequence, protocol, unit, 25 if target == "datalogger" else 5, prefix + address * 2
        )
    value = options["value"]
    if target == "datalogger":
        encoded = value.encode("utf-8")
        return Frame(
            sequence,
            protocol,
            unit,
            24,
            prefix + address + len(encoded).to_bytes(2, "big") + encoded,
        )
    format_ = options.get("format", "dec")
    if format_ == "text":
        encoded = value.encode("utf-8")
    elif format_ in {"hex", "dec"}:
        encoded = int(value, 16 if format_ == "hex" else 10).to_bytes(2, "big")
    else:
        raise ValueError("Invalid register format")
    return Frame(sequence, protocol, unit, 6, prefix + address + encoded)


def register_response(frame: Frame) -> tuple[str, dict] | None:
    offset = 30 if frame.protocol == 6 else 10
    body = frame.payload[offset:]
    if frame.function in {5, 6, 24, 25} and len(body) >= 3:
        key = body[:2].hex()
        if frame.function == 5 and len(body) >= 6:
            return key, {"value": body[4:].hex()}
        if frame.function == 6 and len(body) >= 4:
            return key, {"value": body[2:].hex()}
        if frame.function == 24:
            return key, {"result": body[2:3].hex()}
        if frame.function == 25 and len(body) >= 4:
            length = int.from_bytes(body[2:4], "big")
            if len(body) >= 4 + length:
                return key, {"value": body[4 : 4 + length].decode("utf-8", errors="replace")}
    if frame.function == 16 and len(body) >= 5:
        return body[:4].hex(), {"value": body[4:5].hex()}
    return None


@dataclass(eq=False)
class DeviceSession:
    writer: asyncio.StreamWriter
    peer: tuple
    protocol: int = 6
    logger: str | None = None
    inverters: dict[str, dict] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    pending: dict[tuple[int, str], asyncio.Future] = field(default_factory=dict)


class Server:
    def __init__(
        self,
        settings: RelaySettings,
        observer: Observer | None = None,
        *,
        api_host: str = "127.0.0.1",
        api_port: int = 5782,
        response_seconds: float = 10,
    ) -> None:
        self.settings = settings
        self.observer = observer
        self.api_host, self.api_port = api_host, api_port
        self.response_seconds = response_seconds
        self._listener = self._http = None
        self._sessions: set[DeviceSession] = set()
        self._tasks: set[asyncio.Task] = set()
        self._registry: dict[str, DeviceSession] = {}
        self._cache: dict[int, dict] = {}
        self._queue: asyncio.Queue[Frame] = asyncio.Queue(settings.queue_size)
        self._worker = None
        self._sequence = 0
        self._http_writers: set[asyncio.StreamWriter] = set()

    @property
    def address(self) -> tuple:
        return self._listener.sockets[0].getsockname()

    @property
    def api_address(self) -> tuple:
        return self._http.sockets[0].getsockname()

    @property
    def running(self) -> bool:
        return bool(
            self._listener
            and self._listener.is_serving()
            and self._http
            and self._http.is_serving()
        ) and (self._worker is None or not self._worker.done())

    def registry(self) -> dict:
        return {
            logger: {
                "ip": session.peer[0],
                "port": session.peer[1],
                "protocol": f"{session.protocol:02x}",
                **session.inverters,
            }
            for logger, session in self._registry.items()
        }

    def _spawn(self, coroutine) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _accept(self, reader, writer) -> None:
        if len(self._sessions) >= self.settings.max_connections:
            writer.close()
            return
        session = DeviceSession(writer, writer.get_extra_info("peername"))
        self._sessions.add(session)
        sock = writer.get_extra_info("socket")
        for option, value in [(socket.SO_KEEPALIVE, 1)]:
            try:
                sock.setsockopt(socket.SOL_SOCKET, option, value)
            except OSError:
                pass
        for name, value in [("TCP_KEEPIDLE", 120), ("TCP_KEEPINTVL", 30), ("TCP_KEEPCNT", 3)]:
            if option := getattr(socket, name, None):
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, option, value)
                except OSError:
                    pass
        self._spawn(self._device(reader, session))

    def _accept_http(self, reader, writer) -> None:
        if len(self._http_writers) >= self.settings.max_connections:
            writer.close()
            return
        self._http_writers.add(writer)
        self._spawn(self._request(reader, writer))

    async def _send(self, session, frame) -> None:
        session.writer.write(frame.to_bytes())
        async with asyncio.timeout(self.settings.write_seconds):
            await session.writer.drain()

    async def _device(self, reader, session) -> None:
        try:
            while wire := await read_frame(reader, self.settings.frame_seconds):
                frame = Frame.from_bytes(wire)
                session.protocol = frame.protocol
                if frame.function in {3, 22}:
                    logger = frame.payload[:10].decode("ascii")
                    validate_identity(logger)
                    previous = self._registry.get(logger)
                    if previous and previous is not session:
                        previous.writer.close()
                    session.logger = logger
                    self._registry[logger] = session
                    if frame.function == 3:
                        width = 30 if frame.protocol == 6 else 10
                        identity = frame.payload[width : width + 10].decode("ascii")
                        validate_identity(identity)
                        session.inverters[identity] = {
                            "inverterno": f"{frame.unit:02x}",
                            "power": 0,
                        }
                if response := acknowledgement(frame):
                    await self._send(session, response)
                if frame.function == 3:
                    self._sequence = (self._sequence % 65535) + 1
                    await self._send(
                        session,
                        time_command(
                            session.logger, frame.protocol, self._sequence, datetime.now()
                        ),
                    )
                if response := register_response(frame):
                    key, value = response
                    self._cache.setdefault(frame.function, {})[key] = value
                    if frame.function == 6:
                        self._cache.setdefault(5, {})[key] = value
                    future = session.pending.get((frame.function, key))
                    if future is not None and not future.done():
                        future.set_result(value)
                if self.observer and frame.function in {3, 4, 27, 32, 80}:
                    if self._queue.full():
                        self._queue.get_nowait()
                        self._queue.task_done()
                    self._queue.put_nowait(frame)
        except (ProtocolError, UnicodeError, ValueError, OSError, TimeoutError):
            pass
        finally:
            if session.logger and self._registry.get(session.logger) is session:
                self._registry.pop(session.logger)
            for future in session.pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Datalogger disconnected"))
            await _close(session.writer)
            self._sessions.discard(session)

    async def _observe(self) -> None:
        while True:
            frame = await self._queue.get()
            try:
                async with asyncio.timeout(self.settings.observation_seconds):
                    await self.observer("device", frame)
            except Exception:
                pass
            finally:
                self._queue.task_done()

    async def api(self, method: str, path: str) -> tuple[int, str]:
        parsed = urlsplit(path)
        target = parsed.path.strip("/")
        options = {key: values[-1] for key, values in parse_qs(parsed.query).items()}
        if method == "GET" and target in {"", "help"}:
            return (
                200,
                "HA Growatt. Use /datalogger or /inverter to list devices or access registers.",
            )
        if method == "GET" and target == "info":
            return 200, json.dumps(
                {"connections": len(self._sessions), "dataloggers": len(self._registry)}
            )
        if target not in {"datalogger", "inverter"}:
            return 400, "Bad request"
        if method == "GET" and not options:
            return 200, json.dumps(self.registry())
        command = options.get("command")
        if command is None:
            return 400, "no command entered"
        if method == "GET" and command == "regall":
            return 200, json.dumps(self._cache.get(25 if target == "datalogger" else 5, {}))
        identity = options.get(target)
        session = (
            self._registry.get(identity)
            if target == "datalogger"
            else next((s for s in self._sessions if identity in s.inverters), None)
        )
        if session is None:
            return 400, "device not connected"
        unit = 1 if target == "datalogger" else int(session.inverters[identity]["inverterno"], 16)
        try:
            async with session.lock:
                self._sequence = (self._sequence % 65535) + 1
                frame = register_command(
                    session.logger, session.protocol, unit, self._sequence, method, target, options
                )
                width = 30 if frame.protocol == 6 else 10
                key = frame.payload[width : width + (4 if frame.function == 16 else 2)].hex()
                future = asyncio.get_running_loop().create_future()
                session.pending[(frame.function, key)] = future
                try:
                    await self._send(session, frame)
                    async with asyncio.timeout(self.response_seconds):
                        response = await future
                finally:
                    session.pending.pop((frame.function, key), None)
                if method == "PUT":
                    if (
                        response.get(
                            "result", response.get("value") if frame.function == 16 else "00"
                        )
                        != "00"
                    ):
                        return 400, "device rejected the command"
                    return 200, "OK"
                value = response["value"]
                if target == "inverter":
                    format_ = options.get("format", "dec")
                    if format_ == "dec":
                        value = int(value, 16)
                    elif format_ == "text":
                        value = bytes.fromhex(value).decode("utf-8", errors="replace")
                    elif format_ != "hex":
                        return 400, "Invalid register format"
                return 200, json.dumps({"value": value})
        except (ValueError, KeyError, OverflowError):
            return 400, "invalid command parameters"
        except (TimeoutError, ConnectionError, OSError):
            return 400, "no or invalid response received"

    async def _request(self, reader, writer) -> None:
        try:
            async with asyncio.timeout(15):
                header = await reader.readuntil(b"\r\n\r\n")
                if len(header) > 16384:
                    return
                method, path, _ = header.split(b"\r\n", 1)[0].decode("ascii").split(" ", 2)
                status, body = await self.api(method, path)
                payload = body.encode("utf-8")
                writer.write(
                    (
                        f"HTTP/1.1 {status} {'OK' if status == 200 else 'Bad Request'}\r\n"
                        "Content-Type: text/plain; charset=utf-8\r\n"
                        f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
                    ).encode()
                    + payload
                )
                await writer.drain()
        except (ValueError, UnicodeError, OSError, TimeoutError, asyncio.IncompleteReadError):
            pass
        finally:
            await _close(writer)
            self._http_writers.discard(writer)

    async def __aenter__(self):
        self._listener = await asyncio.start_server(
            self._accept, self.settings.listen_host, self.settings.listen_port
        )
        try:
            self._http = await asyncio.start_server(
                self._accept_http,
                self.api_host,
                self.api_port,
                limit=16384,
            )
            if self.observer:
                self._worker = asyncio.create_task(self._observe())
        except BaseException:
            await self.__aexit__()
            raise
        return self

    async def __aexit__(self, *_):
        for listener in (self._listener, self._http):
            if listener:
                listener.close()
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for session in list(self._sessions):
            await _close(session.writer)
        for writer in list(self._http_writers):
            await _close(writer)
        self._http_writers.clear()
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
        for listener in (self._listener, self._http):
            if listener:
                await listener.wait_closed()
