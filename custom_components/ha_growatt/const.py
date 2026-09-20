"""Home Assistant companion settings."""

DOMAIN = "ha_growatt"
DEFAULTS = {
    "daylight_alerts": True,
    "stale_minutes": 15,
    "sunrise_grace_minutes": 30,
    "buffered_events": True,
}
GUIDE = "https://github.com/Herbertmt978/HA-Growatt/blob/main/docs/home-assistant-features.md"
