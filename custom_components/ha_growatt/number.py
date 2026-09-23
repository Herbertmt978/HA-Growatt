"""Readback-verified inverter number settings."""

import math

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .direct_entities import NativeControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = {}

    @callback
    def update(identity):
        added = []
        for control in hub.controls.controls(identity):
            if control.switch:
                continue
            key = (identity, control.key)
            if key not in entities:
                entities[key] = NativeNumber(hub, identity, control)
                added.append(entities[key])
        if added:
            async_add_entities(added)
        for (device, _), entity in entities.items():
            if device == identity:
                entity.refresh_state()

    entry.async_on_unload(hub.controls.subscribe(update))


class NativeNumber(NativeControlEntity, NumberEntity):
    _attr_mode = NumberMode.BOX
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"

    def __init__(self, hub, identity, control):
        super().__init__(hub, identity, control.key, control.label)
        self._attr_native_min_value = control.minimum
        self._attr_native_max_value = 100

    @property
    def available(self):
        return super().available and self.key in {
            control.key for control in self.hub.controls.controls(self.identity)
        }

    @property
    def native_value(self):
        return self.hub.controls.value(self.identity, self.key)

    async def async_set_native_value(self, value):
        if type(value) not in {int, float} or not math.isfinite(value) or value != int(value):
            raise HomeAssistantError("Enter a whole percentage")
        await self.hub.controls.set_control(self.identity, self.key, int(value))
