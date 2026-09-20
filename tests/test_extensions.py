import asyncio
import json
import time
from datetime import datetime
from pathlib import Path

import pytest

from ha_growatt.extensions import ExtensionProcess
from ha_growatt.outputs import Extension, PublicationPolicy
from ha_growatt.pipeline import Pipeline
from ha_growatt.protocol import Frame
from ha_growatt.publisher import MqttSettings
from ha_growatt.relay import RelaySettings
from ha_growatt.runtime_options import RuntimeOptions
from ha_growatt.settings import Settings

MESSAGE = {
    "device": "INVERT0001",
    "time": "2026-09-20T10:30:00",
    "buffered": "no",
    "values": {
        "pvserial": "INVERT0001",
        "pvpowerout": 1234,
        "pvenergytoday": 20,
        "totworktime": 7200,
    },
}
CONTEXT = {
    "layout": "T06NNNN",
    "recorddict": {
        "T06NNNN": {
            "pvserial": {"divide": 1},
            "pvpowerout": {"divide": 10},
            "pvenergytoday": {"divide": 10},
            "totworktime": {"divide": 7200},
        }
    },
}


@pytest.mark.parametrize("custom", [False, True])
def test_csv_output_matches_observed_header_scaling_and_daily_path(tmp_path, custom):
    options = {"outpath": str(tmp_path)}
    if custom:
        options["csvheader"] = "device,time,pvpowerout,pvenergytoday,totworktime"
    extension = ExtensionProcess("grotcsv", options, CONTEXT)
    try:
        extension.publish(MESSAGE, "", CONTEXT)
        extension.publish(MESSAGE, "", CONTEXT)
    finally:
        extension.close()
    rows = (tmp_path / "2026-minute/20260920.csv").read_text().splitlines()
    assert len(rows) == 3
    assert rows[1] == rows[2]
    if custom:
        assert rows[0] == options["csvheader"]
        assert rows[1] == "INVERT0001,2026-09-20T10:30:00,123.4,2.0,1.0"
    else:
        assert rows[0] == "device,time,pvserial,pvpowerout,pvenergytoday,totworktime"
        assert rows[1] == "INVERT0001,2026-09-20T10:30:00,INVERT0001,123.4,2.0,1.0"


@pytest.mark.parametrize(
    "options,expected",
    [
        ({"ip": "127.0.0.1", "port": 8080}, "http://127.0.0.1:8080"),
        ({"url": "https://synthetic.invalid/test"}, "https://synthetic.invalid/test"),
    ],
)
def test_http_extension_preserves_encoded_json_string(options, expected, monkeypatch):
    from ha_growatt import outputs

    sent = []
    monkeypatch.setattr(outputs, "send_http", sent.append)
    Extension("grottext", options).publish(MESSAGE, "")
    assert sent[0].url == expected
    assert json.loads(sent[0].body) == json.dumps(MESSAGE)


def test_stuck_user_extension_has_a_bounded_lifetime(tmp_path, monkeypatch):
    (tmp_path / "stuck_extension.py").write_text(
        "import time\ndef grottext(conf, data, message):\n    time.sleep(60)\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    extension = ExtensionProcess("stuck_extension", {}, {}, timeout=0.3)
    start = time.monotonic()
    with pytest.raises(RuntimeError, match="delivery failed"):
        extension.publish(MESSAGE, "", {})
    extension.close()
    assert time.monotonic() - start < 5


@pytest.mark.parametrize(
    "case", json.loads((Path(__file__).parent / "fixtures/extension_packet_cases.json").read_text())
)
def test_observed_extension_packet_and_timestamp_policy(case, monkeypatch):
    from ha_growatt import outputs, pipeline

    sent = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 20, 9, 30, tzinfo=tz)

    class Capture:
        def __init__(self, *args):
            pass

        def publish(self, message, packet, context):
            sent.append((message, packet, context))

        def close(self):
            pass

    monkeypatch.setattr(outputs, "datetime", Clock)
    monkeypatch.setattr(pipeline, "ExtensionProcess", Capture)
    source = next(
        item
        for item in json.loads(
            (Path(__file__).parent / "fixtures/telemetry_cases.json").read_text()
        )
        if item["name"] == case["case"]
    )
    source_frame = Frame.from_bytes(bytes.fromhex(source["wire"]))
    frame = Frame(
        source_frame.transaction,
        source_frame.protocol,
        source_frame.unit,
        80 if case["buffered"] else 4,
        source_frame.payload,
    )
    settings = Settings(
        RelaySettings("unused.invalid"),
        MqttSettings("unused.invalid"),
        source["profile"],
        runtime=RuntimeOptions(
            home_assistant=False,
            policy=PublicationPolicy(case["time"], case["sendbuf"]),
            extension="capture",
        ),
    )

    async def scenario():
        service = Pipeline(settings)
        service.start()
        await service.observe("device", frame)
        await service.close()

    asyncio.run(scenario())
    assert sent[0][0] == case["message"]
    assert sent[0][1] == case["packet"]
    context = sent[0][2]
    assert context["recorddict"][context["layout"]]["pvpowerout"]["divide"] == 10
