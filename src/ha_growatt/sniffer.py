"""Passive Ethernet/IPv4 capture with bounded TCP stream reassembly."""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from dataclasses import dataclass, field

from .protocol import Frame, FrameBuffer, ProtocolError
from .relay import Observer, RelaySettings


@dataclass(frozen=True, slots=True)
class Segment:
    source: str
    destination: str
    source_port: int
    destination_port: int
    sequence: int
    flags: int
    payload: bytes


def tcp_segment(packet: bytes) -> Segment | None:
    if len(packet) < 14:
        return None
    offset = 14
    ethertype = int.from_bytes(packet[12:14], "big")
    while ethertype in {0x8100, 0x88A8}:
        if len(packet) < offset + 4:
            return None
        ethertype = int.from_bytes(packet[offset + 2 : offset + 4], "big")
        offset += 4
    if ethertype != 0x0800 or len(packet) < offset + 20:
        return None
    ip = packet[offset:]
    length = int.from_bytes(ip[2:4], "big")
    header = (ip[0] & 15) * 4
    if ip[0] >> 4 != 4 or header < 20 or len(ip) < length or length < header + 20:
        return None
    if ip[9] != 6 or int.from_bytes(ip[6:8], "big") & 0x3FFF:
        return None
    tcp = ip[header:length]
    tcp_header = (tcp[12] >> 4) * 4
    if tcp_header < 20 or tcp_header > len(tcp):
        return None
    source_port, destination_port, sequence = struct.unpack_from(">HHI", tcp)
    return Segment(
        socket.inet_ntoa(ip[12:16]),
        socket.inet_ntoa(ip[16:20]),
        source_port,
        destination_port,
        sequence,
        tcp[13],
        tcp[tcp_header:],
    )


def _distance(sequence: int, expected: int) -> int:
    return ((sequence - expected + 2**31) % 2**32) - 2**31


@dataclass(slots=True)
class Stream:
    expected: int
    updated: float
    parser: FrameBuffer = field(default_factory=FrameBuffer)
    waiting: dict[int, bytes] = field(default_factory=dict)


class Reassembler:
    def __init__(self, *, maximum_flows: int = 128, idle_seconds: float = 360) -> None:
        self.maximum_flows = maximum_flows
        self.idle_seconds = idle_seconds
        self.streams: dict[tuple, Stream] = {}

    def feed(self, segment: Segment, now: float | None = None) -> list[Frame]:
        now = time.monotonic() if now is None else now
        for key in [
            key for key, value in self.streams.items() if now - value.updated > self.idle_seconds
        ]:
            self.streams.pop(key)
        key = (segment.source, segment.source_port, segment.destination, segment.destination_port)
        if segment.flags & 4:
            self.streams.pop(key, None)
            return []
        sequence = (segment.sequence + bool(segment.flags & 2)) % 2**32
        if segment.flags & 2:
            self.streams.pop(key, None)
        if key not in self.streams:
            if len(self.streams) >= self.maximum_flows:
                oldest = min(self.streams, key=lambda item: self.streams[item].updated)
                self.streams.pop(oldest)
            self.streams[key] = Stream(sequence, now)
        stream = self.streams[key]
        stream.updated = now
        data = segment.payload
        distance = _distance(sequence, stream.expected)
        if distance < 0:
            data = data[min(len(data), -distance) :]
            sequence = stream.expected
        elif distance > 0:
            if (
                len(stream.waiting) >= 128
                or sum(map(len, stream.waiting.values())) + len(data) > 1_048_576
            ):
                self.streams.pop(key)
            elif data:
                stream.waiting.setdefault(sequence, data)
            return []
        frames = []
        try:
            while data:
                stream.expected = (stream.expected + len(data)) % 2**32
                for wire in stream.parser.feed(data):
                    try:
                        frames.append(Frame.from_bytes(wire))
                    except ProtocolError:
                        continue
                data = b""
                for queued in sorted(
                    stream.waiting, key=lambda seq: _distance(seq, stream.expected)
                ):
                    distance = _distance(queued, stream.expected)
                    if distance > 0:
                        break
                    block = stream.waiting.pop(queued)
                    data = block[min(len(block), -distance) :]
                    if data:
                        break
        except ProtocolError:
            self.streams.pop(key, None)
        if segment.flags & 1:
            self.streams.pop(key, None)
        return frames


class Sniffer:
    def __init__(
        self, settings: RelaySettings, observer: Observer, interface: str | None = None
    ) -> None:
        self.settings = settings
        self.observer = observer
        self.interface = interface
        self._socket = None
        self._task = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def _capture(self, addresses: set[str]) -> None:
        loop = asyncio.get_running_loop()
        streams = Reassembler(maximum_flows=self.settings.max_connections)
        while True:
            packet = await loop.sock_recv(self._socket, 65535)
            segment = tcp_segment(packet)
            if segment is None or segment.destination not in addresses:
                continue
            if segment.destination_port != self.settings.upstream_port:
                continue
            for frame in streams.feed(segment):
                try:
                    async with asyncio.timeout(self.settings.observation_seconds):
                        await self.observer("device", frame)
                except Exception:
                    continue

    async def __aenter__(self):
        if not hasattr(socket, "AF_PACKET"):
            raise OSError("Passive capture requires Linux packet sockets")
        loop = asyncio.get_running_loop()
        endpoints = await loop.getaddrinfo(self.settings.upstream_host, None, family=socket.AF_INET)
        self._socket = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
        try:
            if self.interface:
                self._socket.bind((self.interface, 0))
            self._socket.setblocking(False)
            self._task = asyncio.create_task(
                self._capture({endpoint[4][0] for endpoint in endpoints})
            )
        except BaseException:
            self._socket.close()
            raise
        return self

    async def __aexit__(self, *_):
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        if self._socket:
            self._socket.close()
