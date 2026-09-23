"""Readback-verified inverter and experimental schedule switches."""

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback

from .direct_entities import NativeControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = {}

    @callback
    def update(identity):
        added = []
        for control in hub.controls.controls(identity):
            if not control.switch:
                continue
            key = (identity, control.key)
            if key not in entities:
                entities[key] = NativeSwitch(hub, identity, control.key, control.label)
                added.append(entities[key])
        for period in hub.controls.periods(identity):
            key = (identity, f"{period}_enabled")
            if key not in entities:
                label = f"{period.replace('_', ' ').title()} enabled"
                entities[key] = NativeSwitch(hub, identity, key[1], label)
                added.append(entities[key])
        if added:
            async_add_entities(added)
        for (device, _), entity in entities.items():
            if device == identity:
                entity.refresh_state()

    entry.async_on_unload(hub.controls.subscribe(update))


class NativeSwitch(NativeControlEntity, SwitchEntity):
    @property
    def available(self):
        if not super().available:
            return False
        if self.key.endswith("_enabled"):
            return self.key.removesuffix("_enabled") in self.hub.controls.periods(self.identity)
        return self.key in {control.key for control in self.hub.controls.controls(self.identity)}

    @property
    def is_on(self):
        if self.key.endswith("_enabled"):
            period = self.hub.controls.period(self.identity, self.key.removesuffix("_enabled"))
            return period.enabled if period else None
        value = self.hub.controls.value(self.identity, self.key)
        return bool(value) if value is not None else None

    async def async_turn_on(self, **kwargs):
        if self.key.endswith("_enabled"):
            await self.hub.controls.set_period(
                self.identity, self.key.removesuffix("_enabled"), "enabled", True
            )
        else:
            await self.hub.controls.set_control(self.identity, self.key, 1)

    async def async_turn_off(self, **kwargs):
        if self.key.endswith("_enabled"):
            await self.hub.controls.set_period(
                self.identity, self.key.removesuffix("_enabled"), "enabled", False
            )
        else:
            await self.hub.controls.set_control(self.identity, self.key, 0)
