"""Administrator-only diagnostic actions over the existing MQTT connection."""

import asyncio
import json
import time
import uuid

import voluptuous as vol
from homeassistant.components import mqtt
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

SERVICES = ("identify_hardware", "read_registers")


class RegisterTools:
    def __init__(self, companion):
        self.companion = companion
        self.pending = {}
        self.unsubscribe = None

    async def start(self):
        self.unsubscribe = await mqtt.async_subscribe(
            self.companion.hass, "ha_growatt/diagnostics/response/+", self.receive
        )
        common = {vol.Required("device_id"): cv.string}
        for name in SERVICES:
            schema = dict(common)
            if name == "read_registers":
                schema.update(
                    {
                        vol.Required("start"): vol.All(int, vol.Range(min=0, max=65535)),
                        vol.Optional("count", default=1): vol.All(int, vol.Range(min=1, max=32)),
                        vol.Optional("previous"): vol.Match(r"^[0-9a-f]{32}$"),
                    }
                )
            self.companion.hass.services.async_register(
                DOMAIN,
                name,
                self.handle,
                schema=vol.Schema(schema),
                supports_response=SupportsResponse.ONLY,
            )

    async def handle(self, call):
        hass = self.companion.hass
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized()
        device = dr.async_get(hass).async_get(call.data["device_id"])
        identities = (
            [
                identity
                for domain, identity in device.identifiers
                if domain == "mqtt" and identity in self.companion.devices
            ]
            if device
            else []
        )
        if len(identities) != 1:
            raise HomeAssistantError("Choose a discovered HA Growatt inverter device")
        if self.companion.closed or len(self.pending) >= 4:
            raise HomeAssistantError("Diagnostic tools are unavailable or busy")
        request_id = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        identity = identities[0]
        self.pending[request_id] = (identity, future)
        data = dict(call.data)
        data.pop("device_id")
        data.update(
            request_id=request_id,
            identity=identity,
            expires=time.time() + 50,
            operation="identify" if call.service == "identify_hardware" else "read",
        )
        try:
            async with asyncio.timeout(55):
                await mqtt.async_publish(
                    hass, "ha_growatt/diagnostics/request", json.dumps(data), qos=0, retain=False
                )
                result = await future
            if "error" in result:
                raise HomeAssistantError(result["error"])
            return result
        except TimeoutError as error:
            raise HomeAssistantError(
                "No diagnostic reply. Check that the app is running, updated "
                "and connected to this broker."
            ) from error
        finally:
            self.pending.pop(request_id, None)

    @callback
    def receive(self, message):
        if message.retain or getattr(message, "dup", False) or len(message.payload) > 16384:
            return
        key = message.topic.rsplit("/", 1)[-1]
        pending = self.pending.get(key)
        if pending is None or pending[1].done():
            return
        try:
            data = json.loads(message.payload)
            if (
                isinstance(data, dict)
                and data.get("identity") == pending[0]
                and isinstance(data.get("result"), dict)
            ):
                pending[1].set_result(data["result"])
        except (ValueError, TypeError):
            return

    def stop(self):
        if self.unsubscribe:
            self.unsubscribe()
            self.unsubscribe = None
        for _, future in self.pending.values():
            if not future.done():
                future.set_exception(
                    HomeAssistantError("HA Growatt was unloaded; retry after it loads")
                )
        self.pending.clear()
        for name in SERVICES:
            self.companion.hass.services.async_remove(DOMAIN, name)
