"""Set up a native receiver or connect the existing app via MQTT."""

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


def direct_schema(values=None):
    values = values or {}
    return vol.Schema(
        {
            vol.Optional("port", default=values.get("port", 5279)): vol.All(
                vol.Coerce(int), vol.Range(min=1024, max=65535)
            ),
            vol.Optional("forward_cloud", default=values.get("forward_cloud", True)): bool,
            vol.Optional("family", default=values.get("family", "default")): vol.In(
                ["default", "min", "mod", "sph", "spf", "spa", "tl3"]
            ),
        }
    )


async def port_available(port):
    """Check a local bind; a later race is still handled during receiver setup."""
    try:
        listener = await asyncio.start_server(lambda _r, w: w.close(), "0.0.0.0", port)
    except OSError:
        return False
    listener.close()
    await listener.wait_closed()
    return True


class Flow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        return self.async_show_menu(step_id="user", menu_options=["direct", "companion"])

    async def async_step_companion(self, user_input=None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        if not self.hass.config_entries.async_entries("mqtt"):
            return self.async_abort(reason="mqtt_required")
        if user_input is not None:
            return self.async_create_entry(title="HA Growatt", data={}, options=user_input)
        return self.async_show_form(
            step_id="companion",
            data_schema=schema(DEFAULTS),
            description_placeholders={"connection_note": await app_connection_note(self.hass)},
        )

    async def async_step_direct(self, user_input=None):
        await self.async_set_unique_id("ha_growatt_direct")
        self._abort_if_unique_id_configured()
        errors = {}
        if user_input is not None:
            if await port_available(user_input["port"]):
                return self.async_create_entry(
                    title="HA Growatt receiver",
                    data={"mode": "direct", **user_input},
                    options=DEFAULTS,
                )
            errors["base"] = "port_in_use"
        return self.async_show_form(
            step_id="direct", data_schema=direct_schema(user_input), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return Options()


class Options(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        options = dict(self.config_entry.options)
        if self.config_entry.data.get("mode") == "direct":
            direct = dict(self.config_entry.data) | options
            if user_input is not None:
                if user_input["port"] != direct["port"] and not await port_available(
                    user_input["port"]
                ):
                    return self.async_show_form(
                        step_id="init",
                        data_schema=vol.Schema(
                            {**schema(direct).schema, **direct_schema(direct).schema}
                        ),
                        errors={"base": "port_in_use"},
                    )
                return self.async_create_entry(data=user_input)
            return self.async_show_form(
                step_id="init",
                data_schema=vol.Schema({**schema(direct).schema, **direct_schema(direct).schema}),
            )
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(step_id="init", data_schema=schema(options))
