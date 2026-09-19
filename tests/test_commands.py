import json
from pathlib import Path

import pytest

from ha_growatt.commands import may_forward
from ha_growatt.protocol import Frame

OBSERVED = json.loads((Path(__file__).parent / "fixtures" / "command_policy.json").read_text())


@pytest.mark.parametrize("case", OBSERVED["records"])
def test_all_function_codes_match_observed_forwarding_policy(case):
    for function in range(256):
        frame = Frame(1, case["protocol"], case["unit"], function, bytes(50))
        assert may_forward(frame.to_bytes(), frame) == (function in case["allowed_functions"])


@pytest.mark.parametrize("case", OBSERVED["exemptions"])
def test_time_and_destination_exemptions_require_verified_payloads(case):
    payload = bytearray(50)
    offset = 30 if case["protocol"] == 6 else 10
    payload[offset : offset + 2] = case["command"].to_bytes(2, "big")
    frame = Frame(1, case["protocol"], 1, 24, bytes(payload))
    assert (
        may_forward(
            frame.to_bytes(),
            None if case["corrupt"] else frame,
            allow_destination_change=case["allow_destination"],
        )
        == case["forwarded"]
    )


def test_explicit_allowlist_does_not_enable_unrelated_records():
    frame = Frame(1, 6, 1, 24, bytes(50))
    assert may_forward(frame.to_bytes(), frame, permitted=frozenset({0x0118}))
    assert not may_forward(frame.to_bytes(), frame, permitted=frozenset({0x0218}))
