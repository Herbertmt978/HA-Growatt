import asyncio
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import pytest

from ha_growatt.compat import CompatibilityDecoder
from ha_growatt.health import clear_health, healthy, write_health
from ha_growatt.pipeline import Pipeline
from ha_growatt.protocol import Frame, ProtocolError
from ha_growatt.publisher import MqttSettings
from ha_growatt.relay import RelaySettings
from ha_growatt.runtime_options import RuntimeOptions
from ha_growatt.settings import Settings


def test_passive_health_lifecycle(tmp_path):
    path = tmp_path / "health"
    assert not healthy(path)
    write_health(path, "proxy")
    assert healthy(path)
    record = json.loads(path.read_text())
    path.write_text(json.dumps(record | {"updated": time.time() - 31}))
    assert not healthy(path)
    write_health(path, "proxy")
    clear_health(path)
    assert not path.exists()


@pytest.mark.parametrize(
    "value",
    [
        None,
        [],
        {},
        {"pid": -1},
        {"pid": "1"},
        {"pid": os.getpid(), "updated": "now"},
        {"pid": os.getpid(), "updated": 10**15},
    ],
)
def test_invalid_health_is_not_alive(tmp_path, value):
    path = tmp_path / "health"
    path.write_text(json.dumps(value))
    assert not healthy(path)
    clear_health(path)


@pytest.mark.parametrize("offset", [6, 8])
@pytest.mark.parametrize("protocol,decrypt", [(2, False), (5, True), (6, True)])
def test_serial_relative_record_matches_measured_positions(offset, protocol, decrypt):
    payload = bytearray(b"LOGGER0001" + bytes(20) + b"INVERT0001" + bytes(250))
    origin = 30 + offset
    payload[origin + 15 : origin + 17] = b"\x00\x01"
    payload[origin + 37 : origin + 41] = (25410).to_bytes(4, "big")
    payload[origin + 41 : origin + 43] = (5000).to_bytes(2, "big")
    payload[origin + 71 : origin + 75] = (89301).to_bytes(4, "big")
    frame = Frame(1, protocol, 1, 4, bytes(payload))
    reading = CompatibilityDecoder("INVERT0001", offset, decrypt).decode(frame)
    assert reading.values["pvpowerout"] == 25410
    assert reading.values["pvfrequentie"] == 5000
    assert reading.values["pvenergytotal"] == 89301
    assert reading.sensor_metadata["pvpowerout"]["divisor"] == 10
    with pytest.raises(ProtocolError):
        CompatibilityDecoder("INVERT0002", offset, decrypt).decode(frame)
    with pytest.raises(ProtocolError):
        CompatibilityDecoder("INVERT0001", offset, decrypt).decode(
            replace(frame, payload=frame.payload[:50])
        )


def test_slow_and_failing_outputs_do_not_block_other_outputs():
    async def scenario():
        settings = Settings(
            RelaySettings("unused.invalid", queue_size=2),
            MqttSettings("unused.invalid"),
            "classic-6",
            runtime=RuntimeOptions(home_assistant=False),
        )
        pipeline = Pipeline(settings)
        entered, release, delivered = asyncio.Event(), asyncio.Event(), []

        async def slow(reading):
            entered.set()
            await release.wait()

        async def broken(reading):
            raise OSError("synthetic")

        async def working(reading):
            delivered.append(reading.message)

        pipeline._outputs = {"slow": slow, "broken": broken, "working": working}
        case = next(
            case
            for case in json.loads(
                (Path(__file__).parent / "fixtures/telemetry_cases.json").read_text()
            )
            if case["profile"] == "classic-6"
        )
        frame = Frame.from_bytes(bytes.fromhex(case["wire"]))
        pipeline.start()
        try:
            await pipeline.observe("device", frame)
            await asyncio.wait_for(entered.wait(), 1)
            for _ in range(5):
                await pipeline.observe("device", frame)
                await asyncio.sleep(0)
            assert len(delivered) == 6
            assert pipeline.failures == 6
            assert pipeline.dropped == 3
        finally:
            release.set()
            await pipeline.close()
        assert all(worker.done() for worker in pipeline._workers)

    asyncio.run(scenario())
