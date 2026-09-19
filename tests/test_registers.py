from datetime import datetime

import pytest

from ha_growatt.protocol import Frame, ProtocolError
from ha_growatt.registers import parse_register_report


def report_payload(ranges, stamp=bytes([26, 9, 19, 12, 30, 0])):
    payload = b"LOGGER0001".ljust(30, b"\x00") + b"INVERT0001".ljust(30, b"\x00")
    payload += stamp + bytes([len(ranges)])
    for start, words in ranges:
        payload += start.to_bytes(2, "big") + (start + len(words) - 1).to_bytes(2, "big")
        payload += b"".join(word.to_bytes(2, "big") for word in words)
    return payload


def decode(payload, function=4):
    return parse_register_report(Frame(1, 6, 1, function, payload))


@pytest.mark.parametrize("function,namespace", [(3, "holding"), (4, "input")])
def test_register_ranges_and_signed_values(function, namespace):
    report = decode(report_payload([(3000, [1, 65535, 65534]), (4000, [0, 12345])]), function)
    assert report.logger == "LOGGER0001"
    assert report.inverter == "INVERT0001"
    assert report.recorded_at == datetime(2026, 9, 19, 12, 30)
    assert report.namespace == namespace
    assert report.unsigned(4000, 2) == 12345
    assert report.signed(3001, 2) == -2
    assert report.signed(3001) == -1
    assert report.unsigned(3000) == 1
    with pytest.raises(KeyError):
        report.unsigned(3002, 2)
    with pytest.raises(ValueError):
        report.unsigned(3000, 3)
    with pytest.raises(TypeError):
        report.registers[3000] = 2


def test_invalid_clock_is_reported_without_fabricating_a_time():
    assert decode(report_payload([(0, [5])], stamp=bytes(6))).recorded_at is None


@pytest.mark.parametrize("offset", [0, 1, 29, 30, 59, 60, 65, 66, 67, 68, 70, 72])
def test_truncation_never_produces_partial_measurements(offset):
    payload = report_payload([(0, [5, 8])])
    with pytest.raises(ProtocolError):
        decode(payload[:offset])


def test_overlapping_ranges_are_rejected():
    with pytest.raises(ProtocolError, match="Overlapping"):
        decode(report_payload([(0, [1, 2]), (1, [3])]))


def test_unknown_trailing_data_is_rejected():
    with pytest.raises(ProtocolError, match="after"):
        decode(report_payload([(0, [1])]) + bytes(2))


def test_inverted_range_is_rejected():
    payload = bytearray(report_payload([(1, [5])]))
    payload[69:71] = bytes(2)
    with pytest.raises(ProtocolError, match="range"):
        decode(bytes(payload))


@pytest.mark.parametrize("identity", [bytes(30), b"a\x00b" + bytes(27), b"\xff" + bytes(29)])
def test_invalid_identity(identity):
    with pytest.raises(ProtocolError, match="[Ii]dentity"):
        decode(identity + report_payload([(0, [5])])[30:])


def test_empty_range_set_and_other_formats_are_not_guessed():
    with pytest.raises(ProtocolError, match="no ranges"):
        decode(report_payload([]))
    with pytest.raises(ProtocolError, match="not a version"):
        parse_register_report(Frame(1, 5, 1, 4, report_payload([(0, [1])])))
