"""Set up a native receiver or connect the existing app via MQTT."""

import asyncio
import json
import re

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.components import mqtt
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from ha_growatt.modbus_receiver import PROFILE_CHOICES

from .const import DEFAULTS, DOMAIN

_SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
_CONTROL_MODELS = ("auto", "sph", "spa", "min_tl_xh", "mod_tl3_xh")


def gateway_host(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 253
        or any(character.isspace() or character in "/\\@" for character in value)
    ):
        raise vol.Invalid("Enter a gateway hostname or IP address")
    return value


def investigation_range(values):
    if values["investigation_start"] + values["investigation_count"] > 65536:
        raise vol.Invalid("The raw investigation block must end by address 65535")
    return values


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


def modbus_schema(values=None):
    values = values or {}
    return vol.All(
        vol.Schema(
            {
                vol.Required("host", default=values.get("host", "")): gateway_host,
                vol.Optional("port", default=values.get("port", 502)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Optional("unit", default=values.get("unit", 1)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=247)
                ),
                vol.Required("identity", default=values.get("identity", "growatt_modbus")): vol.All(
                    str, vol.Match(_SAFE_ID)
                ),
                vol.Optional("profile", default=values.get("profile", "min-3000-v124")): vol.In(
                    PROFILE_CHOICES
                ),
                vol.Optional("interval", default=values.get("interval", 60)): vol.All(
                    vol.Coerce(int), vol.Range(min=30, max=3600)
                ),
                vol.Optional(
                    "investigation_kind", default=values.get("investigation_kind", "input")
                ): vol.In(["input", "holding"]),
                vol.Optional(
                    "investigation_start", default=values.get("investigation_start", 0)
                ): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
                vol.Optional(
                    "investigation_count", default=values.get("investigation_count", 32)
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=32)),
            }
        ),
        investigation_range,
    )


def modbus_options_schema(values):
    return vol.Schema(
        {
            vol.Required("host", default=values["host"]): gateway_host,
            vol.Optional("port", default=values["port"]): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=65535)
            ),
            vol.Optional("unit", default=values["unit"]): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=247)
            ),
            vol.Optional("profile", default=values["profile"]): vol.In(PROFILE_CHOICES),
            vol.Optional("interval", default=values["interval"]): vol.All(
                vol.Coerce(int), vol.Range(min=30, max=3600)
            ),
            vol.Optional(
                "investigation_kind", default=values.get("investigation_kind", "input")
            ): vol.In(["input", "holding"]),
            vol.Optional(
                "investigation_start", default=values.get("investigation_start", 0)
            ): vol.All(vol.Coerce(int), vol.Range(min=0, max=65535)),
            vol.Optional(
                "investigation_count", default=values.get("investigation_count", 32)
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=32)),
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
        return self.async_show_menu(step_id="user", menu_options=["direct", "modbus", "companion"])

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

    async def async_step_modbus(self, user_input=None):
        if user_input is not None:
            await self.async_set_unique_id(f"ha_growatt_modbus_{user_input['identity']}")
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"Growatt Modbus {user_input['identity']}",
                data={"mode": "modbus", **user_input},
                options=DEFAULTS,
            )
        return self.async_show_form(step_id="modbus", data_schema=modbus_schema())

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return Options()


class Options(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        if self.config_entry.data.get("mode") == "direct":
            return self.async_show_menu(
                step_id="init",
                menu_options=["receiver", "inverter", "outputs"],
            )
        if self.config_entry.data.get("mode") == "modbus":
            return await self.async_step_modbus_options(user_input)
        return await self.async_step_receiver(user_input)

    async def async_step_modbus_options(self, user_input=None):
        options = dict(self.config_entry.options)
        if user_input is not None:
            return self.async_create_entry(data=options | user_input)
        values = dict(self.config_entry.data) | options
        return self.async_show_form(
            step_id="modbus_options",
            data_schema=vol.All(
                vol.Schema({**schema(values).schema, **modbus_options_schema(values).schema}),
                investigation_range,
            ),
        )

    async def async_step_receiver(self, user_input=None):
        options = dict(self.config_entry.options)
        if self.config_entry.data.get("mode") == "direct":
            direct = dict(self.config_entry.data) | options
            controls_schema = {
                vol.Optional("enable_controls", default=direct.get("enable_controls", False)): bool,
                vol.Optional(
                    "experimental_controls", default=direct.get("experimental_controls", False)
                ): bool,
            }
            if user_input is not None:
                if user_input["port"] != direct["port"] and not await port_available(
                    user_input["port"]
                ):
                    return self.async_show_form(
                        step_id="receiver",
                        data_schema=vol.Schema(
                            {
                                **schema(direct).schema,
                                **direct_schema(direct).schema,
                                **controls_schema,
                            }
                        ),
                        errors={"base": "port_in_use"},
                    )
                return self.async_create_entry(data=options | user_input)
            return self.async_show_form(
                step_id="receiver",
                data_schema=vol.Schema(
                    {**schema(direct).schema, **direct_schema(direct).schema, **controls_schema}
                ),
            )
        if user_input is not None:
            return self.async_create_entry(data=options | user_input)
        return self.async_show_form(step_id="receiver", data_schema=schema(options))

    async def async_step_inverter(self, user_input=None):
        hub = getattr(self.config_entry, "runtime_data", None)
        identities = sorted(getattr(getattr(hub, "receiver", None), "snapshots", {}))
        if not identities:
            return self.async_abort(reason="no_inverters")
        if user_input is not None:
            self._inverter_identity = user_input["identity"]
            return await self.async_step_inverter_model()
        return self.async_show_form(
            step_id="inverter",
            data_schema=vol.Schema({vol.Required("identity"): vol.In(identities)}),
        )

    async def async_step_inverter_model(self, user_input=None):
        identity = self._inverter_identity
        options = dict(self.config_entry.options)
        if user_input is not None:
            models = dict(options.get("control_models", {}))
            models[identity] = user_input["control_model"]
            hardware = dict(options.get("hardware_models", {}))
            hardware[identity] = user_input["hardware_model"].strip()
            return self.async_create_entry(
                data=options | {"control_models": models, "hardware_models": hardware}
            )
        return self.async_show_form(
            step_id="inverter_model",
            description_placeholders={"inverter": identity},
            data_schema=vol.Schema(
                {
                    vol.Optional(
                        "control_model",
                        default=options.get("control_models", {}).get(identity, "auto"),
                    ): vol.In(_CONTROL_MODELS),
                    vol.Optional(
                        "hardware_model",
                        default=options.get("hardware_models", {}).get(identity, ""),
                    ): vol.All(str, vol.Length(max=64)),
                }
            ),
        )

    async def async_step_outputs(self, user_input=None):
        return self.async_show_menu(
            step_id="outputs",
            menu_options=["http_output", "pvoutput_output", "influx_output", "mqtt_output"],
        )

    def _save_output(self, values, step_id, data_schema):
        from .output_config import native_output_settings

        merged = dict(self.config_entry.options) | values
        try:
            native_output_settings(merged)
        except (TypeError, ValueError):
            return self.async_show_form(
                step_id=step_id, data_schema=data_schema, errors={"base": "invalid_output"}
            )
        return self.async_create_entry(data=merged)

    async def async_step_http_output(self, user_input=None):
        options = self.config_entry.options
        output_schema = vol.Schema(
            {vol.Optional("endpoint", default=options.get("output_http_endpoint", "")): str}
        )
        if user_input is not None:
            return self._save_output(
                {"output_http_endpoint": user_input["endpoint"].strip()},
                "http_output",
                output_schema,
            )
        return self.async_show_form(
            step_id="http_output",
            data_schema=output_schema,
        )

    async def async_step_pvoutput_output(self, user_input=None):
        options = self.config_entry.options
        if user_input is not None:
            key = user_input.pop("api_key")
            system = user_input["system"].strip()
            if system and not (key or options.get("output_pvoutput_api_key")):
                return self.async_show_form(
                    step_id="pvoutput_output",
                    data_schema=self._pvoutput_schema(user_input),
                    errors={"base": "api_key_required"},
                )
            return self._save_output(
                {
                    "output_pvoutput_system": system,
                    "output_pvoutput_api_key": (
                        key or options.get("output_pvoutput_api_key", "") if system else ""
                    ),
                },
                "pvoutput_output",
                self._pvoutput_schema(user_input),
            )
        return self.async_show_form(step_id="pvoutput_output", data_schema=self._pvoutput_schema())

    def _pvoutput_schema(self, values=None):
        values = values or {}
        return vol.Schema(
            {
                vol.Optional(
                    "system",
                    default=values.get(
                        "system", self.config_entry.options.get("output_pvoutput_system", "")
                    ),
                ): str,
                vol.Optional("api_key", default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )

    async def async_step_influx_output(self, user_input=None):
        options = self.config_entry.options
        output_schema = self._influx_schema()
        if user_input is not None:
            from .output_config import destination_secret

            values = {f"output_influx_{key}": value for key, value in user_input.items()}
            previous = (
                options.get("output_influx_endpoint", "").strip(),
                options.get("output_influx_version", 2),
                options.get("output_influx_username", ""),
            )
            selected = (
                user_input["endpoint"].strip(),
                user_input["version"],
                user_input["username"],
            )
            for key in ("token", "password"):
                name = f"output_influx_{key}"
                values[name] = destination_secret(
                    values[name], options.get(name, ""), previous, selected
                )
            if not user_input["endpoint"].strip():
                values["output_influx_token"] = ""
                values["output_influx_password"] = ""
            return self._save_output(values, "influx_output", output_schema)
        return self.async_show_form(
            step_id="influx_output",
            data_schema=output_schema,
        )

    def _influx_schema(self):
        options = self.config_entry.options
        return vol.Schema(
            {
                vol.Optional("endpoint", default=options.get("output_influx_endpoint", "")): str,
                vol.Optional("version", default=options.get("output_influx_version", 2)): vol.In(
                    [1, 2]
                ),
                vol.Optional(
                    "database", default=options.get("output_influx_database", "grottdb")
                ): str,
                vol.Optional(
                    "organisation", default=options.get("output_influx_organisation", "")
                ): str,
                vol.Optional("bucket", default=options.get("output_influx_bucket", "")): str,
                vol.Optional("username", default=options.get("output_influx_username", "")): str,
                vol.Optional("password", default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional("token", default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
            }
        )

    async def async_step_mqtt_output(self, user_input=None):
        options = self.config_entry.options
        output_schema = self._mqtt_schema()
        if user_input is not None:
            from .output_config import destination_secret

            values = {f"output_mqtt_{key}": value for key, value in user_input.items()}
            previous = (
                options.get("output_mqtt_host", "").strip(),
                options.get("output_mqtt_port", 1883),
                options.get("output_mqtt_username", ""),
                options.get("output_mqtt_tls", False),
            )
            selected = (
                user_input["host"].strip(),
                user_input["port"],
                user_input["username"],
                user_input["tls"],
            )
            values["output_mqtt_password"] = destination_secret(
                user_input["password"],
                options.get("output_mqtt_password", ""),
                previous,
                selected,
            )
            if not user_input["host"].strip():
                values["output_mqtt_password"] = ""
            return self._save_output(values, "mqtt_output", output_schema)
        return self.async_show_form(
            step_id="mqtt_output",
            data_schema=output_schema,
        )

    def _mqtt_schema(self):
        options = self.config_entry.options
        return vol.Schema(
            {
                vol.Optional("host", default=options.get("output_mqtt_host", "")): str,
                vol.Optional("port", default=options.get("output_mqtt_port", 1883)): vol.All(
                    vol.Coerce(int), vol.Range(min=1, max=65535)
                ),
                vol.Optional("username", default=options.get("output_mqtt_username", "")): str,
                vol.Optional("password", default=""): selector.TextSelector(
                    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
                ),
                vol.Optional("tls", default=options.get("output_mqtt_tls", False)): bool,
                vol.Optional(
                    "topic", default=options.get("output_mqtt_topic", "energy/growatt")
                ): str,
            }
        )
