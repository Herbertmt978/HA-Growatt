"""Privacy and size limits for optional unknown Shine diagnostics."""

import asyncio
import json
from pathlib import Path

import pytest

from ha_growatt.native_receiver import NativeReceiver
from ha_growatt.protocol import Frame, ProtocolError
from ha_growatt.registers import parse_register_report
from ha_growatt.selection import FamilyDecoder, SelectionSettings
from ha_growatt.telemetry import Decoder, Telemetry
from ha_growatt.unknown_shine import MAX_FORMATS, UnknownShineFormats


def test_unknown_formats_contain_only_allowlisted_packet_shape_fields():
    tracker = UnknownShineFormats()
    secret = b"LOGGER0001 PRIVATE001 password=do-not-publish"
    frame = Frame(123, 6, 1, 4, secret)
    tracker.observe(frame)
    tracker.observe(frame)
    tracker.observe(
        frame,
        Telemetry(
            {"pvserial": "PRIVATE001", "pvpowerout": 123456},
            None,
            "custom:private-profile",
            decode_errors=2,
        ),
    )
    tracker.observe(frame, Telemetry({"pvserial": "PRIVATE001"}, None, "classic-6"))
    result = tracker.export()
    assert result["total_frames"] == 3
    assert result["formats"][0] == {
        "result": "decode_failed",
        "protocol": 6,
        "function": 4,
        "payload_bytes": len(secret),
        "profile": None,
        "frames": 2,
    }
    assert result["formats"][1]["profile"] == "custom"
    encoded = json.dumps(result)
    for private in ("LOGGER0001", "PRIVATE001", "password", "123456", "private-profile"):
        assert private not in encoded


def test_unknown_formats_are_bounded_even_with_many_shapes():
    tracker = UnknownShineFormats()
    for size in range(1, 30):
        tracker.observe(Frame(1, 6, 1, 4, bytes(size)))
    result = tracker.export()
    assert len(result["formats"]) == MAX_FORMATS
    assert result["other_frames"] == 29 - MAX_FORMATS
    assert result["total_frames"] == 29
    assert len(json.dumps(result)) < 2_000


def test_native_receiver_records_unknown_shapes_only_when_enabled():
    fixture = json.loads((Path(__file__).parent / "fixtures/telemetry_cases.json").read_text())
    source = Frame.from_bytes(
        bytes.fromhex(
            next(
                case["wire"]
                for case in fixture
                if case["profile"] == "classic-6" and not case["include_all"]
            )
        )
    )
    payload = bytearray(source.payload)
    payload[30:40] = b"bad/topic!"
    failed = Frame(source.transaction, source.protocol, source.unit, 4, bytes(payload))
    incomplete = Frame(source.transaction, source.protocol, source.unit, 4, source.payload[:180])

    async def scenario():
        normal = NativeReceiver()
        assert normal.unknown_formats is None
        await normal.observe("device", failed)
        assert normal.failed_measurements == 1

        receiver = NativeReceiver(unknown_diagnostics=True)
        await receiver.observe("cloud", failed)
        await receiver.observe("device", source)
        assert receiver.unknown_formats.export()["total_frames"] == 0
        await receiver.observe("device", failed)
        await receiver.observe("device", incomplete)
        report = receiver.unknown_formats.export()
        assert report["total_frames"] == 2
        assert {row["result"] for row in report["formats"]} == {
            "decode_failed",
            "incomplete_fields",
        }
        assert receiver.measurements == 2
        assert receiver.failed_measurements == 1
        assert "bad/topic!" not in json.dumps(report)
        assert "INVERT0001" not in json.dumps(report)

    asyncio.run(scenario())


@pytest.mark.parametrize("profile", ["classic-6", "mod-6", "min-6"])
def test_scalar_shine_fixtures_do_not_declare_unknown_register_addresses(profile):
    cases = json.loads((Path(__file__).parent / "fixtures/telemetry_cases.json").read_text())
    case = next(item for item in cases if item["profile"] == profile and not item["include_all"])
    frame = Frame.from_bytes(bytes.fromhex(case["wire"]))
    assert Decoder(profile).decode(frame).profile == profile
    with pytest.raises(ProtocolError, match="no ranges"):
        parse_register_report(frame)


def test_unassigned_scalar_bytes_can_hold_private_text_in_an_otherwise_valid_reading():
    cases = json.loads((Path(__file__).parent / "fixtures/telemetry_cases.json").read_text())
    case = next(
        item for item in cases if item["profile"] == "classic-6" and not item["include_all"]
    )
    source = Frame.from_bytes(bytes.fromhex(case["wire"]))
    payload = bytearray(source.payload)
    # These twelve bytes are outside every classic-6 numeric field. Their
    # contents cannot be treated as an anonymous numerical measurement.
    payload[159:171] = b"PRIVATEPASS1"
    frame = Frame(source.transaction, source.protocol, source.unit, source.function, bytes(payload))
    decoded = FamilyDecoder(SelectionSettings()).decode(frame)
    assert decoded.profile == "classic-6" and decoded.decode_errors == 0

    async def scenario():
        receiver = NativeReceiver(unknown_diagnostics=True)
        await receiver.observe("device", frame)
        assert receiver.measurements == 1
        assert receiver.unknown_formats.export()["total_frames"] == 0
        assert "PRIVATEPASS1" not in json.dumps(receiver.unknown_formats.export())

    asyncio.run(scenario())
