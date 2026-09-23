"""Experimental SPH/SPA charging schedule times."""

from homeassistant.components.text import TextEntity
from homeassistant.core import callback

from .direct_entities import NativeControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = {}

    @callback
    def update(identity):
        added = []
        for period in hub.controls.periods(identity):
            for part in ("start", "end"):
                key = (identity, f"{period}_{part}")
                if key not in entities:
                    label = f"{period.replace('_', ' ').title()} {part}"
                    entities[key] = NativeTimeText(hub, identity, key[1], label)
                    added.append(entities[key])
        if added:
            async_add_entities(added)
        for (device, _), entity in entities.items():
            if device == identity:
                entity.refresh_state()

    entry.async_on_unload(hub.controls.subscribe(update))


class NativeTimeText(NativeControlEntity, TextEntity):
    _attr_native_min = 5
    _attr_native_max = 5
    _attr_pattern = r"(?:[01][0-9]|2[0-3]):[0-5][0-9]"

    @property
    def available(self):
        return super().available and self.key.rsplit("_", 1)[0] in self.hub.controls.periods(
            self.identity
        )

    @property
    def native_value(self):
        key, part = self.key.rsplit("_", 1)
        period = self.hub.controls.period(self.identity, key)
        return getattr(period, part) if period else None

    async def async_set_value(self, value):
        key, part = self.key.rsplit("_", 1)
        await self.hub.controls.set_period(self.identity, key, part, value)
