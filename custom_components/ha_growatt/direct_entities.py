"""Shared identity and availability for native inverter settings."""

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityCategory

from .const import DOMAIN


class NativeControlEntity:
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hub, identity, key, label):
        self.hub = hub
        self.identity = identity
        self.key = key
        self._attr_name = label
        self._attr_unique_id = f"ha_growatt_direct_{identity}_{key}"
        model = hub.options.get("hardware_models", {}).get(identity)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, identity)},
            name=identity,
            manufacturer="Growatt",
            **({"model": model} if model else {}),
        )

    @property
    def available(self):
        return self.hub.controls.available(self.identity)

    def refresh_state(self):
        if self.hass is not None:
            self.async_write_ha_state()
