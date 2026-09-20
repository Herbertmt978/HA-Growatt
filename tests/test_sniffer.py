import socket
import struct

from ha_growatt.protocol import Frame
from ha_growatt.sniffer import Reassembler, Segment, tcp_segment


def packet(payload, *, sequence=100, flags=0x18, vlan=False, fragment=0):
    tcp = struct.pack(">HHIIBBHHH", 40000, 5279, sequence, 0, 5 << 4, flags, 65535, 0, 0) + payload
    ip = struct.pack(
        ">BBHHHBBH4s4s",
        0x45,
        0,
        len(tcp) + 20,
        1,
        fragment,
        64,
        6,
        0,
        socket.inet_aton("192.0.2.2"),
        socket.inet_aton("192.0.2.1"),
    )
    ethernet = bytes(12) + (b"\x81\x00\x00\x01\x08\x00" if vlan else b"\x08\x00")
    return ethernet + ip + tcp


def test_packet_headers_vlan_and_fragment_rejection():
    for vlan in (False, True):
        segment = tcp_segment(packet(b"example", vlan=vlan))
        assert segment == Segment("192.0.2.2", "192.0.2.1", 40000, 5279, 100, 0x18, b"example")
    assert tcp_segment(packet(b"example", fragment=0x2000)) is None
    assert tcp_segment(packet(b"example")[:-1]) is None
    assert tcp_segment(bytes(40)) is None


def test_split_coalesced_retransmitted_and_out_of_order_records():
    frames = [Frame(i, 6, 1, 4, b"test payload") for i in range(3)]
    wire = b"".join(frame.to_bytes() for frame in frames)
    streams = Reassembler()
    assert streams.feed(tcp_segment(packet(b"", sequence=99, flags=2))) == []
    assert streams.feed(tcp_segment(packet(wire[:9]))) == []
    assert streams.feed(tcp_segment(packet(wire[25:], sequence=125))) == []
    assert streams.feed(tcp_segment(packet(wire[:9]))) == []
    assert streams.feed(tcp_segment(packet(wire[9:25], sequence=109))) == frames
    assert streams.feed(tcp_segment(packet(wire, sequence=100))) == []


def test_sequence_wraparound_and_reset_retire_old_buffer():
    frame = Frame(1, 2, 1, 4, b"payload")
    wire = frame.to_bytes()
    stream = Reassembler()
    start = 2**32 - 5
    assert stream.feed(tcp_segment(packet(wire[:5], sequence=start))) == []
    assert stream.feed(tcp_segment(packet(wire[5:], sequence=0))) == [frame]
    assert stream.feed(tcp_segment(packet(b"", sequence=20, flags=4))) == []
    assert not stream.streams


def test_bad_checksum_does_not_hide_the_next_frame():
    good = Frame(1, 6, 1, 4, b"payload")
    damaged = bytearray(good.to_bytes())
    damaged[-1] ^= 1
    stream = Reassembler()
    assert stream.feed(tcp_segment(packet(bytes(damaged) + good.to_bytes()))) == [good]


def test_stream_retention_is_bounded_and_idle_streams_expire():
    streams = Reassembler(maximum_flows=2, idle_seconds=10)
    for index in range(3):
        streams.feed(Segment(f"192.0.2.{index + 1}", "192.0.2.9", 4000, 5279, 1, 2, b""), now=index)
    assert len(streams.streams) == 2
    streams.feed(Segment("192.0.2.4", "192.0.2.9", 4000, 5279, 1, 2, b""), now=20)
    assert len(streams.streams) == 1
