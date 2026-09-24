"""Real local UDP frames and a bounded fake serial port for read-only transports."""

import asyncio

import pytest

from ha_growatt.modbus_scan import ReadError
from ha_growatt.modbus_transport import SerialModbusReader, UdpModbusReader, _crc


class Gateway(asyncio.DatagramProtocol):
    def __init__(self, framing, *, bad_reply=False):
        self.framing = framing
        self.bad_reply = bad_reply
        self.requests = []

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, request, address):
        self.requests.append(request)
        if self.framing == "rtu":
            assert request[0] == 7 and request[1] in {3, 4}
            assert request[-2:] == _crc(request[:-2])
            count = int.from_bytes(request[4:6], "big")
            body = bytes((7, request[1], count * 2)) + b"\x12\x34" * count
            reply = body + _crc(body)
            if self.bad_reply:
                reply = reply[:-1] + bytes((reply[-1] ^ 0xFF,))
        else:
            assert request[6] == 7 and request[7] in {3, 4}
            count = int.from_bytes(request[10:12], "big")
            payload = bytes((request[7], count * 2)) + b"\x12\x34" * count
            reply = request[:4] + (len(payload) + 1).to_bytes(2, "big") + request[6:7] + payload
            if self.bad_reply:
                reply = b"\xff\xff" + reply[2:]
        self.transport.sendto(reply, address)


@pytest.mark.parametrize("framing", ["socket", "rtu"])
def test_udp_read_functions_and_reply_validation(framing):
    async def scenario():
        loop = asyncio.get_running_loop()
        gateway = Gateway(framing)
        server, _ = await loop.create_datagram_endpoint(
            lambda: gateway, local_addr=("127.0.0.1", 0)
        )
        try:
            port = server.get_extra_info("sockname")[1]
            reader = UdpModbusReader("127.0.0.1", port, 7, 0.5, 1, framing)
            assert await reader.read("input", 3000, 2) == [0x1234, 0x1234]
            assert await reader.read("holding", 43, 1) == [0x1234]
            with pytest.raises(ValueError, match="read-only"):
                await reader.read("write", 43, 1)
            assert len(gateway.requests) == 2
            assert {request[1 if framing == "rtu" else 7] for request in gateway.requests} == {
                3,
                4,
            }
        finally:
            server.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("framing", ["socket", "rtu"])
def test_udp_rejects_wrong_transaction_or_checksum(framing):
    async def scenario():
        loop = asyncio.get_running_loop()
        gateway = Gateway(framing, bad_reply=True)
        server, _ = await loop.create_datagram_endpoint(
            lambda: gateway, local_addr=("127.0.0.1", 0)
        )
        try:
            reader = UdpModbusReader(
                "127.0.0.1", server.get_extra_info("sockname")[1], 7, 0.5, 1, framing
            )
            with pytest.raises(ReadError, match="Mismatched|checksum"):
                await reader.read("input", 3000, 2)
        finally:
            server.close()

    asyncio.run(scenario())


def test_rtu_crc_matches_published_frame_example():
    assert _crc(bytes.fromhex("01030000000A")) == bytes.fromhex("C5CD")


def test_serial_rtu_reads_only_validated_frames(monkeypatch):
    requests = []

    class Port:
        def __init__(self, device, **kwargs):
            assert device == "COM9"
            assert kwargs["baudrate"] == 9600 and kwargs["parity"] == "E"
            self.reply = b""

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def reset_input_buffer(self):
            pass

        def write(self, request):
            requests.append(request)
            assert request[0] == 7 and request[1] in {3, 4}
            assert request[-2:] == _crc(request[:-2])
            count = int.from_bytes(request[4:6], "big")
            body = bytes((7, request[1], count * 2)) + b"\x12\x34" * count
            self.reply = body + _crc(body)
            return len(request)

        def flush(self):
            pass

        def read(self, count):
            chunk, self.reply = self.reply[:count], self.reply[count:]
            return chunk

    monkeypatch.setattr("ha_growatt.modbus_transport.serial.Serial", Port)

    async def scenario():
        reader = SerialModbusReader("COM9", 7, 0.5, 1, 9600, "E", 1)
        assert await reader.read("input", 3000, 2) == [0x1234, 0x1234]
        assert await reader.read("holding", 43, 1) == [0x1234]
        with pytest.raises(ValueError, match="read-only"):
            await reader.read("write", 43, 1)

    asyncio.run(scenario())
    assert len(requests) == 2


def test_serial_rtu_rejects_bad_checksum(monkeypatch):
    class Port:
        def __init__(self, *_args, **_kwargs):
            self.reply = b""

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def reset_input_buffer(self):
            pass

        def write(self, request):
            body = bytes((request[0], request[1], 2, 0, 42))
            self.reply = body + b"\0\0"
            return len(request)

        def flush(self):
            pass

        def read(self, count):
            chunk, self.reply = self.reply[:count], self.reply[count:]
            return chunk

    monkeypatch.setattr("ha_growatt.modbus_transport.serial.Serial", Port)

    async def scenario():
        reader = SerialModbusReader("COM9", 7, 0.5, 1, 9600, "N", 1)
        with pytest.raises(ReadError, match="checksum"):
            await reader.read("input", 3000, 1)

    asyncio.run(scenario())
