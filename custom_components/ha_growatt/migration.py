"""Preview and explicitly adopt an unused historical entity ID through HA."""

from functools import partial

import voluptuous as vol
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_metadata
from homeassistant.components.sensor import UNIT_CONVERTERS
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN


def register(hass):
    async def adopt(call):
        if call.context.user_id:
            user = await hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized()
        return await adoption(hass, **call.data)

    hass.services.async_register(
        DOMAIN,
        "adopt_history",
        adopt,
        schema=vol.Schema(
            {
                vol.Required("target_entity"): cv.entity_id,
                vol.Required("source_entity_id"): cv.entity_id,
                vol.Optional("confirm", default=False): cv.boolean,
                vol.Optional("same_measurement", default=False): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.ONLY,
    )


async def adoption(hass, target_entity, source_entity_id, confirm=False, same_measurement=False):
    registry = er.async_get(hass)
    target = registry.async_get(target_entity)
    mqtt_target = target and target.platform == "mqtt" and target.unique_id.startswith("grott_")
    native_target = (
        target and target.platform == DOMAIN and target.unique_id.startswith("ha_growatt_direct_")
    )
    if not (mqtt_target or native_target):
        raise HomeAssistantError("Choose an HA Growatt measurement sensor")
    if target_entity == source_entity_id or not source_entity_id.startswith("sensor."):
        raise HomeAssistantError("Choose a different historical sensor ID")
    if registry.async_get(source_entity_id) or hass.states.get(source_entity_id):
        raise HomeAssistantError(
            "The historical ID still has an owner. Disabling an entity does not release it."
        )
    state = hass.states.get(target_entity)
    if state is None or state.state in {"unknown", "unavailable"}:
        raise HomeAssistantError("Wait for a valid reading from the target sensor")
    unit = state.attributes.get("unit_of_measurement")
    state_class = state.attributes.get("state_class")
    if state_class not in {"measurement", "total", "total_increasing"}:
        raise HomeAssistantError("The target does not record numeric statistics")
    if "recorder" not in hass.config.components:
        raise HomeAssistantError("History adoption needs Recorder")
    metadata = await get_instance(hass).async_add_executor_job(
        partial(get_metadata, hass, statistic_ids={source_entity_id, target_entity})
    )
    if source_entity_id not in metadata:
        raise HomeAssistantError("No statistics exist for the historical ID")
    source = metadata[source_entity_id][1]
    source_unit = source["unit_of_measurement"]
    converter = UNIT_CONVERTERS.get(state.attributes.get("device_class"))
    if unit != source_unit and not (
        converter and unit in converter.VALID_UNITS and source_unit in converter.VALID_UNITS
    ):
        raise HomeAssistantError("The units are not compatible")
    if bool(source.get("has_sum")) != (state_class in {"total", "total_increasing"}):
        raise HomeAssistantError("A cumulative counter cannot adopt a measurement's history")
    report = {
        "source_entity_id": source_entity_id,
        "target_entity": target_entity,
        "source_unit": source_unit,
        "target_unit": unit,
        "compatible": True,
        "applied": False,
        "target_has_separate_statistics": target_entity in metadata,
        "guidance": "Compare the same inverter and measurement, including daily versus lifetime "
        "energy. Export a backup before confirming. Existing target statistics stay "
        "separate; no historical rows are deleted or merged.",
    }
    if confirm:
        if not same_measurement:
            raise HomeAssistantError("Confirm that both IDs measure the same inverter and quantity")
        # Metadata was read off-thread. Recheck identity ownership after that await.
        if (
            registry.async_get(target_entity) != target
            or registry.async_get(source_entity_id)
            or hass.states.get(source_entity_id)
        ):
            raise HomeAssistantError("Entity ownership changed; preview the migration again")
        registry.async_update_entity(target_entity, new_entity_id=source_entity_id)
        report["applied"] = True
    return report
