"""Native HA diagnostic download with an explicit allowlist."""

from homeassistant.util import dt as dt_util

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    service = entry.runtime_data
    if entry.data.get("mode") == "modbus":
        receiver = service.receiver
        return {
            "version": 1,
            "mode": "modbus",
            "profile": service.receiver.profile,
            "receiver_running": receiver.running,
            "gateway_connected": receiver.connected,
            "inverters": len(receiver.snapshots),
            "observations": {
                "measurements": receiver.measurements,
                "failed_measurements": receiver.failed_measurements,
            },
            "last_error": receiver.last_error,
            "restart_recovery_available": not receiver.cache_error,
            "active_issues": sorted({value[0] for value in service.current_issues.values()}),
            "daylight_alerts": service.options["daylight_alerts"],
        }
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
            "packet_health": receiver.packet_health.export(),
            "controls_enabled": service.controls.enabled,
            "controls_available": sum(
                len(service.controls.controls(identity)) for identity in service.controls.states
            ),
            "optional_outputs": service.outputs.status(),
            "optional_output_rejections": service.outputs.rejected,
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


async def async_get_device_diagnostics(hass, entry, device):
    """Explain a selected inverter without exporting its serial or readings."""
    if entry.data.get("mode") not in {"direct", "modbus"}:
        return await async_get_config_entry_diagnostics(hass, entry)
    service = entry.runtime_data
    receiver = service.receiver
    mode = entry.data["mode"]
    prefix = "ha_growatt_modbus_"
    identity = None
    for domain, value in device.identifiers:
        if domain != DOMAIN:
            continue
        if mode == "direct" and value in receiver.snapshots:
            identity = value
        elif mode == "modbus" and value.startswith(prefix):
            possible = value.removeprefix(prefix)
            if possible in receiver.snapshots:
                identity = possible
    if identity is None:
        return {"mode": mode, "reading_available": False}
    snapshot = receiver.snapshots[identity]
    age = max(0, int((dt_util.utcnow() - snapshot.received_at).total_seconds()))
    result = {
        "mode": mode,
        "profile": snapshot.telemetry.profile,
        "reading_available": True,
        "reading_age_seconds": age,
        "fields_received": len(snapshot.telemetry.values),
        "connection_live": (
            receiver.connected
            if mode == "modbus"
            else bool(
                receiver.control_transport
                and receiver.control_transport.session_key(identity) is not None
            )
        ),
    }
    if mode == "direct":
        result["controls_available"] = [
            control.key for control in service.controls.controls(identity)
        ]
        result["schedules_available"] = list(service.controls.periods(identity))
    else:
        result["last_error"] = receiver.last_error
    return result
