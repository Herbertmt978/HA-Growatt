"""Native HA diagnostic download with an explicit allowlist."""


async def async_get_config_entry_diagnostics(hass, entry):
    service = entry.runtime_data
    if entry.data.get("mode") == "direct":
        receiver = service.receiver
        return {
            "version": 2,
            "mode": "direct",
            "inverters": len(receiver.snapshots),
            "receiver_running": receiver.running,
            "cloud_forwarding": receiver.forward_cloud,
            "observations": {
                "announcements": receiver.announcements,
                "announcement_warnings": receiver.announcement_warnings,
                "measurements": receiver.measurements,
                "failed_measurements": receiver.failed_measurements,
                "incomplete_fields": receiver.incomplete_fields,
                "buffered_records": receiver.buffered_records,
            },
            "restart_recovery_available": not receiver.cache_error,
            "active_issues": sorted({value[0] for value in service.current_issues.values()}),
            "daylight_alerts": service.options["daylight_alerts"],
            "stale_minutes": service.options["stale_minutes"],
            "sunrise_grace_minutes": service.options["sunrise_grace_minutes"],
            "buffered_events": service.options["buffered_events"],
        }
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
