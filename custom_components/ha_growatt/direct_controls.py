"""Guarded inverter settings for the native Home Assistant receiver."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from homeassistant.exceptions import HomeAssistantError

from ha_growatt.capabilities import capabilities
from ha_growatt.controls import Control, read_setting, write_setting
from ha_growatt.schedules import Period, read_period, write_period


@dataclass
class SettingState:
    profile: str
    session: int
    values: dict[str, int] = field(default_factory=dict)
    periods: dict[str, Period] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    refresh_at: float = 0


class DirectControls:
    """Offer settings only after a current-session read succeeds."""

    def __init__(self, hub) -> None:
        self.hub = hub
        self.states: dict[str, SettingState] = {}
        self.listeners: set[Callable[[str], None]] = set()
        self.tasks: set[asyncio.Task] = set()
        self.closed = False

    @property
    def enabled(self) -> bool:
        return self.hub.options.get("enable_controls") is True

    def _transport(self):
        return self.hub.receiver.control_transport

    def _session(self, identity: str) -> int | None:
        transport = self._transport()
        return transport.session_key(identity) if transport else None

    def _model(self, identity: str) -> tuple[str, str]:
        selected = self.hub.options.get("control_models", {}).get(identity, "auto")
        exact = self.hub.options.get("hardware_models", {}).get(identity, "")
        return selected, exact

    def _capability(self, identity: str, profile: str):
        selected, exact = self._model(identity)
        experimental = self.hub.options.get("experimental_controls") is True
        allowed = capabilities(profile, selected, experimental, exact)
        name = re.sub(r"\s+", " ", exact.strip().upper()).removeprefix("GROWATT ")
        # A decoder layout is not evidence that battery registers are safe to write.
        battery_match = (
            (selected == "min_tl_xh" and bool(re.fullmatch(r"MIN \d+TL-XH", name)))
            or (selected == "mod_tl3_xh" and bool(re.fullmatch(r"(?:MOD|MID) \d+TL3-XH", name)))
            or (selected == "sph" and name.startswith("SPH "))
            or (selected == "spa" and name.startswith("SPA "))
        )
        controls = tuple(
            control
            for control in allowed.controls
            if control.key == "output_limit" or (experimental and battery_match)
        )
        periods = allowed.schedules if experimental and battery_match else ()
        return controls, periods

    def subscribe(self, listener: Callable[[str], None]) -> Callable[[], None]:
        self.listeners.add(listener)
        for identity in self.states:
            listener(identity)
        return lambda: self.listeners.discard(listener)

    def _notify(self, identity: str) -> None:
        for listener in tuple(self.listeners):
            listener(identity)

    def observe(self, reading) -> None:
        if self.closed or not self.enabled or reading.restored:
            return
        identity = reading.identity
        session = self._session(identity)
        if session is None:
            return
        profile = reading.snapshot.telemetry.profile
        state = self.states.get(identity)
        if state is None or (state.profile, state.session) != (profile, session):
            state = SettingState(profile, session)
            self.states[identity] = state
            self._notify(identity)
        now = asyncio.get_running_loop().time()
        if now >= state.refresh_at:
            state.refresh_at = now + 300
            task = asyncio.create_task(self.refresh(identity))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    def available(self, identity: str) -> bool:
        state = self.states.get(identity)
        return bool(
            self.enabled
            and not self.closed
            and state is not None
            and self._session(identity) == state.session
        )

    def controls(self, identity: str) -> tuple[Control, ...]:
        state = self.states.get(identity)
        if not state or not self.available(identity):
            return ()
        allowed, _ = self._capability(identity, state.profile)
        return tuple(control for control in allowed if control.key in state.values)

    def periods(self, identity: str) -> tuple[str, ...]:
        state = self.states.get(identity)
        if not state or not self.available(identity):
            return ()
        _, allowed = self._capability(identity, state.profile)
        return tuple(key for key in allowed if key in state.periods)

    def value(self, identity: str, key: str):
        state = self.states.get(identity)
        return state.values.get(key) if state else None

    def period(self, identity: str, key: str) -> Period | None:
        state = self.states.get(identity)
        return state.periods.get(key) if state else None

    async def refresh(self, identity: str) -> None:
        state = self.states.get(identity)
        transport = self._transport()
        if not state or transport is None or not self.available(identity):
            return
        async with state.lock:
            controls, periods = self._capability(identity, state.profile)
            for control in controls:
                if not self.available(identity) or self.states.get(identity) is not state:
                    return
                try:
                    value = await read_setting(transport, identity, control)
                except (ValueError, ConnectionError, TimeoutError, OSError):
                    state.values.pop(control.key, None)
                else:
                    if self.available(identity) and self.states.get(identity) is state:
                        state.values[control.key] = control.display(value)
            for key in periods:
                if not self.available(identity) or self.states.get(identity) is not state:
                    return
                try:
                    period = await read_period(transport, identity, key)
                except (ValueError, ConnectionError, TimeoutError, OSError):
                    state.periods.pop(key, None)
                else:
                    if self.available(identity) and self.states.get(identity) is state:
                        state.periods[key] = period
            state.refresh_at = asyncio.get_running_loop().time() + 300
        self._notify(identity)

    def check_sessions(self) -> None:
        for identity, state in self.states.items():
            if self._session(identity) != state.session and (state.values or state.periods):
                state.values.clear()
                state.periods.clear()
                self._notify(identity)

    def _command(self, identity: str, key: str, *, period: bool = False):
        state = self.states.get(identity)
        transport = self._transport()
        if state is None or transport is None or not self.available(identity):
            raise HomeAssistantError("The datalogger is not connected")
        allowed, periods = self._capability(identity, state.profile)
        if period:
            if key not in periods or key not in state.periods:
                raise HomeAssistantError("Read this schedule before changing it")
            target = key
        else:
            target = next((control for control in allowed if control.key == key), None)
            if target is None or key not in state.values:
                raise HomeAssistantError("Read this setting before changing it")
        return state, transport, target

    async def set_control(self, identity: str, key: str, value: int) -> None:
        state, transport, control = self._command(identity, key)
        try:
            control.validate(value)
        except ValueError as error:
            raise HomeAssistantError(str(error)) from error
        deadline = asyncio.get_running_loop().time() + 15

        def current():
            if (
                self.closed
                or self.states.get(identity) is not state
                or not self.available(identity)
                or key not in state.values
                or asyncio.get_running_loop().time() > deadline
            ):
                raise ValueError("The command expired or the connection changed; nothing sent")

        async with state.lock:
            try:
                current()
                result = await write_setting(
                    transport, identity, control, value, before_send=current
                )
            except (ValueError, ConnectionError, TimeoutError, OSError) as error:
                state.values.pop(key, None)
                state.refresh_at = 0
                self._notify(identity)
                raise HomeAssistantError(str(error)) from error
            state.values[key] = control.display(result)
        self._notify(identity)

    async def set_period(self, identity: str, key: str, part: str, value) -> None:
        if part not in {"start", "end", "enabled"}:
            raise HomeAssistantError("Unsupported schedule field")
        state, transport, _ = self._command(identity, key, period=True)
        deadline = asyncio.get_running_loop().time() + 30

        def current():
            if (
                self.closed
                or self.states.get(identity) is not state
                or not self.available(identity)
                or key not in state.periods
                or asyncio.get_running_loop().time() > deadline
            ):
                raise ValueError("The schedule or connection changed; read it again")

        async with state.lock:
            try:
                current()
                expected = state.periods[key]
                updated = replace(expected, **{part: value})
                result = await write_period(
                    transport, identity, key, updated, expected, before_send=current
                )
            except (ValueError, ConnectionError, TimeoutError, OSError) as error:
                state.periods.pop(key, None)
                state.refresh_at = 0
                self._notify(identity)
                raise HomeAssistantError(str(error)) from error
            state.periods[key] = result
        self._notify(identity)

    async def close(self) -> None:
        self.closed = True
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks.clear()
        self.listeners.clear()
        self.states.clear()
