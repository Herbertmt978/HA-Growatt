"""Home Assistant owner for an explicitly configured direct Modbus connection."""

from __future__ import annotations

import math
from datetime import timedelta

from homeassistant.core import callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from ha_growatt.modbus_receiver import ModbusReceiver

from .const import DEFAULTS, DOMAIN, GUIDE
from .health import issues


class ModbusHub:
    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.options = DEFAULTS | entry.options
        connection = dict(entry.data) | entry.options
        self.receiver = ModbusReceiver(
            host=connection["host"],
            port=connection["port"],
            unit=connection["unit"],
            identity=connection["identity"],
            profile=connection["profile"],
            interval=connection["interval"],
            state_path=hass.config.path(
                ".storage", f"ha_growatt_modbus_{connection['identity']}.json"
            ),
        )
        self.started = dt_util.utcnow()
        self.current_issues = {}
        self.unsubscribers = []
        self.closed = False

    @property
    def stale_minutes(self):
        return max(
            self.options["stale_minutes"],
            math.ceil((self.receiver.interval + 60) / 60),
        )

    async def start(self):
        await self.receiver.start()
        self.unsubscribers.append(self.receiver.subscribe(self.reading))
        self.unsubscribers.append(
            async_track_time_interval(self.hass, self.evaluate, timedelta(seconds=30))
        )
        self.unsubscribers.append(
            async_track_state_change_event(self.hass, ["sun.sun"], self.sun_changed)
        )

    @callback
    def reading(self, reading):
        self.evaluate(dt_util.utcnow())

    @callback
    def sun_changed(self, event):
        self.evaluate(dt_util.utcnow())

    @callback
    def evaluate(self, now):
        if self.closed:
            return
        sun = self.hass.states.get("sun.sun")
        devices = {
            identity: {"last_record": snapshot.received_at.isoformat()}
            for identity, snapshot in self.receiver.snapshots.items()
        }
        wanted = issues(
            now,
            self.started,
            bool(sun and sun.state == "above_horizon"),
            get_astral_event_date(self.hass, "sunrise", now),
            {"online": self.receiver.running, "observations": {}},
            now if self.receiver.running else None,
            devices,
            self.options | {"stale_minutes": self.stale_minutes},
        )
        wanted = {f"modbus_{self.receiver.identity}_{key}": value for key, value in wanted.items()}
        if not self.receiver.connected and self.receiver.failed_measurements:
            daylight = bool(sun and sun.state == "above_horizon")
            sunrise = get_astral_event_date(self.hass, "sunrise", now)
            if daylight and (
                sunrise is None
                or now - sunrise >= timedelta(minutes=self.options["sunrise_grace_minutes"])
            ):
                wanted[f"modbus_{self.receiver.identity}_unavailable"] = (
                    "modbus_unavailable",
                    {},
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

    async def close(self):
        self.closed = True
        for unsubscribe in self.unsubscribers:
            unsubscribe()
        self.unsubscribers.clear()
        for key in self.current_issues:
            ir.async_delete_issue(self.hass, DOMAIN, key)
        self.current_issues.clear()
        await self.receiver.close()
