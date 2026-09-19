import asyncio

import pytest

from ha_growatt.protocol import (
    Frame,
    FrameBuffer,
    ProtocolError,
    checksum,
    frame_size,
    mask_payload,
    read_frame,
)


def test_standard_crc_check_value():
    assert checksum(b"123456789") == 0x4B37
    assert checksum(b"") == 0xFFFF


def test_known_mask_bytes():
    assert mask_payload(bytes(14)) == b"GrowattGrowatt"
    assert mask_payload(b"Growatt") == bytes(7)


def test_version_two_ack_wire_format():
    wire = bytes.fromhex("000100020003010400")
    frame = Frame.from_bytes(wire)
    assert frame == Frame(1, 2, 1, 4, b"\x00")
    assert frame.to_bytes() == wire


@pytest.mark.parametrize("protocol", [2, 5, 6])
@pytest.mark.parametrize(
    "payload",
    [b"", b"\x00", bytes(range(256)), bytes(65533)],
    ids=["empty", "one-byte", "all-byte-values", "maximum-payload"],
)
def test_wire_round_trip(protocol, payload):
    frame = Frame(123, protocol, 7, 0x16, payload)
    wire = frame.to_bytes()
    assert frame_size(wire[:8]) == len(wire)
    assert Frame.from_bytes(wire) == frame


@pytest.mark.parametrize("protocol", [5, 6])
def test_checksum_failure_is_not_silent(protocol):
    wire = bytearray(Frame(1, protocol, 1, 4, bytes(30)).to_bytes())
    wire[12] ^= 1
    with pytest.raises(ProtocolError, match="checksum"):
        Frame.from_bytes(bytes(wire))


@pytest.mark.parametrize("split", range(41))
def test_tcp_fragmentation_and_coalescing(split):
    frames = [
        Frame(1, 2, 1, 4, b"\x00").to_bytes(),
        Frame(2, 6, 1, 0x16, b"LOGGER0001".ljust(32, b"\x00")).to_bytes(),
        Frame(3, 5, 1, 4, bytes(50)).to_bytes(),
    ]
    wire = b"".join(frames)
    decoder = FrameBuffer()
    output = decoder.feed(wire[:split]) + decoder.feed(wire[split:])
    decoder.finish()
    assert output == frames
    assert decoder.pending_bytes == 0


def test_chunk_containing_many_frames_does_not_accumulate():
    frame = Frame(1, 2, 1, 4, b"\x00").to_bytes()
    decoder = FrameBuffer()
    assert decoder.feed(frame * 20000) == [frame] * 20000
    assert decoder.pending_bytes == 0


@pytest.mark.parametrize("wire", [b"", bytes(7), bytes.fromhex("0001000600010104")])
def test_invalid_header(wire):
    with pytest.raises(ProtocolError):
        frame_size(wire)


def test_unknown_protocol_drops_pending_buffer():
    decoder = FrameBuffer()
    with pytest.raises(ProtocolError, match="protocol"):
        decoder.feed(bytes.fromhex("000100ff00100104"))
    assert decoder.pending_bytes == 0


def test_truncated_frame():
    decoder = FrameBuffer()
    decoder.feed(bytes.fromhex("0001000200030104"))
    with pytest.raises(ProtocolError, match="ended"):
        decoder.finish()
    assert decoder.pending_bytes == 0


@pytest.mark.parametrize("difference", [-1, 1])
def test_declared_length_must_match(difference):
    wire = Frame(1, 2, 1, 4, b"abc").to_bytes()
    wire = wire[:difference] if difference == -1 else wire + b"0"
    with pytest.raises(ProtocolError, match="size"):
        Frame.from_bytes(wire)


@pytest.mark.parametrize(
    "frame",
    [
        Frame(0, 9, 0, 0, b""),
        Frame(0, 6, 256, 0, b""),
        Frame(65536, 2, 1, 4, b""),
        Frame(0, 6, 1, 4, bytes(65534)),
    ],
)
def test_encoding_rejects_invalid_values(frame):
    with pytest.raises(ProtocolError):
        frame.to_bytes()


def test_async_read_enforces_one_deadline_and_eof():
    async def scenario():
        reader = asyncio.StreamReader()
        wire = Frame(1, 6, 1, 4, b"abc").to_bytes()
        reader.feed_data(wire)
        reader.feed_eof()
        assert await read_frame(reader, 1) == wire
        assert await read_frame(reader, 1) is None
        reader = asyncio.StreamReader()
        reader.feed_data(wire[:8])
        with pytest.raises(TimeoutError):
            await read_frame(reader, 0.02)
        reader.feed_eof()
        with pytest.raises(ProtocolError, match="payload"):
            await read_frame_with_header(wire[:8])
        truncated = asyncio.StreamReader()
        truncated.feed_data(b"x")
        truncated.feed_eof()
        with pytest.raises(ProtocolError, match="header"):
            await read_frame(truncated, 1)

    async def read_frame_with_header(header):
        reader = asyncio.StreamReader()
        reader.feed_data(header)
        reader.feed_eof()
        return await read_frame(reader, 1)

    asyncio.run(scenario())
