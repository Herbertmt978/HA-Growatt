"""Evaluate feed freshness without changing measurement states."""

from datetime import datetime, timedelta


def issues(now, started, daylight, sunrise, service, service_seen, devices, options):
    if now - started < timedelta(minutes=2):
        return {}
    if service.get("online") is False:
        return {"service_offline": ("service_offline", {})}
    daylight_ready = daylight and not (
        sunrise and now - sunrise < timedelta(minutes=options["sunrise_grace_minutes"])
    )
    if service_seen is None or now - service_seen > timedelta(seconds=60):
        return {"service_offline": ("service_offline", {})} if daylight_ready else {}
    result = {}
    counters = service.get("observations", {})
    if counters.get("failed_measurements", 0) and not counters.get("measurements", 0):
        result["decode_failed"] = ("decode_failed", {})
    if not options["daylight_alerts"] or not daylight_ready:
        return result
    for identity, data in devices.items():
        try:
            stamp = datetime.fromisoformat(data["last_record"])
            if stamp.tzinfo is None:
                raise ValueError("Missing timezone")
            recent = (
                timedelta(seconds=-60) <= now - stamp <= timedelta(minutes=options["stale_minutes"])
            )
        except (KeyError, TypeError, ValueError):
            recent = False
        if not recent:
            result["feed_" + identity] = (
                "feed_stale",
                {"inverter": identity, "minutes": str(options["stale_minutes"])},
            )
    return result
