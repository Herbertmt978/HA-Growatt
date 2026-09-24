"""Native Home Assistant receiver for installations without an app or MQTT."""

from __future__ import annotations

import json
import re
from collections import deque
from datetime import timedelta

import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, Unauthorized
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from ha_growatt.native_outputs import NativeOutputs
from ha_growatt.native_receiver import NativeReceiver

from .const import DEFAULTS, DOMAIN, GUIDE
from .direct_controls import DirectControls
from .health import issues
from .output_config import native_output_settings

_IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class DirectHub:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.options = DEFAULTS | entry.options
        self.outputs = NativeOutputs(native_output_settings(self.options))
        self.receiver = NativeReceiver(
            port=self.options.get("port", entry.data["port"]),
            forward_cloud=self.options.get("forward_cloud", entry.data["forward_cloud"]),
            state_path=hass.config.path(".storage", "ha_growatt_native_readings.json"),
            family=self.options.get("family", entry.data.get("family", "default")),
            unknown_diagnostics=self.options.get("unknown_shine_diagnostics", False),
            on_telemetry=self.outputs.publish,
        )
        self.controls = DirectControls(self)
        self.started = dt_util.utcnow()
        self.current_issues = {}
        self.unsubscribers = []
        self.closed = False
        self.recent_events = deque(maxlen=256)

    async def start(self):
        self.outputs.start()
        try:
            await self.receiver.start()
        except BaseException:
            await self.outputs.close()
            raise
        self.unsubscribers.append(self.receiver.subscribe(self.reading))
        self.unsubscribers.append(self.receiver.subscribe_buffered(self.buffered))
        self.unsubscribers.append(
            async_track_time_interval(self.hass, self.evaluate, timedelta(seconds=30))
        )
        self.unsubscribers.append(
            async_track_state_change_event(self.hass, ["sun.sun"], self.sun_changed)
        )
        self.hass.services.async_register(
            DOMAIN,
            "capture_evidence",
            self.capture_evidence,
            schema=vol.Schema({vol.Required("action"): vol.In(["start", "report"])}),
            supports_response=SupportsResponse.ONLY,
        )

    async def capture_evidence(self, call):
        if call.context.user_id:
            user = await self.hass.auth.async_get_user(call.context.user_id)
            if user is None or not user.is_admin:
                raise Unauthorized()
        if self.closed:
            raise HomeAssistantError("The native receiver is not running")
        if call.data["action"] == "start":
            self.receiver.private_capture.start()
            return {"status": "Capturing packet structure for ten minutes", "frames": 0}
        return self.receiver.private_capture.export_shareable(self.receiver.decoder)

    @callback
    def reading(self, reading):
        self.controls.observe(reading)
        self.evaluate(dt_util.utcnow())

    @callback
    def buffered(self, telemetry):
        if not self.options["buffered_events"]:
            return
        identity = telemetry.device_id or telemetry.values.get("pvserial")
        if (
            not isinstance(identity, str)
            or not _IDENTITY.fullmatch(identity)
            or telemetry.recorded_at is None
        ):
            return
        values = {key: value for key, value in telemetry.values.items() if type(value) is int}
        if len(values) > 512 or any(len(key) > 64 for key in values):
            return
        payload = {
            "inverter": identity,
            "recorded_at": telemetry.recorded_at.isoformat(),
            "values": values,
        }
        signature = json.dumps(payload, sort_keys=True)
        if signature in self.recent_events:
            return
        self.recent_events.append(signature)
        self.hass.bus.async_fire("ha_growatt_buffered_record", payload)

    @callback
    def sun_changed(self, event):
        self.evaluate(dt_util.utcnow())

    @callback
    def evaluate(self, now):
        if self.closed:
            return
        self.controls.check_sessions()
        sun = self.hass.states.get("sun.sun")
        service = {
            "online": self.receiver.running,
            "observations": {
                "announcements": self.receiver.announcements,
                "announcement_warnings": self.receiver.announcement_warnings,
                "measurements": self.receiver.measurements,
                "failed_measurements": self.receiver.failed_measurements,
            },
        }
        devices = {
            identity: {"last_record": snapshot.received_at.isoformat()}
            for identity, snapshot in self.receiver.snapshots.items()
        }
        wanted = issues(
            now,
            self.started,
            bool(sun and sun.state == "above_horizon"),
            get_astral_event_date(self.hass, "sunrise", now),
            service,
            now if self.receiver.running else None,
            devices,
            self.options,
        )
        wanted = {f"direct_{key}": value for key, value in wanted.items()}
        for key in self.current_issues.keys() - wanted.keys():
            ir.async_delete_issue(self.hass, DOMAIN, key)
        for key, (translation, placeholders) in wanted.items():
            if self.current_issues.get(key) != (translation, placeholders):
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    key,
                    is_fixable=False,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=translation,
                    translation_placeholders=placeholders,
                    learn_more_url=GUIDE,
                )
        self.current_issues = wanted

    async def close(self):
        self.closed = True
        self.hass.services.async_remove(DOMAIN, "capture_evidence")
        for unsubscribe in self.unsubscribers:
            unsubscribe()
        self.unsubscribers.clear()
        for key in self.current_issues:
            ir.async_delete_issue(self.hass, DOMAIN, key)
        self.current_issues.clear()
        await self.controls.close()
        await self.receiver.close()
        await self.outputs.close()
