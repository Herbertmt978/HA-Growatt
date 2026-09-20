"""Synthetic native HA checks; never run against an existing HA configuration."""

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path

from ha_feature_probe import CONF, CONFIG, boot, until
from homeassistant.components import mqtt
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import async_import_statistics, get_metadata
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

BASE = Path(__file__).parent


async def main():
    await asyncio.to_thread(CONFIG.mkdir, exist_ok=True)
    await asyncio.to_thread(
        shutil.copytree,
        BASE / "custom_components",
        CONFIG / "custom_components",
        dirs_exist_ok=True,
    )
    CONF["sun"] = {}
    CONF["sensor"] = [
        {
            "platform": "min_max",
            "name": "Original solar sum",
            "type": "sum",
            "entity_ids": ["sensor.test_growatt", "sensor.test_solaredge"],
        }
    ]
    expression = (
        "{{ ((states('sensor.test_growatt') | float) * {'W': 0.001, 'kW': 1}"
        "[state_attr('sensor.test_growatt', 'unit_of_measurement')] + "
        "(states('sensor.test_solaredge') | float) * {'W': 0.001, 'kW': 1}"
        "[state_attr('sensor.test_solaredge', 'unit_of_measurement')]) | round(4) }}"
    )
    available = (
        "{{ is_number(states('sensor.test_growatt')) and "
        "is_number(states('sensor.test_solaredge')) and "
        "state_attr('sensor.test_growatt', 'unit_of_measurement') in ['W','kW'] and "
        "state_attr('sensor.test_solaredge', 'unit_of_measurement') in ['W','kW'] }}"
    )
    CONF["template"] = [
        {
            "sensor": [
                {
                    "name": "Normalised solar",
                    "unique_id": "normalised-solar",
                    "state": expression,
                    "availability": available,
                    "unit_of_measurement": "kW",
                    "device_class": "power",
                    "state_class": "measurement",
                }
            ]
        }
    ]
    hass = await boot()
    results = {}
    try:
        await until(lambda: hass.states.get("sensor.normalised_solar") is not None)
        assert hass.states.get("sensor.normalised_solar").state == "unavailable"
        hass.states.async_set(
            "sensor.test_growatt", 125.3, {"unit_of_measurement": "W", "device_class": "power"}
        )
        hass.states.async_set(
            "sensor.test_solaredge", 1.59, {"unit_of_measurement": "kW", "device_class": "power"}
        )
        await until(lambda: hass.states.get("sensor.normalised_solar").state == "1.7153")
        assert hass.states.get("sensor.original_solar_sum").state == "unknown"
        hass.states.async_set(
            "sensor.test_growatt",
            "unavailable",
            {"unit_of_measurement": "W", "device_class": "power"},
        )
        await until(lambda: hass.states.get("sensor.normalised_solar").state == "unavailable")
        hass.states.async_set(
            "sensor.test_growatt", 0.1253, {"unit_of_measurement": "kW", "device_class": "power"}
        )
        await until(lambda: hass.states.get("sensor.normalised_solar").state == "1.7153")
        results["native_mixed_units_late_sources_missing_input_and_unit_change"] = "passed"
        helper_flow = await hass.config_entries.flow.async_init(
            "template", context={"source": "user"}
        )
        helper_flow = await hass.config_entries.flow.async_configure(
            helper_flow["flow_id"], {"next_step_id": "sensor"}
        )
        helper_flow = await hass.config_entries.flow.async_configure(
            helper_flow["flow_id"],
            {
                "name": "Solar total helper",
                "state": expression,
                "unit_of_measurement": "kW",
                "device_class": "power",
                "state_class": "measurement",
                "additional_options": {"availability": available},
            },
        )
        assert helper_flow["type"] == "create_entry", helper_flow
        helper_entry = helper_flow["result"]
        await until(lambda: helper_entry.state.value == "loaded")
        await until(lambda: hass.states.get("sensor.solar_total_helper") is not None)
        assert hass.states.get("sensor.solar_total_helper").state == "1.7153"
        await hass.config_entries.async_reload(helper_entry.entry_id)
        await until(lambda: hass.states.get("sensor.solar_total_helper") is not None)
        assert hass.states.get("sensor.solar_total_helper").state == "1.7153"
        results["native_template_helper_setup_reload"] = "passed"

        for old_entry in hass.config_entries.async_entries("ha_growatt"):
            await hass.config_entries.async_remove(old_entry.entry_id)
        flow = await hass.config_entries.flow.async_init("ha_growatt", context={"source": "user"})
        assert flow["type"] == "form", flow
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"],
            {
                "daylight_alerts": True,
                "stale_minutes": 15,
                "sunrise_grace_minutes": 0,
                "buffered_events": True,
            },
        )
        assert flow["type"] == "create_entry", flow
        entry = flow["result"]
        await until(lambda: entry.state.value == "loaded")
        service = entry.runtime_data
        # HA batches MQTT subscriptions for 0.5 seconds after connection.
        await asyncio.sleep(1)
        events = []
        unsubscribe = hass.bus.async_listen(
            "ha_growatt_buffered_record", lambda event: events.append(event.data)
        )

        async def send(topic, data, retain=False):
            if topic == "ha_growatt/service/status":
                data = {"connections": [], **data}
            await mqtt.async_publish(hass, topic, json.dumps(data), retain=retain)
            await asyncio.sleep(0.3)

        now = datetime.now(UTC)
        service.started = now - timedelta(minutes=5)
        await send("ha_growatt/service/status", {"schema": 1, "online": True, "observations": {}})
        await until(lambda: service.service_seen is not None)
        await send(
            "ha_growatt/TEST000001/status",
            {"schema": 1, "last_record": (now - timedelta(hours=3)).isoformat()},
        )
        hass.states.async_set("sun.sun", "above_horizon")
        service.evaluate(now)
        assert ir.async_get(hass).async_get_issue("ha_growatt", "feed_TEST000001"), {
            "service": service.service,
            "devices": service.devices,
            "options": service.options,
            "issues": service.current_issues,
            "sun": hass.states.get("sun.sun").state,
        }
        hass.states.async_set("sun.sun", "below_horizon")
        service.evaluate(now)
        assert not ir.async_get(hass).async_get_issue("ha_growatt", "feed_TEST000001")
        hass.states.async_set("sun.sun", "above_horizon")
        await send("ha_growatt/TEST000001/status", {"schema": 1, "last_record": now.isoformat()})
        assert not ir.async_get(hass).async_get_issue("ha_growatt", "feed_TEST000001")
        await send("ha_growatt/service/status", {"online": False})
        assert ir.async_get(hass).async_get_issue("ha_growatt", "service_offline")
        await send("ha_growatt/service/status", {"online": True})
        assert not ir.async_get(hass).async_get_issue("ha_growatt", "service_offline")
        results["native_mqtt_repairs_day_night_freshness_and_service_recovery"] = "passed"
        await send(
            "homeassistant/sensor/ha_growatt_migration/config",
            {
                "name": "Migration lifetime test",
                "unique_id": "grott_TEST000001_pvenergytotal",
                "state_topic": "qualification/migration/state",
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
            },
            True,
        )
        await asyncio.sleep(1)
        await send("qualification/migration/state", 120)
        registry = er.async_get(hass)
        await until(
            lambda: (
                registry.async_get_entity_id("sensor", "mqtt", "grott_TEST000001_pvenergytotal")
                is not None
            )
        )
        target = registry.async_get_entity_id("sensor", "mqtt", "grott_TEST000001_pvenergytotal")
        await until(
            lambda: hass.states.get(target) is not None and hass.states.get(target).state == "120"
        )
        source = "sensor.historical_growatt_energy"
        metadata = {
            "source": "recorder",
            "statistic_id": source,
            "name": "Historical inverter total",
            "unit_of_measurement": "Wh",
            "unit_class": "energy",
            "has_mean": False,
            "mean_type": 0,
            "has_sum": True,
        }
        async_import_statistics(
            hass,
            metadata,
            [
                {
                    "start": now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=2),
                    "state": 119000,
                    "sum": 19000,
                }
            ],
        )
        recorder = get_instance(hass)
        await recorder.async_block_till_done()
        before = await recorder.async_add_executor_job(
            partial(get_metadata, hass, statistic_ids={source})
        )
        for _ in range(30):
            if source in before:
                break
            await asyncio.sleep(0.2)
            before = await recorder.async_add_executor_job(
                partial(get_metadata, hass, statistic_ids={source})
            )
        assert source in before
        response = await hass.services.async_call(
            "ha_growatt",
            "adopt_history",
            {
                "target_entity": target,
                "source_entity_id": source,
            },
            blocking=True,
            return_response=True,
        )
        assert response["compatible"] and not response["applied"]
        assert registry.async_get(target)
        response = await hass.services.async_call(
            "ha_growatt",
            "adopt_history",
            {
                "target_entity": target,
                "source_entity_id": source,
                "confirm": True,
                "same_measurement": True,
            },
            blocking=True,
            return_response=True,
        )
        assert response["applied"]
        await until(lambda: hass.states.get(source) is not None)
        after = await recorder.async_add_executor_job(
            partial(get_metadata, hass, statistic_ids={source})
        )
        assert before[source] == after[source]
        assert registry.async_get(source).unique_id == "grott_TEST000001_pvenergytotal"
        results["native_history_preview_confirm_identity_and_statistics_preserved"] = "passed"
        event = {
            "schema": 1,
            "inverter": "TEST000001",
            "recorded_at": "2026-09-20T09:00:00",
            "values": {"pvpowerout": 123},
        }
        await send("ha_growatt/events/buffered", event, True)
        # Live delivery of a retained publication is new; replay on resubscribe is not.
        assert len(events) == 1
        await send("ha_growatt/events/buffered", event)
        assert len(events) == 1
        assert await hass.config_entries.async_reload(entry.entry_id)
        await until(lambda: entry.state.value == "loaded")
        await asyncio.sleep(0.4)
        assert len(events) == 1
        assert service.closed and not service.unsubscribers
        event["recorded_at"] = "2026-09-20T09:05:00"
        await send("ha_growatt/events/buffered", event)
        assert len(events) == 2
        assert await hass.config_entries.async_unload(entry.entry_id)
        event["recorded_at"] = "2026-09-20T09:10:00"
        await send("ha_growatt/events/buffered", event)
        assert len(events) == 2
        unsubscribe()
        results["native_buffered_events_duplicate_retained_reload_and_unload"] = "passed"
    finally:
        await hass.async_stop()
        (CONFIG / "reliability-result.json").write_text(json.dumps(results, indent=2))
    print(json.dumps(results))


asyncio.run(main())
