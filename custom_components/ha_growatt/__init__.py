"""HA-specific support for the app's MQTT service, without duplicate sensors."""

import json
import re
from collections import deque
from datetime import timedelta

from homeassistant.components import mqtt
from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .const import DEFAULTS, DOMAIN, GUIDE
from .health import issues

_IDENTITY = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


async def async_setup_entry(hass, entry):
    from .migration import register

    companion = Companion(hass, entry)
    entry.runtime_data = companion
    await companion.start()
    register(hass)
    entry.async_on_unload(entry.add_update_listener(reload_options))
    return True


async def reload_options(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    entry.runtime_data.stop()
    hass.services.async_remove(DOMAIN, "adopt_history")
    return True


class Companion:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.options = DEFAULTS | entry.options
        self.started = dt_util.utcnow()
        self.service = {}
        self.service_seen = None
        self.devices = {}
        self.current_issues = {}
        self.unsubscribers = []
        self.recent_events = deque(maxlen=256)
        self.closed = False

    async def start(self):
        try:
            for topic in ("ha_growatt/+/status", "ha_growatt/events/buffered"):
                self.unsubscribers.append(
                    await mqtt.async_subscribe(self.hass, topic, self.receive)
                )
            self.unsubscribers.append(
                async_track_time_interval(self.hass, self.evaluate, timedelta(seconds=30))
            )
            self.unsubscribers.append(
                async_track_state_change_event(self.hass, ["sun.sun"], self.sun_changed)
            )
        except BaseException:
            self.stop()
            raise

    @callback
    def receive(self, message):
        if self.closed or len(message.payload) > 65536:
            return
        try:
            data = json.loads(message.payload)
            if not isinstance(data, dict):
                return
            if message.topic == "ha_growatt/events/buffered":
                self.buffered(message, data)
                return
            identity = message.topic.split("/")[1]
            if identity == "service":
                if type(data.get("online")) is not bool:
                    return
                counts = data.get("observations", {})
                if not isinstance(counts, dict) or any(
                    type(v) is not int or v < 0 for v in counts.values()
                ):
                    return
                self.service = {
                    "online": data["online"],
                    "observations": {
                        key: counts[key]
                        for key in (
                            "announcements",
                            "announcement_warnings",
                            "measurements",
                            "failed_measurements",
                            "incomplete_fields",
                            "buffered_records",
                        )
                        if key in counts
                    },
                }
                # A retained online announcement is not evidence of a live app.
                if not message.retain:
                    self.service_seen = dt_util.utcnow()
            elif _IDENTITY.fullmatch(identity) and (
                identity in self.devices or len(self.devices) < 128
            ):
                if data.get("schema") != 1 or not isinstance(data.get("last_record"), str):
                    return
                self.devices[identity] = {"last_record": data["last_record"]}
            self.evaluate(dt_util.utcnow())
        except (ValueError, TypeError, KeyError):
            return

    @callback
    def buffered(self, message, data):
        if not self.options["buffered_events"] or message.retain or data.get("schema") != 1:
            return
        identity = data.get("inverter")
        values = data.get("values")
        if (
            not isinstance(identity, str)
            or not _IDENTITY.fullmatch(identity)
            or not isinstance(values, dict)
        ):
            return
        stamp = dt_util.parse_datetime(data.get("recorded_at", ""))
        if (
            stamp is None
            or len(values) > 512
            or any(
                not isinstance(key, str) or len(key) > 64 or type(value) is not int
                for key, value in values.items()
            )
        ):
            return
        payload = {"inverter": identity, "recorded_at": data["recorded_at"], "values": values}
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
        sun = self.hass.states.get("sun.sun")
        wanted = issues(
            now,
            self.started,
            bool(sun and sun.state == "above_horizon"),
            get_astral_event_date(self.hass, "sunrise", now),
            self.service,
            self.service_seen,
            self.devices,
            self.options,
        )
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

    @callback
    def stop(self):
        self.closed = True
        for unsubscribe in self.unsubscribers:
            unsubscribe()
        self.unsubscribers.clear()
        for key in self.current_issues:
            ir.async_delete_issue(self.hass, DOMAIN, key)
        self.current_issues.clear()
