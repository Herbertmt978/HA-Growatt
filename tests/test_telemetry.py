import json
from pathlib import Path

import pytest

from ha_growatt.protocol import Frame, ProtocolError
from ha_growatt.telemetry import Decoder

CASES = json.loads((Path(__file__).parent / "fixtures" / "telemetry_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
@pytest.mark.parametrize("function", [3, 4, 80])
def test_independently_observed_scalar_outputs(case, function):
    frame = Frame.from_bytes(bytes.fromhex(case["wire"]))
    frame = Frame(frame.transaction, frame.protocol, frame.unit, function, frame.payload)
    decoded = Decoder(case["profile"], include_all=case["include_all"]).decode(frame)
    assert decoded.values == case["expected"]
    assert decoded.recorded_at.isoformat() == "2026-09-19T12:34:56"
    assert decoded.buffered == (function == 80)


def test_wrong_protocol_and_truncated_identity_do_not_publish_guessed_values():
    frame = Frame.from_bytes(bytes.fromhex(CASES[0]["wire"]))
    decoder = Decoder(CASES[0]["profile"])
    for changed in [
        Frame(1, 5, 1, 4, frame.payload),
        Frame(1, 6, 1, 2, frame.payload),
        Frame(1, 6, 1, 4, frame.payload[:30]),
    ]:
        with pytest.raises(ProtocolError):
            decoder.decode(changed)


def test_unsafe_identity_is_not_an_mqtt_topic():
    frame = Frame.from_bytes(bytes.fromhex(CASES[0]["wire"]))
    payload = bytearray(frame.payload)
    payload[30:40] = b"bad/topic!"
    with pytest.raises(ProtocolError, match="identity"):
        Decoder(CASES[0]["profile"]).decode(Frame(1, 6, 1, 4, bytes(payload)))


def test_unknown_profile_is_rejected():
    with pytest.raises(ValueError, match="profile"):
        Decoder("unverified")


def test_default_decoder_accepts_different_verified_packet_sizes_per_device():
    decoder = Decoder("auto")
    for case in CASES:
        if case["include_all"]:
            continue
        if case["profile"] in {"classic-2", "classic-5", "classic-6", "extended-6"}:
            result = decoder.decode(Frame.from_bytes(bytes.fromhex(case["wire"])))
            assert result.values == case["expected"]
            assert result.profile == case["profile"]
    with pytest.raises(ProtocolError, match="identity"):
        decoder.decode(Frame(1, 6, 1, 4, bytes(300)))
