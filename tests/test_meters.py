import json
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qs

import pytest

from ha_growatt.discovery import discovery_messages
from ha_growatt.outputs import PublicationPolicy, PVOutput, PVOutputSettings
from ha_growatt.protocol import Frame
from ha_growatt.selection import FamilyDecoder, SelectionSettings
from ha_growatt.telemetry import Decoder

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("case", json.loads((FIXTURES / "csv_meter_cases.json").read_text()))
def test_csv_meter_values_polarity_and_home_assistant_identity(case):
    telemetry = FamilyDecoder(SelectionSettings()).decode(
        Frame.from_bytes(bytes.fromhex(case["wire"]))
    )
    assert telemetry.values == case["expected"]
    assert telemetry.device_id == "SDM630"
    configs = discovery_messages(telemetry.device_id, wire_profile=telemetry.profile)
    assert len(configs) == 45
    assert all(
        config["state_topic"] == "homeassistant/grott/SDM630/state" for config in configs.values()
    )


def test_binary_meter_timestamp_identity_and_two_pvoutput_updates():
    case = next(
        c
        for c in json.loads((FIXTURES / "extra_telemetry_cases.json").read_text())
        if c["profile"] == "meter-6" and not c["include_all"]
    )
    telemetry = Decoder("meter-6").decode(Frame.from_bytes(bytes.fromhex(case["wire"])))
    message = PublicationPolicy().message(telemetry, datetime(2026, 9, 20, 9, 30))
    assert message["device"] == "LOGGER0001"
    assert message["time"] == "2026-09-20T09:30:00"
    writes = PVOutput(PVOutputSettings("synthetic", default_system="12345")).requests(message)
    assert [parse_qs(write.body.decode()) for write in writes] == [
        {"d": ["20260920"], "t": ["09:30"], "v3": ["4100"], "c1": ["3"], "v6": ["230.0"]},
        {"d": ["20260920"], "t": ["09:30"], "v4": ["1.3"], "v6": ["230.0"], "n": ["1"]},
    ]
