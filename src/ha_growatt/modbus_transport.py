"""Read-only Modbus UDP and serial RTU transports for direct inverter polling.

UDP supports the socket (MBAP) and RTU datagram framers exposed by other
Growatt Modbus integrations. Serial sends Modbus RTU frames over an explicitly
configured local device. Only functions 03 and 04 are constructed here.
"""

from __future__ import annotations

import asyncio
import secrets

import serial

from .modbus_scan import FUNCTIONS, MAX_WORDS, MIN_DELAY, ModbusReader, ReadError


def _request(kind: str, first: int, count: int) -> tuple[int, bytes]:
    if (
        kind not in FUNCTIONS
        or not 0 <= first <= 65535
        or not 1 <= count <= MAX_WORDS
        or first + count > 65536
    ):
        raise ValueError("Invalid read-only register block")
    function = FUNCTIONS[kind]
    return function, bytes((function,)) + first.to_bytes(2, "big") + count.to_bytes(2, "big")


def _values(function: int, count: int, payload: bytes) -> list[int]:
    if payload[:1] == bytes((function | 0x80,)) and len(payload) == 2:
        raise ReadError(f"Modbus exception {payload[1]}", splittable=payload[1] == 2)
    if payload[:2] != bytes((function, count * 2)) or len(payload) != 2 + count * 2:
        raise ReadError("Mismatched Modbus values")
    return [int.from_bytes(payload[i : i + 2], "big") for i in range(2, len(payload), 2)]


def _crc(data: bytes) -> bytes:
    remainder = 0xFFFF
    for byte in data:
        remainder ^= byte
        for _ in range(8):
            remainder = (remainder >> 1) ^ (0xA001 if remainder & 1 else 0)
    return remainder.to_bytes(2, "little")


def _rtu_reply(frame: bytes, unit: int, function: int, count: int) -> list[int]:
    if len(frame) < 5 or frame[0] != unit:
        raise ReadError("Mismatched Modbus RTU reply")
    if frame[-2:] != _crc(frame[:-2]):
        raise ReadError("Invalid Modbus RTU checksum")
    return _values(function, count, frame[1:-2])


def _socket_reply(frame: bytes, request: bytes, function: int, count: int) -> list[int]:
    if len(frame) < 9 or frame[:4] != request[:4] or frame[6] != request[6]:
        raise ReadError("Mismatched Modbus UDP reply")
    length = int.from_bytes(frame[4:6], "big")
    if not 3 <= length <= 3 + MAX_WORDS * 2 or len(frame) != 6 + length:
        raise ReadError("Invalid Modbus UDP reply length")
    return _values(function, count, frame[7:])


class _OneReply(asyncio.DatagramProtocol):
    def __init__(self, future: asyncio.Future[bytes]):
        self.future = future

    def datagram_received(self, data: bytes, address) -> None:
        if not self.future.done():
            self.future.set_result(data)

    def error_received(self, error: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(ReadError("Modbus UDP gateway unavailable"))


class UdpModbusReader(ModbusReader):
    """Send one read request per datagram; reject any unrelated or malformed reply."""

    def __init__(
        self, host: str, port: int, unit: int, delay: float, timeout: float, framing: str
    ) -> None:
        super().__init__(host, port, unit, delay, timeout)
        if framing not in {"socket", "rtu"}:
            raise ValueError("Choose socket or RTU framing for Modbus UDP")
        self.framing = framing

    async def read(self, kind: str, first: int, count: int) -> list[int]:
        function, pdu = _request(kind, first, count)
        loop = asyncio.get_running_loop()
        await asyncio.sleep(max(0, self.delay - (loop.time() - self._last_send)))
        if self.framing == "rtu":
            body = bytes((self.unit,)) + pdu
            request = body + _crc(body)
        else:
            request = (
                secrets.randbelow(65536).to_bytes(2, "big")
                + b"\0\0\0\x06"
                + bytes((self.unit,))
                + pdu
            )
        reply: asyncio.Future[bytes] = loop.create_future()
        transport = None
        try:
            async with asyncio.timeout(self.timeout):
                transport, _ = await loop.create_datagram_endpoint(
                    lambda: _OneReply(reply), remote_addr=(self.host, self.port)
                )
                transport.sendto(request)
                self._last_send = loop.time()
                frame = await reply
        except (OSError, TimeoutError) as error:
            raise ReadError(type(error).__name__) from error
        finally:
            if transport is not None:
                transport.close()
        if self.framing == "rtu":
            return _rtu_reply(frame, self.unit, function, count)
        return _socket_reply(frame, request, function, count)


class SerialModbusReader:
    """Issue bounded RTU reads over one named local serial adapter."""

    def __init__(
        self,
        device: str,
        unit: int,
        delay: float,
        timeout: float,
        baudrate: int,
        parity: str,
        stopbits: int,
    ) -> None:
        if not device or not 1 <= unit <= 247:
            raise ValueError("Enter a serial device and Modbus unit 1–247")
        if delay < MIN_DELAY or not 0.5 <= timeout <= 10:
            raise ValueError(
                "Use at least 0.5 seconds between requests and a 0.5–10 second timeout"
            )
        if baudrate not in {2400, 4800, 9600, 19200, 38400, 57600, 115200}:
            raise ValueError("Choose a supported serial baud rate")
        if parity not in {"N", "E", "O"} or stopbits not in {1, 2}:
            raise ValueError("Choose serial parity N, E or O and one or two stop bits")
        self.host, self.port, self.unit = device, None, unit
        self.delay, self.timeout = delay, timeout
        self.baudrate, self.parity, self.stopbits = baudrate, parity, stopbits
        self._last_send = 0.0

    async def read(self, kind: str, first: int, count: int) -> list[int]:
        function, pdu = _request(kind, first, count)
        loop = asyncio.get_running_loop()
        await asyncio.sleep(max(0, self.delay - (loop.time() - self._last_send)))
        body = bytes((self.unit,)) + pdu
        request = body + _crc(body)
        self._last_send = loop.time()
        try:
            frame = await asyncio.to_thread(self._exchange, request, function, count)
        except (OSError, serial.SerialException) as error:
            raise ReadError(type(error).__name__) from error
        return _rtu_reply(frame, self.unit, function, count)

    def _exchange(self, request: bytes, function: int, count: int) -> bytes:
        with serial.Serial(
            self.host,
            baudrate=self.baudrate,
            bytesize=8,
            parity=self.parity,
            stopbits=self.stopbits,
            timeout=self.timeout,
            write_timeout=self.timeout,
        ) as device:
            device.reset_input_buffer()
            if device.write(request) != len(request):
                raise ReadError("Modbus serial request was incomplete")
            device.flush()
            header = device.read(3)
            if len(header) != 3 or header[0] != self.unit:
                raise ReadError("Modbus serial gateway timed out or returned another unit")
            if header[1] == (function | 0x80):
                remaining = 2  # Exception code is already in the third byte.
            elif header[1] == function and header[2] == count * 2:
                remaining = count * 2 + 2  # Register bytes and checksum.
            else:
                raise ReadError("Mismatched Modbus serial values")
            tail = device.read(remaining)
            if len(tail) != remaining:
                raise ReadError("Modbus serial reply was incomplete")
            return header + tail
