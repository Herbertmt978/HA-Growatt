"""Native sensors for the single-integration receiver."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.event import async_track_time_interval

from ha_growatt.discovery import sensor_value, sensors_for

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = {}
    if entry.data.get("mode") == "direct":
        counters = [
            NativeCounter(hub, key, label)
            for key, label in (
                ("measurements", "Readings received"),
                ("failed_measurements", "Failed measurements"),
                ("announcement_warnings", "Packet warnings"),
                ("incomplete_fields", "Incomplete fields"),
                ("buffered_records", "Buffered records"),
                ("output_failures", "Output failures"),
                ("output_dropped", "Dropped output readings"),
            )
        ]
        async_add_entities(counters)

        @callback
        def refresh_counters(now=None):
            for counter in counters:
                counter.refresh()

        entry.async_on_unload(
            async_track_time_interval(hass, refresh_counters, timedelta(seconds=30))
        )

    @callback
    def receive(reading):
        if entry.data.get("mode") == "direct":
            refresh_counters()
        telemetry = reading.snapshot.telemetry
        specs = sensors_for(
            wire_profile=telemetry.profile,
            sensor_metadata=telemetry.sensor_metadata,
        )
        added = []
        current = set()
        for spec in specs:
            key = (reading.identity, spec.key)
            current.add(key)
            entity = entities.get(key)
            if entity is None:
                entity = NativeSensor(reading.identity, spec, reading, entry.data.get("mode"))
                entities[key] = entity
                added.append(entity)
            else:
                entity.set_reading(reading)
        for key, entity in entities.items():
            if key[0] == reading.identity and key not in current:
                entity.clear_reading()
        if added:
            async_add_entities(added)

    entry.async_on_unload(hub.receiver.subscribe(receive))


class NativeSensor(SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, identity, spec, reading, mode="direct"):
        self.identity = identity
        self.spec = spec
        self._attr_name = spec.label
        prefix = "ha_growatt_modbus" if mode == "modbus" else "ha_growatt_direct"
        self._attr_unique_id = f"{prefix}_{identity}_{spec.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, f"{prefix}_{identity}") if mode == "modbus" else (DOMAIN, identity)
            },
            name=identity,
            manufacturer="Growatt",
        )
        if spec.unit:
            self._attr_native_unit_of_measurement = spec.unit
        if spec.device_class:
            self._attr_device_class = SensorDeviceClass(spec.device_class)
        if spec.state_class:
            self._attr_state_class = SensorStateClass(spec.state_class)
        if spec.entity_category:
            self._attr_entity_category = spec.entity_category
        if spec.icon:
            self._attr_icon = spec.icon
        self._attr_native_value = None
        self.set_reading(reading)

    @callback
    def set_reading(self, reading):
        snapshot = reading.snapshot
        self._attr_native_value = sensor_value(
            self.spec, snapshot.telemetry.values, snapshot.received_at, snapshot.telemetry.profile
        )
        if self.hass is not None:
            self.async_write_ha_state()

    @callback
    def clear_reading(self):
        if self._attr_native_value is not None:
            self._attr_native_value = None
            if self.hass is not None:
                self.async_write_ha_state()


class NativeCounter(SensorEntity):
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:counter"

    def __init__(self, hub, key, label):
        self.hub = hub
        self.key = key
        self._attr_name = label
        self._attr_unique_id = f"ha_growatt_direct_receiver_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "ha_growatt_direct_receiver")},
            name="HA Growatt receiver",
            manufacturer="HA Growatt",
        )
        self._attr_native_value = 0
        self.refresh()

    @callback
    def refresh(self):
        if self.key in {"output_failures", "output_dropped"}:
            name = "failures" if self.key == "output_failures" else "dropped"
            self._attr_native_value = sum(
                value[name] for value in self.hub.outputs.status().values()
            )
        else:
            self._attr_native_value = getattr(self.hub.receiver, self.key)
        if self.hass is not None:
            self.async_write_ha_state()
