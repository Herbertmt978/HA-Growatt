"""Observations about fresh readings; never adjust a device or its timestamps."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo


def clock_health(recorded_at, timezone="local", *, now=None):
    now = now or datetime.now(UTC)
    reference = now.astimezone() if timezone == "local" else now.astimezone(ZoneInfo(timezone))
    label = f"Service local time ({reference.tzname()})" if timezone == "local" else timezone
    result = {"reference": label, "offset_seconds": None, "state": "Not reported"}
    if recorded_at is None:
        return result
    # Packet timestamps have no UTC offset. Compare wall clocks, without guessing
    # which side of an autumn clock change the datalogger intended.
    seconds = round(
        (recorded_at.replace(tzinfo=None) - reference.replace(tzinfo=None)).total_seconds()
    )
    result["offset_seconds"] = seconds
    magnitude = abs(seconds)
    result["state"] = (
        "Within two minutes"
        if magnitude <= 120
        else "Possible time zone or daylight-saving offset"
        if abs(magnitude - 3600) <= 120
        else "Reported clock differs"
    )
    return result


# MIC TL-X manual, June 2023, section 11.2. Other families can assign the
# same number differently, so unknown models retain an explicit numeric code.
MIC_FAULTS = {
    201: "Residual current is high",
    202: "PV voltage is too high",
    203: "Insulation fault",
    300: "Grid voltage is outside its range",
    302: "No AC connection",
    303: "Neutral-to-earth voltage is abnormal",
    304: "Grid frequency is outside its range",
    407: "Self-test failed",
}


def reading_health(values, profile, family):
    raw = values.get("pvstatus")
    state = "Not reported"
    if type(raw) is int:
        state = f"Unmapped state {raw}"
        if family in {"MIC TL-X", "MIN TL-XH"} and profile in {"mod-6", "min-6"}:
            state = {0: "Standby", 1: "Operating normally", 3: "Fault", 4: "Firmware update"}.get(
                raw & 255, state
            )
    field = {"mod-6": "faultmaincode", "min-6": "faultcode"}.get(profile)
    fault = values.get(field) if field else None
    description = "Not reported"
    if type(fault) is int:
        fault &= 65535
        description = "No main fault reported" if fault == 0 else f"Unmapped fault code {fault}"
        if family == "MIC TL-X" and fault in MIC_FAULTS:
            description = f"{MIC_FAULTS[fault]} (code {fault})"
    warning_field = {"mod-6": "warnmaincode", "min-6": "warningcode"}.get(profile)
    warning = values.get(warning_field) if warning_field else None
    return {
        "operating_state": state,
        "fault_description": description,
        "state_code": raw,
        "fault_code": fault,
        "warning_code": warning & 65535 if type(warning) is int else None,
    }
