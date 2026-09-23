"""Show whether native Growatt readings are still arriving."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .const import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = {}
    if entry.data.get("mode") == "modbus":
        identity = hub.receiver.identity
        entity = NativeConnected(identity, "modbus", hub)
        entities[identity] = entity
        async_add_entities([entity])

    @callback
    def receive(reading):
        entity = entities.get(reading.identity)
        if entity is None:
            entity = NativeConnected(reading.identity, entry.data.get("mode"), hub)
            entities[reading.identity] = entity
            if not reading.restored:
                entity.received(reading)
            async_add_entities([entity])
        elif not reading.restored:
            entity.received(reading)

    @callback
    def refresh(now):
        for entity in entities.values():
            entity.refresh(now)

    entry.async_on_unload(hub.receiver.subscribe(receive))
    entry.async_on_unload(async_track_time_interval(hass, refresh, timedelta(seconds=30)))


class NativeConnected(BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_name = "Connected"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY

    def __init__(self, identity, mode="direct", hub=None):
        prefix = "ha_growatt_modbus" if mode == "modbus" else "ha_growatt_direct"
        self._mode = mode
        self._hub = hub
        self._attr_unique_id = f"{prefix}_{identity}_connected"
        self._attr_device_info = DeviceInfo(
            identifiers={
                (DOMAIN, f"{prefix}_{identity}") if mode == "modbus" else (DOMAIN, identity)
            },
            name=identity,
            manufacturer="Growatt",
        )
        self.last_live = None
        self._attr_is_on = False

    @callback
    def received(self, reading):
        self.last_live = reading.snapshot.received_at
        self.refresh(dt_util.utcnow())

    @callback
    def refresh(self, now):
        max_age = timedelta(minutes=15)
        if self._mode == "modbus" and self._hub is not None:
            max_age = max(max_age, timedelta(seconds=self._hub.receiver.interval + 60))
        self._attr_is_on = bool(
            self.last_live
            and timedelta(seconds=-60) <= now - self.last_live <= max_age
            and (self._mode != "modbus" or self._hub.receiver.connected)
        )
        if self.hass is not None:
            self.async_write_ha_state()
