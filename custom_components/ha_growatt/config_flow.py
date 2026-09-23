"""Enable the companion using Home Assistant's existing MQTT connection."""

import asyncio
import json

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import mqtt
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .const import DEFAULTS, DOMAIN


async def app_connection_note(hass):
    """Check for a fresh app status on HA's broker without blocking setup."""
    if not any(
        getattr(entry.state, "value", entry.state) == "loaded"
        for entry in hass.config_entries.async_entries("mqtt")
    ):
        return "MQTT is configured but has not connected yet. Check its broker connection."

    live_status = asyncio.get_running_loop().create_future()

    @callback
    def receive(message):
        # A retained online value may be left from a previous app session.
        if message.retain or live_status.done():
            return
        try:
            status = json.loads(message.payload)
        except (TypeError, ValueError):
            return
        if isinstance(status, dict) and type(status.get("online")) is bool:
            live_status.set_result(status["online"])

    try:
        unsubscribe = await mqtt.async_subscribe(hass, "ha_growatt/service/status", receive, qos=0)
    except (HomeAssistantError, OSError, RuntimeError):
        return (
            "Home Assistant could not check the MQTT connection. You can still add the companion."
        )
    try:
        online = await asyncio.wait_for(live_status, timeout=4)
    except TimeoutError:
        return (
            "No live app status arrived on Home Assistant's broker. "
            "Check that the app is running and uses the same broker. "
            "You can still add the companion and check again later."
        )
    finally:
        unsubscribe()
    if online:
        return "Live HA Growatt app status arrived on Home Assistant's MQTT broker."
    return "The HA Growatt app reported that it is offline. Check the app and broker connection."


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
        return self.async_show_form(
            step_id="user",
            data_schema=schema(DEFAULTS),
            description_placeholders={"connection_note": await app_connection_note(self.hass)},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return Options()


class Options(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=schema(self.config_entry.options))
