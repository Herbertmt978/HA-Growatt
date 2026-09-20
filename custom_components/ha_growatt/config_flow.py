"""Enable the companion using Home Assistant's existing MQTT connection."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import DEFAULTS, DOMAIN


def schema(options):
    return vol.Schema(
        {
            vol.Optional("daylight_alerts", default=options.get("daylight_alerts", True)): bool,
            vol.Optional("stale_minutes", default=options.get("stale_minutes", 15)): vol.All(
                vol.Coerce(int), vol.Range(min=5, max=120)
            ),
            vol.Optional(
                "sunrise_grace_minutes", default=options.get("sunrise_grace_minutes", 30)
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=180)),
            vol.Optional("buffered_events", default=options.get("buffered_events", True)): bool,
        }
    )


class Flow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if not self.hass.config_entries.async_entries("mqtt"):
            return self.async_abort(reason="mqtt_required")
        if user_input is not None:
            return self.async_create_entry(title="HA Growatt", data={}, options=user_input)
        return self.async_show_form(step_id="user", data_schema=schema(DEFAULTS))

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return Options()


class Options(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=schema(self.config_entry.options))
