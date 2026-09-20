import asyncio
import json
from dataclasses import replace

import pytest

from ha_growatt.diagnostics import SupportCapture
from ha_growatt.hardware import read_firmware
from ha_growatt.legacy_config import addon_options
from ha_growatt.pipeline import Pipeline
from ha_growatt.protocol import Frame, ProtocolError
from ha_growatt.publisher import MqttSettings
from ha_growatt.relay import RelaySettings
from ha_growatt.runtime_options import RuntimeOptions
from ha_growatt.settings import Settings
from ha_growatt.telemetry import Telemetry


def test_announcements_and_measurement_failures_have_separate_counters():
    async def scenario():
        pipeline = Pipeline(Settings(RelaySettings("localhost"), MqttSettings("localhost"), "auto"))

        class Decoder:
            def decode(self, _):
                raise ProtocolError("Unsupported packet")

        pipeline.decoder = Decoder()
        pipeline.capture.start()
        for function in (3, 4, 80, 22):
            await pipeline.observe("device", Frame(1, 6, 1, function, b"SECRET0001" + bytes(100)))
        assert pipeline.observations.announcement_warnings == 1
        assert pipeline.observations.failed_measurements == 2
        assert pipeline.observations.measurements == 0
        encoded = json.dumps(pipeline.capture.export())
        assert "SECRET0001" not in encoded
        assert "announcement_not_measurement" in encoded

    asyncio.run(scenario())


def test_capture_is_opt_in_bounded_expires_and_excludes_custom_text(monkeypatch):
    capture = SupportCapture()
    frame = Frame(1, 6, 1, 4, b"SECRET0001private address password")
    telemetry = Telemetry(
        {"pvserial": "PRIVATE001", "pvpowerout": 432199}, None, "custom:private-path"
    )
    capture.record(frame, "decoded", telemetry)
    assert not capture.export()["records"]
    capture.start()
    for _ in range(300):
        capture.record(frame, "decoded", telemetry)
    assert len(capture.export()["records"]) == 256
    encoded = json.dumps(capture.export())
    for text in ("SECRET0001", "PRIVATE001", "432199", "private-path", "password"):
        assert text not in encoded
    monkeypatch.setattr("ha_growatt.diagnostics.time.monotonic", lambda: capture._until + 1)
    capture.record(frame, "ignored")
    assert capture.export()["records"][-1]["result"] == "decoded"
    capture.start()
    assert not capture.export()["records"]
    capture.stop()
    assert not capture.active


@pytest.mark.parametrize(
    "raw, expected",
    [(b"GH1.02GH2.01", "GH1.02 / GH2.01"), (b"GH1.02GH1.02", "GH1.02"), (b"\0" * 12, None)],
)
def test_firmware_uses_only_documented_read_registers(raw, expected):
    class Transport:
        async def command(self, identity, function, body):
            assert identity == "SYNTHETIC" and function == 5 and body == b"\0\x09\0\x0e"
            return Frame(1, 6, 1, 5, bytes(30) + body + raw)

    if expected:
        assert asyncio.run(read_firmware(Transport(), "SYNTHETIC")) == expected
    else:
        with pytest.raises(ValueError):
            asyncio.run(read_firmware(Transport(), "SYNTHETIC"))


def test_app_hardware_and_refresh_options_validate_without_changing_defaults():
    relay, _, _, runtime = addon_options(
        {
            "cloud_recovery_seconds": 0,
            "settings_refresh_seconds": 0,
            "inverters": [{"serial": "SYNTHETIC", "model": "MIN 3000TL-X", "firmware": "GH1.02"}],
        }
    )
    assert relay.cloud_recovery_seconds == 0 and runtime.settings_refresh_seconds == 0
    assert runtime.hardware["SYNTHETIC"]["model"] == "MIN 3000TL-X"
    for seconds in (-1, 1, 29, True, 86401):
        with pytest.raises(ValueError):
            replace(runtime, settings_refresh_seconds=seconds)
    with pytest.raises(ValueError):
        RuntimeOptions(hardware={"SYNTHETIC": {"model": "bad\ntext"}})


def test_hardware_details_reach_same_mqtt_device():
    from ha_growatt.ha_features import Device, feature_discovery

    messages = feature_discovery(
        Device("SYNTHETIC", "extended-6", 0, ""),
        True,
        hardware={"model": "MIN 3000TL-X", "firmware": "GH1.02"},
    )
    for message in messages.values():
        assert message["device"]["identifiers"] == ["SYNTHETIC"]
        assert message["device"]["model"] == "MIN 3000TL-X"
        assert message["device"]["sw_version"] == "GH1.02"
