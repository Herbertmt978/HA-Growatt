"""Pure freshness rules also run without installing Home Assistant."""

import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path

issues = runpy.run_path(str(Path(__file__).parents[1] / "custom_components/ha_growatt/health.py"))[
    "issues"
]
NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)
OPTIONS = {"daylight_alerts": True, "stale_minutes": 15, "sunrise_grace_minutes": 30}


def check(**changes):
    args = dict(
        now=NOW,
        started=NOW - timedelta(hours=1),
        daylight=True,
        sunrise=NOW - timedelta(hours=6),
        service={"online": True},
        service_seen=NOW,
        devices={"SYNTHETIC": {"last_record": (NOW - timedelta(hours=8)).isoformat()}},
        options=OPTIONS,
    )
    return issues(**(args | changes))


def test_daylight_stale_and_fresh_feeds():
    assert "feed_SYNTHETIC" in check()
    assert not check(devices={"SYNTHETIC": {"last_record": NOW.isoformat()}})
    assert not check(daylight=False)
    assert not check(sunrise=NOW - timedelta(minutes=29))
    assert not check(started=NOW - timedelta(seconds=119))
    assert not check(options=OPTIONS | {"daylight_alerts": False})


def test_offline_service_is_separate_even_at_night():
    assert set(check(daylight=False, service={"online": False})) == {"service_offline"}
    assert set(check(service_seen=NOW - timedelta(seconds=61))) == {"service_offline"}
    assert set(check(service_seen=None)) == {"service_offline"}


def test_missing_status_waits_for_daylight_and_sunrise_grace():
    for status in ({}, {"online": True}):
        for seen in (None, NOW - timedelta(hours=8)):
            assert not check(daylight=False, service=status, service_seen=seen)
            assert not check(sunrise=NOW - timedelta(minutes=29), service=status, service_seen=seen)
            assert set(
                check(sunrise=NOW - timedelta(minutes=30), service=status, service_seen=seen)
            ) == {"service_offline"}
    assert set(check(service={"online": False}, sunrise=NOW - timedelta(minutes=5))) == {
        "service_offline"
    }


def test_missing_status_recovers_and_still_warns_when_feed_alerts_are_disabled():
    assert set(check(options=OPTIONS | {"daylight_alerts": False}, service_seen=None)) == {
        "service_offline"
    }
    assert not check(daylight=False, service_seen=NOW)
    assert set(check(service_seen=NOW)) == {"feed_SYNTHETIC"}


def test_bad_future_and_missing_timestamps_are_never_fresh():
    for stamp in (
        "not a date",
        "2026-09-20T12:00:00",
        (NOW + timedelta(hours=1)).isoformat(),
        None,
    ):
        assert "feed_SYNTHETIC" in check(devices={"SYNTHETIC": {"last_record": stamp}})


def test_decode_issue_does_not_count_announcements_or_hide_service_failure():
    assert not check(
        daylight=False, service={"online": True, "observations": {"announcement_warnings": 9}}
    )
    assert set(
        check(daylight=False, service={"online": True, "observations": {"failed_measurements": 2}})
    ) == {"decode_failed"}
    assert not check(
        daylight=False,
        service={"online": True, "observations": {"failed_measurements": 2, "measurements": 5}},
    )
