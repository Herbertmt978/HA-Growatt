"""Manual readback of native inverter settings."""

from homeassistant.components.button import ButtonEntity
from homeassistant.core import callback

from .direct_entities import NativeControlEntity


async def async_setup_entry(hass, entry, async_add_entities):
    hub = entry.runtime_data
    entities = {}

    @callback
    def update(identity):
        if identity not in entities and (
            hub.controls.controls(identity) or hub.controls.periods(identity)
        ):
            entities[identity] = NativeRefresh(hub, identity)
            async_add_entities([entities[identity]])
        if identity in entities:
            entities[identity].refresh_state()

    entry.async_on_unload(hub.controls.subscribe(update))


class NativeRefresh(NativeControlEntity, ButtonEntity):
    def __init__(self, hub, identity):
        super().__init__(hub, identity, "refresh_settings", "Refresh settings")

    async def async_press(self):
        await self.hub.controls.refresh(self.identity)
