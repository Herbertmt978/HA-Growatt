import json
from pathlib import Path

import pytest
from jinja2 import Environment

from ha_growatt.discovery import discovery_messages
from ha_growatt.protocol import Frame
from ha_growatt.telemetry import Decoder

CASES = json.loads((Path(__file__).parent / "fixtures" / "extra_telemetry_cases.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["name"])
def test_additional_observed_layouts_and_discovery(case):
    telemetry = Decoder(case["profile"], include_all=case["include_all"]).decode(
        Frame.from_bytes(bytes.fromhex(case["wire"]))
    )
    assert telemetry.values == case["expected"]
    configs = discovery_messages(
        telemetry.values["pvserial"],
        profile="all",
        wire_profile=case["profile"],
        include_all=case["include_all"],
    )
    for config in configs.values():
        rendered = (
            Environment()
            .from_string(config["value_template"])
            .render(value_json=telemetry.values | {"grott_last_push": "2026-09-20T10:00:00+00:00"})
        )
        assert rendered.strip()
