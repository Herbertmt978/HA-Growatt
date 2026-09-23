"""Native sensors for the single-integration receiver."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo

from ha_growatt.discovery import sensor_value, sensors_for

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = {}

    @callback
    def receive(reading):
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
                entity = NativeSensor(reading.identity, spec, reading)
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

    def __init__(self, identity, spec, reading):
        self.identity = identity
        self.spec = spec
        self._attr_name = spec.label
        self._attr_unique_id = f"ha_growatt_direct_{identity}_{spec.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity)}, name=identity, manufacturer="Growatt"
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
