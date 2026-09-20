"""Native HA diagnostic download with an explicit allowlist."""


async def async_get_config_entry_diagnostics(hass, entry):
    service = entry.runtime_data
    return {
        "version": 1,
        "inverters": len(service.devices),
        "app_online": service.service.get("online", False),
        "observations": service.service.get("observations", {}),
        "active_issues": sorted({value[0] for value in service.current_issues.values()}),
        "daylight_alerts": service.options["daylight_alerts"],
        "stale_minutes": service.options["stale_minutes"],
        "sunrise_grace_minutes": service.options["sunrise_grace_minutes"],
        "buffered_events": service.options["buffered_events"],
    }
