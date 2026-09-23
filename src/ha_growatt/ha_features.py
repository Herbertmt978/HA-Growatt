"""Additional MQTT entities without changing existing telemetry discovery."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from .capabilities import capabilities
from .controls import read_setting, write_setting
from .dataloggers import discovery as logger_discovery
from .dataloggers import logger_identifier
from .discovery import validate_identity
from .reading_health import clock_health, reading_health
from .register_diagnostics import RegisterDiagnostics
from .schedules import Period, read_period, schedule_keys, write_period

_LOG = logging.getLogger(__name__)


@dataclass
class Device:
    identity: str
    profile: str
    last_seen: float
    last_record: str
    logger: str = ""
    upload_interval: float | None = None
    measurement_session: int | None = None
    decode_errors: int = 0
    readings: int = 0
    values: dict[str, int] = field(default_factory=dict)
    schedules: dict[str, Period] = field(default_factory=dict)
    command_result: str = "No command sent"
    firmware_changed: bool = False
    health: dict = field(default_factory=dict)
    clock: dict = field(default_factory=dict)
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
    if device.logger:
        common["device"]["via_device"] = logger_identifier(device.logger)
    allowed = capabilities(device.profile, model, experimental, (hardware or {}).get("model", ""))
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
        ("operating_state", "Operating state"),
        ("fault_description", "Reported main fault"),
        ("clock_status", "Reported clock"),
        ("write_conflict", "Setting changes"),
        ("connection", "Data connection"),
        ("profile", "Decoder profile"),
        ("capabilities", "Control capabilities"),
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
        for control in allowed.controls:
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
        for key in allowed.schedules:
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
        dataloggers=None,
        refresh_seconds=300,
    ) -> None:
        self.publisher, self.transport, self.pipeline = publisher, transport, pipeline
        self.controls = controls
        self.experimental = experimental
        self.models = models or {}
        self.hardware = hardware or {}
        self.dataloggers = dataloggers or {}
        self._logger_announced = {}
        self._logger_history = {}
        self.refresh_seconds = refresh_seconds
        self.devices: dict[str, Device] = {}
        for identity, snapshot in getattr(publisher, "snapshots", {}).items():
            self.devices[identity] = Device(
                identity,
                snapshot.telemetry.profile,
                float("-inf"),
                snapshot.received_at.isoformat(),
                logger=self._logger_identity(snapshot.telemetry),
            )
        self._announced = {}
        self._topics = {}
        self._tasks = []
        self._commands = asyncio.Queue(16)
        self._loop = None
        self.rejected_commands = 0
        self.diagnostics = RegisterDiagnostics(self)
        self._diagnostic_requests = asyncio.Queue(4)
        self._diagnostic_seen = {}

    @staticmethod
    def _logger_identity(telemetry):
        identity = telemetry.values.get("datalogserial", "")
        try:
            validate_identity(identity)
        except (ValueError, TypeError):
            return ""
        return identity

    def capability(self, device):
        return capabilities(
            device.profile,
            self.models.get(device.identity, "auto"),
            self.experimental,
            self.hardware.get(device.identity, {}).get("model", ""),
        )

    def capability_status(self, device):
        allowed = self.capability(device)
        connected = self.transport.connection(device.identity) != "disconnected"

        def reason(key):
            if not self.controls:
                return "Controls are disabled"
            if not connected:
                return "Datalogger is disconnected"
            if key not in device.values:
                return "Waiting for a successful setting readback"
            return "Available; writes are checked by reading back the result"

        return {
            "family": allowed.model_family,
            "battery": allowed.battery,
            "explanation": allowed.explanation if self.controls else "Controls are disabled",
            "schedules": list(allowed.schedules) if self.controls else [],
            "settings": [
                {"key": control.key, "label": control.label, "reason": reason(control.key)}
                for control in allowed.controls
            ],
        }

    def command_context(self, device):
        return (
            device.profile,
            self.models.get(device.identity, "auto"),
            self.experimental,
            self.hardware.get(device.identity, {}).get("model", ""),
            self.hardware.get(device.identity, {}).get("firmware", ""),
        )

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
        logger = self._logger_identity(telemetry)
        session = self.transport.session_key(identity) if self.transport else None
        elapsed = now - device.last_seen
        device.upload_interval = (
            round(elapsed, 1)
            if device.readings
            and logger == device.logger
            and session is not None
            and session == device.measurement_session
            and 1 <= elapsed <= 3600
            else None
        )
        device.logger = logger
        device.measurement_session = session
        device.last_seen = now
        device.last_record = datetime.now(UTC).isoformat()
        device.decode_errors += telemetry.decode_errors
        device.readings += 1
        device.health = reading_health(
            telemetry.values, telemetry.profile, self.capability(device).model_family
        )
        settings = getattr(self.pipeline, "settings", None)
        timezone = settings.runtime.policy.timezone if settings else "local"
        device.clock = clock_health(telemetry.recorded_at, timezone)

    def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        self.publisher.command_callback = self._message
        self._tasks = [
            asyncio.create_task(self._statuses()),
            asyncio.create_task(self._control_loop()),
            asyncio.create_task(self._refresh_loop()),
            asyncio.create_task(self._diagnostic_loop()),
        ]

    def _message(self, message) -> None:
        if message.topic == "ha_growatt/diagnostics/request":
            if (
                not message.retain
                and not getattr(message, "dup", False)
                and len(message.payload) <= 1024
                and self._loop is not None
                and not self._loop.is_closed()
            ):
                self._loop.call_soon_threadsafe(self._diagnostic_enqueue, bytes(message.payload))
            return
        if not self.controls or message.retain or getattr(message, "dup", False):
            return
        if len(message.payload) > 32 or len(message.topic) > 128:
            return
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._enqueue, message.topic, bytes(message.payload))

    def _diagnostic_enqueue(self, payload):
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                return
            key, expires = data.get("request_id"), data.get("expires")
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[0-9a-f]{32}", key)
                or type(expires) not in (int, float)
                or not time.time() < expires <= time.time() + 60
            ):
                return
            now = time.time()
            self._diagnostic_seen = {k: v for k, v in self._diagnostic_seen.items() if v > now}
            if key in self._diagnostic_seen or len(self._diagnostic_seen) >= 128:
                return
            if self._diagnostic_requests.full():
                return
            self._diagnostic_seen[key] = expires
            # Snapshot the session when admitted, not after waiting behind another read.
            identity = data.get("identity")
            if not isinstance(identity, str) or identity not in self.devices:
                return
            self._diagnostic_requests.put_nowait(
                (
                    data,
                    self.transport.session_key(identity),
                    self.command_context(self.devices[identity]),
                )
            )
        except (ValueError, TypeError):
            return

    async def _diagnostic_loop(self):
        while True:
            data, session, context = await self._diagnostic_requests.get()
            try:
                identity = data["identity"]
                if (
                    data["expires"] <= time.time()
                    or session != self.transport.session_key(identity)
                    or context != self.command_context(self.devices[identity])
                ):
                    result = {"error": "Diagnostic request expired or the connection changed"}
                else:
                    try:
                        async with asyncio.timeout(max(0, data["expires"] - time.time())):
                            result = await self.diagnostics.run(data)
                    except (ValueError, ConnectionError, TimeoutError) as error:
                        result = {"error": str(error) or "Diagnostic read timed out"}
                await self.publisher._send(
                    "ha_growatt/diagnostics/response/" + data["request_id"],
                    json.dumps({"identity": identity, "result": result}),
                    False,
                )
            except Exception:
                _LOG.warning("Diagnostic request could not be completed")
            finally:
                self._diagnostic_requests.task_done()

    def _enqueue(self, topic: str, payload: bytes) -> None:
        if self._loop is None:
            return
        if self._commands.full():
            self.rejected_commands += 1
        else:
            pieces = topic.split("/")
            if len(pieces) == 4:
                self._commands.put_nowait(
                    (
                        topic,
                        payload,
                        self.transport.session_key(pieces[1]),
                        self._loop.time(),
                        self.command_context(self.devices[pieces[1]])
                        if pieces[1] in self.devices
                        else None,
                    )
                )

    def reading_diagnostics(self, device):
        recent = asyncio.get_running_loop().time() - device.last_seen < 900
        current = recent and device.readings > 0
        clock = (
            device.clock
            if current
            else {
                "state": "Waiting for fresh readings",
                "offset_seconds": None,
                "reference": "Not checked",
            }
        )
        health = device.health if current else {}
        audit = getattr(self.transport, "write_status", None)
        return {
            "operating_state": health.get("operating_state", "Waiting for fresh readings"),
            "fault_description": health.get("fault_description", "Waiting for fresh readings"),
            "clock_status": clock.get("state", "Not reported"),
            "clock": clock,
            "write_conflict": audit(device.identity) if audit else "Not observed",
            "reported_codes": {
                k: health.get(k) for k in ("state_code", "fault_code", "warning_code")
            },
        }

    async def publish_status(self, device: Device) -> None:
        model = self.models.get(device.identity, "auto")
        hardware = self.hardware.get(device.identity, {})
        fingerprint = (
            self.publisher.generation,
            device.profile,
            model,
            self.experimental,
            tuple(sorted(hardware.items())),
            device.logger,
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
        # A refresh can remove settings while MQTT publication awaits a reply.
        # Keep the advertised keys and their values together for this update.
        values = dict(device.values)
        schedules = dict(device.schedules)
        state = {
            "connected": asyncio.get_running_loop().time() - device.last_seen < 900,
            "socket_connected": connection != "disconnected",
            "connection": connection,
            "profile": device.profile,
            "capabilities": self.capability(device).explanation
            if self.controls
            else "Controls are disabled",
            "readings": device.readings,
            "decode_errors": device.decode_errors,
            "last_record": device.last_record,
            "fallbacks": self.transport.stats.fallback_connections,
            "output_failures": self.pipeline.failures,
            "command_result": device.command_result,
            "settings": sorted(values),
            "schedules": sorted(schedules),
            "rejected_commands": self.rejected_commands,
            **self.reading_diagnostics(device),
            "schema": 1,
        }
        await self.publisher._send(f"ha_growatt/{device.identity}/status", json.dumps(state), False)
        for key, value in values.items():
            await self.publisher._send(
                f"ha_growatt/{device.identity}/settings/{key}", str(value), False
            )
        for key, period in schedules.items():
            await self.publisher._send(
                f"ha_growatt/{device.identity}/period/{key}", json.dumps(asdict(period)), False
            )

    def logger_statuses(self):
        observed = getattr(self.transport, "loggers", {})
        identities = set(observed) | {d.logger for d in self.devices.values() if d.logger}
        result = {}
        for identity in sorted(identities):
            logger = observed.get(identity)
            devices = [d for d in self.devices.values() if d.logger == identity]
            latest = max(devices, key=lambda d: d.last_seen, default=None)
            connection = self.transport.logger_connection(identity) if logger else "disconnected"
            result[identity] = {
                "connection": connection,
                "last_contact": logger.last_contact if logger else None,
                "upload_interval": latest.upload_interval
                if latest
                and connection != "disconnected"
                and latest.measurement_session == self.transport.session_key(latest.identity)
                else None,
                "reconnects": max(0, logger.connections - 1) if logger else 0,
                "history": list(logger.reconnects) if logger else [],
            }
        return result

    async def publish_loggers(self):
        for identity, state in self.logger_statuses().items():
            hardware = self.dataloggers.get(identity, {})
            fingerprint = self.publisher.generation, tuple(sorted(hardware.items()))
            if self._logger_announced.get(identity) != fingerprint:
                for topic, config in logger_discovery(identity, hardware).items():
                    await self.publisher._send(topic, json.dumps(config), True)
                self._logger_announced[identity] = fingerprint
            state = dict(state)
            history = state.pop("history")
            root = f"ha_growatt/logger/{identity}"
            history_fingerprint = self.publisher.generation, tuple(history)
            if self._logger_history.get(identity) != history_fingerprint:
                await self.publisher._send(
                    f"{root}/history", json.dumps({"recent_reconnections": history}), False
                )
                self._logger_history[identity] = history_fingerprint
            await self.publisher._send(f"{root}/status", json.dumps(state), False)

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
            try:
                await self.publish_loggers()
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
            allowed = self.capability(device)
            for control in allowed.controls:
                try:
                    value = await read_setting(self.transport, device.identity, control)
                except (ValueError, ConnectionError, TimeoutError):
                    device.values.pop(control.key, None)
                    continue
                if profile != device.profile:
                    return
                device.values[control.key] = control.display(value)
            for key in allowed.schedules:
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
        profile = self.command_context(device)
        deadline = (
            received_at if received_at is not None else asyncio.get_running_loop().time()
        ) + 15

        def check_current():
            if (
                session is None
                or self.transport.session_key(device.identity) != session
                or profile != self.command_context(device)
                or asyncio.get_running_loop().time() > deadline
            ):
                raise ValueError("Command expired or the connection/model changed; nothing sent")

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
                    allowed = self.capability(device)
                    for period_key in allowed.schedules:
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
                        (c for c in allowed.controls if c.key == key),
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
        if not self.controls or device is None or key not in self.capability(device).schedules:
            raise ValueError("This inverter has no enabled experimental schedule profile")
        session = self.transport.session_key(identity)
        profile = self.command_context(device)
        deadline = asyncio.get_running_loop().time() + 30

        def check_current():
            if (
                session is None
                or self.transport.session_key(identity) != session
                or profile != self.command_context(device)
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
            topic, payload, session, queued_at, context = await self._commands.get()
            try:
                identity = topic.split("/")[1]
                if (
                    asyncio.get_running_loop().time() - queued_at > 15
                    or session is None
                    or self.transport.session_key(identity) != session
                    or identity not in self.devices
                    or context != self.command_context(self.devices[identity])
                ):
                    self.rejected_commands += 1
                    if device := self.devices.get(identity):
                        device.command_result = (
                            "Command expired or the connection/model changed; nothing sent"
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
