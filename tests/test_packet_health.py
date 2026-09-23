import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ha_growatt.packet_health import PacketHealth, PrivateCapture, replay_capture
from ha_growatt.protocol import Frame, ProtocolError
from ha_growatt.telemetry import Decoder


def frame(size=100, function=4):
    return Frame(1, 6, 2, function, b"SECRET0001" + bytes(size))


def test_layout_failure_change_and_recovery_are_distinct_and_redacted():
    watcher = PacketHealth()
    good = SimpleNamespace(profile="mod-6", decode_errors=0)
    watcher.observe(frame(), good)
    watcher.observe(frame(200))
    assert watcher.export()[0]["failures"] == 1
    assert "could not" in watcher.export()[0]["state"]
    watcher.observe(frame(200), good)
    watcher.observe(frame(), good)
    row = watcher.export()[0]
    assert row["changes"] == 1
    assert row["state"].startswith("Decoding")
    watcher.observe(frame(300), SimpleNamespace(profile="private-path", decode_errors=0))
    assert "SECRET" not in json.dumps(watcher.export())
    assert "private-path" not in json.dumps(watcher.export())


def test_announcements_and_buffered_frames_do_not_change_layout_baseline():
    watcher = PacketHealth()
    for function in (3, 80, 22):
        watcher.observe(frame(function=function))
    assert watcher.export() == []


def test_private_capture_is_opt_in_bounded_and_expires(monkeypatch):
    clock = [100]
    monkeypatch.setattr("ha_growatt.packet_health.time.monotonic", lambda: clock[0])
    capture = PrivateCapture()
    capture.record(frame())
    assert capture.export()["frames"] == []
    capture.start()
    for _ in range(300):
        capture.record(frame())
    assert len(capture.export()["frames"]) == 256
    assert not capture.active
    assert Frame.from_bytes(bytes.fromhex(capture.export()["frames"][0])).payload.startswith(
        b"SECRET"
    )
    clock[0] += 1801
    assert capture.export()["frames"] == []


def test_private_capture_byte_limit_stop_and_clear():
    capture = PrivateCapture()
    capture.start()
    for _ in range(50):
        capture.record(frame(65000))
    assert capture.size <= 2 * 1024 * 1024
    assert not capture.active
    capture.clear()
    assert not capture.records
    capture.start()
    capture.record(frame())
    capture.stop()
    capture.record(frame())
    assert len(capture.records) == 1


def test_shareable_capture_keeps_structure_without_identity_or_readings():
    cases = json.loads((Path(__file__).parent / "fixtures/telemetry_cases.json").read_text())
    case = next(item for item in cases if item["profile"] == "classic-6")
    original = Frame.from_bytes(bytes.fromhex(case["wire"]))
    payload = bytearray(original.payload)
    payload[-16:] = b"PRIVATE_READINGS"
    capture = PrivateCapture()
    capture.start()
    capture.record(Frame(1, 6, original.unit, 4, bytes(payload)))
    capture.record(Frame(2, 6, original.unit, 4, bytes(payload)))

    shared = capture.export_shareable(Decoder("classic-6"))
    rows = shared["records"]
    assert shared["format"] == "ha-growatt-shareable-1"
    assert len(rows) == 2
    assert rows[0]["source"] == rows[1]["source"] == "Feed 1"
    assert rows[0]["result"] == "decoded"
    assert rows[0]["profile"] == "classic-6"
    assert rows[0]["selected_profile"] == "classic-6"
    assert rows[0]["payload_bytes"] == len(payload)
    assert isinstance(rows[0]["active_blocks"], list)
    assert shared["undecoded_layouts"] == []
    encoded = json.dumps(shared)
    assert "PRIVATE_READINGS" not in encoded
    assert payload[:10].decode() not in encoded
    assert original.to_bytes().hex() not in encoded
    assert "pvpowerout" not in encoded


def test_shareable_capture_does_not_copy_undecodable_or_expired_packets(monkeypatch):
    class Decoder:
        def decode(self, _frame):
            raise ProtocolError("PRIVATE_DIAGNOSIS")

    clock = [100.0]
    monkeypatch.setattr("ha_growatt.packet_health.time.monotonic", lambda: clock[0])
    capture = PrivateCapture()
    capture.start()
    capture.record(Frame(1, 6, 1, 3, b"LOGGER0001PRIVATE_ADDRESS"))
    shared = capture.export_shareable(Decoder())
    assert shared["records"][0]["result"] == "decode_failed"
    assert shared["undecoded_layouts"][0]["decode_issues"] == ["other"]
    assert "PRIVATE" not in json.dumps(shared)
    clock[0] += 1801
    assert capture.export_shareable(Decoder())["records"] == []
    assert capture.export_shareable(Decoder())["undecoded_layouts"] == []


def test_shareable_unknown_layouts_group_changes_without_leaking_private_bytes():
    class Decoder:
        def decode(self, _frame):
            raise ProtocolError("Frame does not match the selected telemetry profile")

    first = bytearray(128)
    first[:10] = b"LOGGER0001"
    secret = b"private@example.com SECRET1234567"
    first[32 : 32 + len(secret)] = secret
    second = bytearray(first)
    second[70:80] = b"READING123"
    capture = PrivateCapture()
    capture.start()
    capture.record(Frame(1, 6, 2, 4, bytes(first)))
    capture.record(Frame(2, 6, 2, 4, bytes(second)))

    shared = capture.export_shareable(Decoder())
    assert len(shared["undecoded_layouts"]) == 1
    layout = shared["undecoded_layouts"][0]
    assert layout["source"] == "Feed 1"
    assert layout["frames"] == 2
    assert layout["payload_bytes"] == 128
    assert layout["decode_issues"] == ["selected_profile_mismatch"]
    assert "classic-6" in layout["header_compatible_profiles"]
    assert layout["active_blocks"] == [0, 32, 64]
    assert layout["changing_blocks"] == [64]
    encoded = json.dumps(shared)
    for secret in ("LOGGER0001", "private@example.com", "SECRET1234567", "READING123"):
        assert secret not in encoded
        assert secret.encode().hex() not in encoded


def test_shareable_unknown_layout_bounds_block_offsets_and_error_text():
    class Decoder:
        def decode(self, _frame):
            raise ProtocolError("Private password and inverter serial: SECRET0001")

    payload = b"SECRET0001" + bytes([1]) * (32 * 90 - 10)
    capture = PrivateCapture()
    capture.start()
    capture.record(Frame(1, 6, 2, 4, payload))
    shared = capture.export_shareable(Decoder())
    row = shared["records"][0]
    layout = shared["undecoded_layouts"][0]
    assert len(row["active_blocks"]) == len(layout["active_blocks"]) == 64
    assert row["active_blocks_omitted"] == layout["active_blocks_omitted"] == 26
    assert layout["changing_blocks"] == []
    assert layout["decode_issues"] == ["other"]
    assert "SECRET" not in json.dumps(shared)


@pytest.mark.parametrize(
    ("failure", "category"),
    [
        ("No verified profile matches this frame", "no_verified_profile"),
        ("No verified family profile matches this frame", "no_verified_family_profile"),
        ("Telemetry does not meet the minimum layout score", "low_layout_score"),
    ],
)
def test_shareable_family_decoder_failures_use_only_safe_categories(failure, category):
    class Decoder:
        def decode(self, _frame):
            raise ProtocolError(failure)

    capture = PrivateCapture()
    capture.start()
    capture.record(frame())
    shared = capture.export_shareable(Decoder())
    assert shared["undecoded_layouts"][0]["decode_issues"] == [category]
    assert failure not in json.dumps(shared)


def test_serial_redacted_replay_keeps_known_numbers_and_replaces_other_bytes():
    cases = json.loads((Path(__file__).parent / "fixtures/telemetry_cases.json").read_text())
    case = next(item for item in cases if item["profile"] == "classic-6")
    original = Frame.from_bytes(bytes.fromhex(case["wire"]))
    payload = bytearray(original.payload)
    payload[:10] = b"PRIVLOG001"
    payload[30:40] = b"PRIVINV001"
    payload[-16:] = b"PRIVATE_READINGS"
    frame = Frame(19, 6, original.unit, 4, bytes(payload))
    capture = PrivateCapture()
    capture.start()
    capture.record(frame)
    capture.record(Frame(20, 6, original.unit, 3, bytes(payload)))
    exported = capture.export_serial_redacted(Decoder("classic-6"))
    assert exported["skipped"] == 1
    assert len(exported["frames"]) == 1
    clean = Frame.from_bytes(bytes.fromhex(exported["frames"][0]["wire"]))
    assert clean.transaction == 0
    assert clean.payload[:10] == b"LOGGER0001"
    assert clean.payload[30:40] == b"INVERT0001"
    assert clean.payload[60:66] == bytes((24, 1, 1, 12, 0, 0))
    assert b"PRIVATE_READINGS" not in clean.payload
    assert b"PRIVLOG001" not in clean.payload
    assert b"PRIVINV001" not in clean.payload
    assert (
        Decoder("classic-6").decode(clean).values["pvpowerout"]
        == (Decoder("classic-6").decode(frame).values["pvpowerout"])
    )
    assert replay_capture(exported, None)["frames"][0]["result"] == "decoded"


def test_serial_redacted_replay_skips_unknown_layouts():
    class Decoder:
        def decode(self, _frame):
            return SimpleNamespace(profile="custom:secret", values={}, decode_errors=0)

    capture = PrivateCapture()
    capture.start()
    capture.record(frame())
    exported = capture.export_serial_redacted(Decoder())
    assert exported["frames"] == []
    assert exported["skipped"] == 1


def test_serial_redacted_replay_scrubs_text_that_lands_in_numeric_positions():
    cases = json.loads((Path(__file__).parent / "fixtures/telemetry_cases.json").read_text())
    case = next(item for item in cases if item["profile"] == "classic-6")
    original = Frame.from_bytes(bytes.fromhex(case["wire"]))
    payload = bytearray(original.payload)
    payload[71:81] = b"SECRET0001"
    capture = PrivateCapture()
    capture.start()
    capture.record(Frame(1, 6, original.unit, 4, bytes(payload)))
    exported = capture.export_serial_redacted(Decoder("classic-6"))
    clean = Frame.from_bytes(bytes.fromhex(exported["frames"][0]["wire"]))
    assert b"SECRET0001" not in clean.payload


def test_serial_redacted_replay_keeps_two_feeds_distinct():
    cases = json.loads((Path(__file__).parent / "fixtures/telemetry_cases.json").read_text())
    case = next(item for item in cases if item["profile"] == "classic-6")
    original = Frame.from_bytes(bytes.fromhex(case["wire"]))
    capture = PrivateCapture()
    capture.start()
    for logger, inverter in (
        (b"PRIVLOG001", b"PRIVINV001"),
        (b"PRIVLOG002", b"PRIVINV002"),
        (b"PRIVLOG001", b"PRIVINV001"),
    ):
        payload = bytearray(original.payload)
        payload[:10] = logger
        payload[30:40] = inverter
        capture.record(Frame(1, 6, original.unit, 4, bytes(payload)))
    exported = capture.export_serial_redacted(Decoder("classic-6"))
    identities = [
        Frame.from_bytes(bytes.fromhex(item["wire"])).payload for item in exported["frames"]
    ]
    assert [item[:10] for item in identities] == [b"LOGGER0001", b"LOGGER0002", b"LOGGER0001"]
    assert [item[30:40] for item in identities] == [
        b"INVERT0001",
        b"INVERT0002",
        b"INVERT0001",
    ]


@pytest.mark.parametrize("profile", ["classic-2", "classic-5", "extended-6", "mod-6"])
def test_serial_redacted_replay_preserves_supported_protocols(profile):
    cases = json.loads((Path(__file__).parent / "fixtures/telemetry_cases.json").read_text())
    case = next(item for item in cases if item["profile"] == profile)
    original = Frame.from_bytes(bytes.fromhex(case["wire"]))
    capture = PrivateCapture()
    capture.start()
    capture.record(original)
    exported = capture.export_serial_redacted(Decoder(profile))
    assert exported["skipped"] == 0
    clean = Frame.from_bytes(bytes.fromhex(exported["frames"][0]["wire"]))
    assert clean.protocol == original.protocol
    assert clean.payload[:10] == b"LOGGER0001"
    assert (
        Decoder(profile).decode(clean).values["pvpowerout"]
        == (Decoder(profile).decode(original).values["pvpowerout"])
    )


def test_replay_decodes_without_exporting_readings_or_identity():
    class Decoder:
        def decode(self, packet):
            if len(packet.payload) > 150:
                raise ProtocolError("SECRET")
            return SimpleNamespace(values={"pvserial": "SECRET", "pvpowerout": 10}, decode_errors=0)

    capture = PrivateCapture()
    capture.start()
    capture.record(frame())
    capture.record(frame(200))
    report = replay_capture(capture.export(), Decoder())
    assert report == {
        "frames": [
            {"result": "decoded", "fields": 2, "incomplete_fields": 0},
            {"result": "decode_failed"},
        ],
        "writes": 0,
    }
    assert "SECRET" not in json.dumps(report)


@pytest.mark.parametrize(
    "data",
    [
        None,
        {},
        {"format": "ha-growatt-private-1", "frames": ["00"] * 257},
        {"format": "ha-growatt-private-1", "frames": [1]},
        {"format": "ha-growatt-serial-redacted-1", "frames": [{"profile": "private"}]},
    ],
)
def test_replay_rejects_unbounded_or_invalid_capture(data):
    with pytest.raises(ValueError):
        replay_capture(data, None)
