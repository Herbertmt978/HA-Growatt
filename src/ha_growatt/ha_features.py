"""Additional MQTT entities without changing existing telemetry discovery."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from .controls import controls_for, read_setting, write_setting
from .discovery import validate_identity
from .schedules import Period, read_period, schedule_keys, write_period

_LOG = logging.getLogger(__name__)


@dataclass
class Device:
    identity: str
    profile: str
    last_seen: float
    last_record: str
    decode_errors: int = 0
    readings: int = 0
    values: dict[str, int] = field(default_factory=dict)
    schedules: dict[str, Period] = field(default_factory=dict)
    command_result: str = "No command sent"
    refresh_at: float = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


def feature_discovery(
    device: Device, controls: bool, *, model="auto", experimental=False, hardware=None
) -> dict[str, dict]:
    identity = device.identity
    validate_identity(identity)
    root = f"ha_growatt/{identity}"
    status = f"{root}/status"
    common = {
        "device": {"identifiers": [identity], "name": identity, "manufacturer": "Growatt"},
        "state_topic": status,
        "entity_category": "diagnostic",
        "expire_after": 30,
    }
    if hardware:
        for key, destination in (("model", "model"), ("firmware", "sw_version")):
            if hardware.get(key):
                common["device"][destination] = hardware[key]
    result = {}

    def add(component, key, name, **options):
        result[f"homeassistant/{component}/ha_growatt/{identity}_{key}/config"] = {
            **common,
            "name": name,
            "unique_id": f"ha_growatt_{identity}_{key}",
            **options,
        }

    add(
        "binary_sensor",
        "connected",
        "Connected",
        device_class="connectivity",
        value_template="{{ 'ON' if value_json.connected else 'OFF' }}",
    )
    for key, name in (
        ("connection", "Data connection"),
        ("profile", "Decoder profile"),
        ("readings", "Readings received"),
        ("decode_errors", "Incomplete fields"),
        ("fallbacks", "Cloud fallbacks"),
        ("output_failures", "Output failures"),
        ("command_result", "Last command result"),
    ):
        add("sensor", key, name, value_template="{{ value_json." + key + " }}")
    availability = {
        "availability_topic": status,
        "availability_template": "{{ 'online' if value_json.socket_connected else 'offline' }}",
    }
    if controls:
        for key, name in (("refresh", "Refresh settings"), ("sync_time", "Sync datalogger time")):
            add(
                "button",
                key,
                name,
                command_topic=f"{root}/command/{key}",
                payload_press="PRESS",
                **availability,
            )
        for control in controls_for(device.profile, model, experimental):
            component = "switch" if control.switch else "number"
            options = {
                "command_topic": f"{root}/command/{control.key}",
                "state_topic": f"{root}/settings/{control.key}",
                "entity_category": "config",
                "retain": False,
                "optimistic": False,
                "availability_topic": status,
                "availability_template": "{{ 'online' if value_json.socket_connected and '"
                + control.key
                + "' in value_json.settings else 'offline' }}",
            }
            if control.switch:
                options.update(payload_on="1", payload_off="0")
            else:
                options.update(
                    min=control.minimum, max=100, step=1, mode="box", unit_of_measurement="%"
                )
            add(component, control.key, control.label, **options)
        for key in schedule_keys(device.profile, model, experimental):
            mode, slot = key.split("_")
            label = (
                "Battery-first charging" if mode == "charge" else "Grid-first discharging"
            ) + f" {slot}"
            for part in ("start", "end", "enabled"):
                options = {
                    "command_topic": f"{root}/command/{key}_{part}",
                    "state_topic": f"{root}/period/{key}",
                    "entity_category": "config",
                    "retain": False,
                    "availability_topic": status,
                    "availability_template": "{{ 'online' if value_json.socket_connected and '"
                    + key
                    + "' in value_json.schedules else 'offline' }}",
                }
                if part == "enabled":
                    options.update(
                        payload_on="1",
                        payload_off="0",
                        optimistic=False,
                        value_template="{{ '1' if value_json.enabled else '0' }}",
                    )
                else:
                    options.update(
                        min=5,
                        max=5,
                        mode="text",
                        pattern=r"(?:[01][0-9]|2[0-3]):[0-5][0-9]",
                        value_template="{{ value_json." + part + " }}",
                    )
                add(
                    "switch" if part == "enabled" else "text",
                    f"{key}_{part}",
                    label + " " + part,
                    **options,
                )
    for topic, config in result.items():
        if not topic.startswith(("homeassistant/sensor/", "homeassistant/binary_sensor/")):
            config.pop("expire_after", None)
        if topic.startswith("homeassistant/button/"):
            config.pop("state_topic", None)
        control = topic.startswith(
            (
                "homeassistant/number/",
                "homeassistant/switch/",
                "homeassistant/button/",
                "homeassistant/text/",
            )
        )
        condition = "value_json.online"
        if control:
            condition += " and '" + identity + "' in value_json.connections"
        config["availability"] = [
            {
                "topic": "ha_growatt/service/status",
                "value_template": "{{ 'online' if " + condition + " else 'offline' }}",
            }
        ]
        if "availability_topic" in config:
            config["availability"].append(
                {
                    "topic": config.pop("availability_topic"),
                    "value_template": config.pop("availability_template"),
                }
            )
        config["availability_mode"] = "all"
    return result


class HomeAssistantFeatures:
    def __init__(
        self,
        publisher,
        transport,
        pipeline,
        *,
        controls=True,
        experimental=False,
        models=None,
        hardware=None,
        refresh_seconds=300,
    ) -> None:
        self.publisher, self.transport, self.pipeline = publisher, transport, pipeline
        self.controls = controls
        self.experimental = experimental
        self.models = models or {}
        self.hardware = hardware or {}
        self.refresh_seconds = refresh_seconds
        self.devices: dict[str, Device] = {}
        for identity, snapshot in getattr(publisher, "snapshots", {}).items():
            self.devices[identity] = Device(
                identity,
                snapshot.telemetry.profile,
                float("-inf"),
                snapshot.received_at.isoformat(),
            )
        self._announced = {}
        self._topics = {}
        self._tasks = []
        self._commands = asyncio.Queue(16)
        self._loop = None
        self.rejected_commands = 0

    def remember(self, telemetry) -> None:
        identity = telemetry.device_id or telemetry.values.get("pvserial")
        if not isinstance(identity, str):
            return
        validate_identity(identity)
        now = asyncio.get_running_loop().time()
        device = self.devices.get(identity)
        if device is None:
            # Do not let arbitrary identities allocate unbounded device storage.
            if len(self.devices) >= 128:
                return
            device = Device(identity, telemetry.profile, now, "")
            self.devices[identity] = device
        if device.profile != telemetry.profile:
            device.values.clear()
            device.schedules.clear()
            device.refresh_at = 0
            device.profile = telemetry.profile
        device.last_seen = now
        device.last_record = datetime.now(UTC).isoformat()
        device.decode_errors += telemetry.decode_errors
        device.readings += 1

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.publisher.command_callback = self._message
        self._tasks = [
            asyncio.create_task(self._statuses()),
            asyncio.create_task(self._control_loop()),
            asyncio.create_task(self._refresh_loop()),
        ]

    def _message(self, message) -> None:
        if not self.controls or message.retain or getattr(message, "dup", False):
            return
        if len(message.payload) > 32 or len(message.topic) > 128:
            return
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._enqueue, message.topic, bytes(message.payload))

    def _enqueue(self, topic: str, payload: bytes) -> None:
        if self._loop is None:
            return
        if self._commands.full():
            self.rejected_commands += 1
        else:
            pieces = topic.split("/")
            if len(pieces) == 4:
                self._commands.put_nowait(
                    (topic, payload, self.transport.session_key(pieces[1]), self._loop.time())
                )

    async def publish_status(self, device: Device) -> None:
        model = self.models.get(device.identity, "auto")
        hardware = self.hardware.get(device.identity, {})
        fingerprint = (
            self.publisher.generation,
            device.profile,
            model,
            self.experimental,
            tuple(sorted(hardware.items())),
        )
        if self._announced.get(device.identity) != fingerprint:
            configs = feature_discovery(
                device,
                self.controls,
                model=model,
                experimental=self.experimental,
                hardware=hardware,
            )
            for topic, config in configs.items():
                await self.publisher._send(topic, json.dumps(config), True)
            previous = self._topics.get(device.identity, set())
            # All feature keys are finite; clear controls left by a previous
            # profile or by a restart with controls disabled.
            from .controls import CONTROLS, XH_CONTROLS

            known = {
                f"homeassistant/{'switch' if c.switch else 'number'}/ha_growatt/"
                f"{device.identity}_{c.key}/config"
                for c in CONTROLS + XH_CONTROLS
            }
            known |= {
                f"homeassistant/{'switch' if part == 'enabled' else 'text'}/ha_growatt/"
                f"{device.identity}_{key}_{part}/config"
                for key in schedule_keys("sph-6", "sph", True)
                for part in ("start", "end", "enabled")
            }
            known |= {
                f"homeassistant/button/ha_growatt/{device.identity}_{key}/config"
                for key in ("refresh", "sync_time")
            }
            for topic in sorted((previous | known) - configs.keys()):
                await self.publisher._send(topic, "", True)
            self._topics[device.identity] = set(configs)
            self._announced[device.identity] = fingerprint
        connection = self.transport.connection(device.identity)
        state = {
            "connected": asyncio.get_running_loop().time() - device.last_seen < 900,
            "socket_connected": connection != "disconnected",
            "connection": connection,
            "profile": device.profile,
            "readings": device.readings,
            "decode_errors": device.decode_errors,
            "last_record": device.last_record,
            "fallbacks": self.transport.stats.fallback_connections,
            "output_failures": self.pipeline.failures,
            "command_result": device.command_result,
            "settings": sorted(device.values),
            "schedules": sorted(device.schedules),
            "rejected_commands": self.rejected_commands,
            "schema": 1,
        }
        await self.publisher._send(f"ha_growatt/{device.identity}/status", json.dumps(state), False)
        for key, value in device.values.items():
            await self.publisher._send(
                f"ha_growatt/{device.identity}/settings/{key}", str(value), False
            )
        for key, period in device.schedules.items():
            await self.publisher._send(
                f"ha_growatt/{device.identity}/period/{key}", json.dumps(asdict(period)), False
            )

    async def _statuses(self) -> None:
        while True:
            try:
                await self.publisher._send(
                    "ha_growatt/service/status",
                    json.dumps(
                        {
                            "online": True,
                            "schema": 1,
                            "observations": asdict(self.pipeline.observations),
                            "connections": [
                                identity
                                for identity in self.devices
                                if self.transport.connection(identity) != "disconnected"
                            ],
                        }
                    ),
                    True,
                )
            except (ConnectionError, TimeoutError):
                await asyncio.sleep(2)
                continue
            for device in list(self.devices.values()):
                try:
                    await self.publish_status(device)
                except (ConnectionError, TimeoutError):
                    break
            await asyncio.sleep(2)

    async def refresh(self, device: Device) -> None:
        async with device.lock:
            profile = device.profile
            model = self.models.get(device.identity, "auto")
            for control in controls_for(profile, model, self.experimental):
                try:
                    value = await read_setting(self.transport, device.identity, control)
                except (ValueError, ConnectionError, TimeoutError):
                    device.values.pop(control.key, None)
                    continue
                if profile != device.profile:
                    return
                device.values[control.key] = control.display(value)
            for key in schedule_keys(profile, model, self.experimental):
                try:
                    period = await read_period(self.transport, device.identity, key)
                except (ValueError, ConnectionError, TimeoutError):
                    device.schedules.pop(key, None)
                    continue
                if profile != device.profile:
                    return
                device.schedules[key] = period

    async def _refresh_loop(self) -> None:
        while True:
            if self.controls and self.refresh_seconds:
                for device in list(self.devices.values()):
                    now = asyncio.get_running_loop().time()
                    if (
                        now >= device.refresh_at
                        and self.transport.connection(device.identity) != "disconnected"
                    ):
                        device.refresh_at = now + self.refresh_seconds
                        await self.refresh(device)
            await asyncio.sleep(2)

    async def execute(
        self, topic: str, payload: bytes, *, received_at: float | None = None
    ) -> None:
        pieces = topic.split("/")
        if len(pieces) != 4 or pieces[0] != "ha_growatt" or pieces[2] != "command":
            return
        device = self.devices.get(pieces[1])
        if device is None or not self.controls:
            return
        session = self.transport.session_key(device.identity)
        profile = (device.profile, self.models.get(device.identity, "auto"))
        deadline = (
            received_at if received_at is not None else asyncio.get_running_loop().time()
        ) + 15

        def check_current():
            if (
                session is None
                or self.transport.session_key(device.identity) != session
                or profile != (device.profile, self.models.get(device.identity, "auto"))
                or asyncio.get_running_loop().time() > deadline
            ):
                raise ValueError("Command expired or the datalogger reconnected; nothing sent")

        key = pieces[3]
        try:
            if key == "refresh" and payload == b"PRESS":
                await self.refresh(device)
                device.command_result = (
                    "Settings refreshed" if device.values else "No supported settings replied"
                )
            elif key == "sync_time" and payload == b"PRESS":
                async with device.lock:
                    check_current()
                    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S").encode()
                    response = await self.transport.command(
                        device.identity,
                        24,
                        b"\x00\x1f" + len(stamp).to_bytes(2, "big") + stamp,
                        before_send=check_current,
                    )
                    from .controls import response_body

                    if response_body(response) != b"\x00\x1f\0":
                        raise ValueError("The datalogger rejected the clock change")
                    device.command_result = "Datalogger accepted the clock change"
            else:
                async with device.lock:
                    check_current()
                    model = self.models.get(device.identity, "auto")
                    for period_key in schedule_keys(device.profile, model, self.experimental):
                        for part in ("start", "end", "enabled"):
                            if key == f"{period_key}_{part}":
                                if period_key not in device.schedules:
                                    raise ValueError("Read the period before changing it")
                                current = await read_period(
                                    self.transport, device.identity, period_key
                                )
                                if part == "enabled":
                                    if payload not in {b"0", b"1"}:
                                        raise ValueError("Use the enabled switch")
                                    value = payload == b"1"
                                else:
                                    value = payload.decode("ascii")
                                await self._write_period(
                                    device,
                                    period_key,
                                    replace(current, **{part: value}),
                                    current,
                                    check_current,
                                )
                                await self.publish_status(device)
                                return
                    control = next(
                        (
                            c
                            for c in controls_for(device.profile, model, self.experimental)
                            if c.key == key
                        ),
                        None,
                    )
                    if control is None or key not in device.values:
                        raise ValueError("This setting is not available on the connected inverter")
                    try:
                        number = Decimal(payload.decode("ascii"))
                    except (InvalidOperation, UnicodeError):
                        raise ValueError("Enter a whole number within the setting range") from None
                    if (
                        not number.is_finite()
                        or not 0 <= number <= 100
                        or number != number.to_integral_value()
                    ):
                        raise ValueError("Enter a whole number within the setting range")
                    value = int(number)
                    control.validate(value)
                    try:
                        applied = await write_setting(
                            self.transport,
                            device.identity,
                            control,
                            value,
                            before_send=check_current,
                        )
                    except (ValueError, ConnectionError, TimeoutError):
                        device.values.pop(key, None)
                        device.refresh_at = 0
                        raise
                    device.values[key] = control.display(applied)
                    device.command_result = f"{control.label}: applied and verified"
        except (ValueError, UnicodeError) as error:
            device.command_result = str(error)
        except (ConnectionError, TimeoutError):
            device.command_result = "No confirmation received; refresh settings before trying again"
        try:
            await self.publish_status(device)
        except (ConnectionError, TimeoutError):
            pass

    async def _write_period(self, device, key, period, expected, check_current):
        try:
            device.schedules[key] = await write_period(
                self.transport, device.identity, key, period, expected, before_send=check_current
            )
        except (ValueError, ConnectionError, TimeoutError):
            device.schedules.pop(key, None)
            device.refresh_at = 0
            raise
        device.command_result = "Period applied and verified"

    async def schedule_action(self, data):
        identity = data.get("serial")
        device = self.devices.get(identity)
        key = f"{data.get('mode')}_{data.get('slot')}"
        if (
            not self.controls
            or device is None
            or key
            not in schedule_keys(
                device.profile, self.models.get(identity, "auto"), self.experimental
            )
        ):
            raise ValueError("This inverter has no enabled experimental schedule profile")
        session = self.transport.session_key(identity)
        profile = (device.profile, self.models.get(identity, "auto"))
        deadline = asyncio.get_running_loop().time() + 30

        def check_current():
            if (
                session is None
                or self.transport.session_key(identity) != session
                or profile != (device.profile, self.models.get(identity, "auto"))
                or asyncio.get_running_loop().time() > deadline
            ):
                raise ValueError("The datalogger connection changed; read the period again")

        async with device.lock:
            check_current()
            if data.get("action") == "read":
                period = await read_period(self.transport, identity, key)
                check_current()
                device.schedules[key] = period
            elif data.get("action") == "write":
                period, expected = Period(**data["period"]), Period(**data["expected"])
                await self._write_period(device, key, period, expected, check_current)
            else:
                raise ValueError("Choose read or write")
        try:
            await self.publish_status(device)
        except (ConnectionError, TimeoutError):
            pass
        return {"period": asdict(period)}

    async def _control_loop(self) -> None:
        while True:
            topic, payload, session, queued_at = await self._commands.get()
            try:
                identity = topic.split("/")[1]
                if (
                    asyncio.get_running_loop().time() - queued_at > 15
                    or session is None
                    or self.transport.session_key(identity) != session
                ):
                    self.rejected_commands += 1
                    if device := self.devices.get(identity):
                        device.command_result = (
                            "Command expired or the datalogger reconnected; nothing sent"
                        )
                else:
                    await self.execute(topic, payload, received_at=queued_at)
            except Exception:
                _LOG.exception("Home Assistant command could not be completed")
            finally:
                self._commands.task_done()

    async def close(self) -> None:
        self.publisher.command_callback = None
        self._loop = None
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
