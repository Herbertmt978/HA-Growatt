"""Run a temporary native-receiver check inside a Home Assistant Core image."""

import asyncio
import json
import os
import socket
from pathlib import Path
from types import MappingProxyType

from homeassistant import bootstrap, loader
from homeassistant.config_entries import SOURCE_USER, ConfigEntry
from homeassistant.core import HomeAssistant


async def main():
    # Home Assistant installs its Voluptuous compatibility layer during import.
    import voluptuous as vol

    hass = HomeAssistant("/config")
    loader.async_setup(hass)
    hass.config.skip_pip = True
    hass = await bootstrap.async_from_config_dict({"sun": {}}, hass)
    assert hass is not None
    await hass.async_start()
    if os.environ.get("HA_GROWATT_PROBE_RESTORE") == "1":
        await hass.async_block_till_done()
        entries = hass.config_entries.async_entries("ha_growatt")
        assert len(entries) == 1
        assert getattr(entries[0].state, "value", entries[0].state) == "loaded"
        restored = [
            state
            for state in hass.states.async_all()
            if state.entity_id.startswith(("sensor.invert0001", "binary_sensor.invert0001"))
        ]
        assert len(restored) == 33, len(restored)
        assert any(
            "pv_output_actual" in state.entity_id and state.state == "0.4" for state in restored
        )
        assert any(
            state.entity_id.startswith("binary_sensor.invert0001") and state.state == "off"
            for state in restored
        )
        print(f"Quiet restart: {len(restored)} saved entities; Connected remains off")
        await hass.async_stop()
        return
    flow = await hass.config_entries.flow.async_init("ha_growatt", context={"source": SOURCE_USER})
    assert set(flow["menu_options"]) == {"direct", "modbus", "companion"}, flow
    direct_form = await hass.config_entries.flow.async_configure(
        flow["flow_id"], {"next_step_id": "direct"}
    )
    assert direct_form.get("step_id") == "direct", {
        "type": str(direct_form.get("type")),
        "reason": direct_form.get("reason"),
    }
    try:
        direct_form["data_schema"]({"family": "max"})
    except vol.Invalid:
        pass
    else:
        raise AssertionError("The native setup offered a family without a built-in layout")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    entry = ConfigEntry(
        domain="ha_growatt",
        title="HA Growatt receiver",
        data={"mode": "direct", "port": port, "forward_cloud": False, "family": "default"},
        options={
            "daylight_alerts": True,
            "stale_minutes": 15,
            "sunrise_grace_minutes": 30,
            "buffered_events": True,
        },
        source=SOURCE_USER,
        version=1,
        minor_version=1,
        unique_id="ha_growatt_direct",
        discovery_keys=MappingProxyType({}),
        subentries_data=[],
        pref_disable_new_entities=None,
        pref_disable_polling=None,
        disabled_by=None,
    )
    await hass.config_entries.async_add(entry)
    await hass.async_block_till_done()
    assert getattr(entry.state, "value", entry.state) == "loaded", entry.state
    cases = json.loads(
        await asyncio.to_thread(Path("/repo/tests/fixtures/telemetry_cases.json").read_text)
    )
    wire = next(
        case["wire"] for case in cases if case["profile"] == "classic-6" and not case["include_all"]
    )
    _reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(bytes.fromhex(wire))
    await writer.drain()
    for _ in range(100):
        await asyncio.sleep(0.1)
        if any(
            state.entity_id.startswith("sensor.invert0001") for state in hass.states.async_all()
        ):
            break
    states = [
        state
        for state in hass.states.async_all()
        if state.entity_id.startswith(("sensor.invert0001", "binary_sensor.invert0001"))
    ]
    assert states, "No native inverter entities appeared"
    assert any(
        "pv_output_actual" in state.entity_id and state.state == "0.4" for state in states
    ), [str(state) for state in states[:10]]
    print(f"Native HA entities: {len(states)}; receiver state: {entry.state}")
    writer.close()
    await writer.wait_closed()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_stop()


if __name__ == "__main__":
    asyncio.run(main())
