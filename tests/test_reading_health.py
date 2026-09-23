import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from test_ha_features import features

from ha_growatt.reading_health import clock_health, reading_health
from ha_growatt.telemetry import Telemetry


@pytest.mark.parametrize("month,local_hour", [(1, 12), (7, 13)])
def test_clock_uses_configured_zone_in_winter_and_summer(month, local_hour):
    result = clock_health(
        datetime(2026, month, 10, local_hour),
        "Europe/London",
        now=datetime(2026, month, 10, 12, tzinfo=UTC),
    )
    assert result == {
        "reference": "Europe/London",
        "offset_seconds": 0,
        "state": "Within two minutes",
    }


@pytest.mark.parametrize(
    "seconds,state",
    [
        (120, "Within"),
        (-121, "Reported"),
        (3600, "Possible"),
        (-3600, "Possible"),
        (7200, "Reported"),
    ],
)
def test_clock_thresholds_do_not_assert_daylight_saving_as_cause(seconds, state):
    now = datetime(2026, 1, 10, 12, tzinfo=UTC)
    result = clock_health(now.replace(tzinfo=None) + timedelta(seconds=seconds), "UTC", now=now)
    assert result["state"].startswith(state)
    assert result["offset_seconds"] == seconds


def test_missing_clock_does_not_fabricate_drift():
    assert clock_health(None)["offset_seconds"] is None


def test_tlx_mode_bits_and_main_fault_are_distinct_from_battery_fault():
    result = reading_health(
        {"pvstatus": 0x501, "faultmaincode": 302, "faultcode": 203}, "mod-6", "MIC TL-X"
    )
    assert result["operating_state"] == "Operating normally"
    assert result["fault_description"] == "No AC connection (code 302)"
    assert reading_health({"faultcode": 203}, "mod-6", "MIC TL-X")["fault_code"] is None


def test_unknown_family_code_and_storage_bitfields_are_not_guessed():
    result = reading_health({"pvstatus": 999, "faultcode": 203}, "min-6", "Unconfirmed")
    assert result["operating_state"] == "Unmapped state 999"
    assert result["fault_description"] == "Unmapped fault code 203"
    assert reading_health({"faultcode": 203}, "sph-6", "Unconfirmed")["fault_code"] is None


def test_overnight_status_waits_for_fresh_evidence():
    async def run():
        service = features()
        service.remember(
            Telemetry({"pvserial": "INVERT0001", "pvstatus": 3}, datetime(2020, 1, 1), "mod-6")
        )
        device = service.devices["INVERT0001"]
        assert service.reading_diagnostics(device)["clock_status"] == "Reported clock differs"
        device.last_seen -= 901
        result = service.reading_diagnostics(device)
        assert result["clock_status"] == "Waiting for fresh readings"
        assert result["clock"]["offset_seconds"] is None
        assert result["operating_state"] == "Waiting for fresh readings"

    asyncio.run(run())
