"""Small ingress-only setup and support page for the Home Assistant app."""

from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext, suppress
from dataclasses import asdict, replace
from importlib.resources import files

from . import __version__
from .discovery import validate_identity
from .installation import compatibility_catalogue, installation_checks, mapped_port
from .migration import migration_preview


class SupportServer:
    def __init__(
        self,
        pipeline,
        transport,
        supervisor,
        *,
        host="0.0.0.0",
        port=8099,
        allowed_peer="172.30.32.2",
    ):
        self.pipeline, self.transport, self.supervisor = pipeline, transport, supervisor
        self.host, self.port, self.allowed_peer = host, port, allowed_peer
        self._server = None
        self._tasks = set()
        self._profile_lock = asyncio.Lock()

    def status(self, *, redacted=False):
        publisher = self.pipeline.ha
        features = self.pipeline.features
        stats = self.transport.stats
        observations = asdict(self.pipeline.observations)
        warnings = []
        connected = bool(publisher and publisher._connected.is_set())
        if not connected:
            warnings.append(
                "MQTT is disconnected. Start the broker and check the app's broker settings."
            )
        if not stats.device_frames:
            warnings.append(
                "Waiting for the first datalogger packet. "
                "Check its destination IP and published TCP port; it may be asleep overnight."
            )
        elif not features or not any(item.readings for item in features.devices.values()):
            warnings.append(
                "Packets are arriving but no fresh inverter reading has decoded. "
                "Check the inverter family. New session-key encrypted loggers are not supported."
            )
        if observations["failed_measurements"]:
            warnings.append(
                "Some measurement records could not be decoded. "
                "Check the profile and download diagnostics for support."
            )
        if stats.observation_failures:
            warnings.append("A packet observer failed. Forwarding continues; download diagnostics.")
        if publisher and publisher.cache_error:
            warnings.append(
                "Restart recovery is unavailable. Check free space and app data permissions; "
                "fresh readings still work."
            )
        if features and any(
            self.transport.connection(identity) == "local" for identity in features.devices
        ):
            warnings.append(
                "Cloud forwarding is unavailable. Local readings continue. "
                + (
                    "Automatic recovery checks the cloud before reconnecting the datalogger."
                    if self.pipeline.settings.relay.cloud_recovery_seconds
                    else "Automatic recovery is disabled; waiting for the datalogger to reconnect."
                )
            )
        devices = []
        now = asyncio.get_running_loop().time()
        for index, device in enumerate(features.devices.values() if features else [], 1):
            devices.append(
                {
                    "identity": f"Inverter {index}" if redacted else device.identity,
                    "profile": device.profile if device.profile in _KNOWN_PROFILES else "custom",
                    "connection": self.transport.connection(device.identity),
                    "recent": now - device.last_seen < 900,
                    "readings": device.readings,
                    "decode_errors": device.decode_errors,
                    "restored": device.readings == 0,
                    "family": self.pipeline.settings.selection.device_families.get(
                        device.identity, self.pipeline.settings.selection.family
                    ),
                    "controls": self.pipeline.settings.runtime.control_models.get(
                        device.identity, "auto"
                    ),
                    **(
                        {}
                        if redacted
                        else self.pipeline.settings.runtime.hardware.get(device.identity, {})
                    ),
                }
            )
        result = {
            "version": __version__,
            "listener": self.transport.running,
            "mqtt_connected": connected,
            "recovery_enabled": bool(publisher and publisher._store),
            "recovery_healthy": bool(publisher and publisher._store and not publisher.cache_error),
            "experimental_controls": self.pipeline.settings.runtime.experimental_controls,
            "transport": asdict(stats),
            "observations": observations,
            "capture_active": self.pipeline.capture.active,
            "output_failures": self.pipeline.failures,
            "dropped_readings": self.pipeline.dropped,
            "devices": devices,
            "warnings": warnings,
        }
        if not redacted:
            result["installation"] = installation_checks(
                result,
                discovery_enabled=self.pipeline.settings.runtime.home_assistant,
                features_enabled=self.pipeline.settings.runtime.ha_features,
            )
        return result

    async def dispatch(self, method, path, headers, body):
        if method == "GET" and path == "/favicon.ico":
            return 204, "image/x-icon", b""
        if method == "GET" and path in {"/", "/app.js", "/setup.js", "/style.css"}:
            name = "index.html" if path == "/" else path[1:]
            content_type = {
                "index.html": "text/html",
                "app.js": "text/javascript",
                "setup.js": "text/javascript",
                "style.css": "text/css",
            }[name]
            return 200, content_type, files("ha_growatt").joinpath("web", name).read_bytes()
        if method == "GET" and path == "/api/compatibility":
            return 200, "application/json", json.dumps(compatibility_catalogue()).encode()
        if method == "GET" and path == "/api/installation":
            port = None
            if self.supervisor:
                try:
                    info = await asyncio.to_thread(self.supervisor.request, "/addons/self/info")
                    port = mapped_port(info, self.pipeline.settings.relay.listen_port)
                except (ConnectionError, KeyError, TypeError):
                    pass
            return 200, "application/json", json.dumps({"host_port": port}).encode()
        if method == "GET" and path == "/api/status":
            return 200, "application/json", json.dumps(self.status()).encode()
        if method == "GET" and path == "/api/diagnostics":
            return (
                200,
                "application/json",
                json.dumps(self.status(redacted=True), indent=2).encode(),
            )
        if method == "GET" and path == "/api/capture":
            return (
                200,
                "application/json",
                json.dumps(self.pipeline.capture.export(), indent=2).encode(),
            )
        if (
            method != "POST"
            or headers.get("x-ha-growatt") != "1"
            or headers.get("content-type") != "application/json"
        ):
            return 405, "application/json", b'{"error":"Unsupported request"}'
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Expected an object")
        if path == "/api/capture/start":
            self.pipeline.capture.start()
            result = {
                "message": "Recording packet summaries for ten minutes. "
                "No payloads or readings are saved."
            }
        elif path == "/api/capture/stop":
            self.pipeline.capture.stop()
            result = {"message": "Capture stopped. The download is ready."}
        elif path == "/api/hardware/read":
            from .hardware import read_firmware

            features = self.pipeline.features
            identity = data.get("serial", "")
            device = features.devices.get(identity) if features else None
            if not device or not features.controls or self.supervisor is None:
                raise ValueError(
                    "Firmware reading needs a connected inverter with controls enabled"
                )
            async with self._profile_lock, device.lock:
                firmware = await read_firmware(self.transport, identity)
                updated = await self.supervisor.profile(
                    identity,
                    self.pipeline.settings.selection.device_families.get(
                        identity, self.pipeline.settings.selection.family
                    ),
                    features.models.get(identity, "auto"),
                    hardware={"firmware": firmware},
                )
                self.pipeline.settings = replace(
                    self.pipeline.settings,
                    runtime=replace(
                        self.pipeline.settings.runtime, hardware=updated.runtime.hardware
                    ),
                )
                features.hardware = updated.runtime.hardware
            result = {"message": "Firmware read and saved.", "firmware": firmware}
        elif path == "/api/profiles":
            identity = data.get("serial", "")
            validate_identity(identity)
            if self.supervisor is None:
                raise ValueError("Profile editing needs the Home Assistant app")
            device = (
                self.pipeline.features.devices.get(identity) if self.pipeline.features else None
            )
            async with self._profile_lock, device.lock if device else nullcontext():
                updated = await self.supervisor.profile(
                    identity,
                    data.get("family", "default"),
                    data.get("controls", "auto"),
                    hardware={key: data[key] for key in ("model", "firmware") if key in data},
                )
                profile_changed = updated.selection.device_families.get(
                    identity, updated.selection.family
                ) != self.pipeline.settings.selection.device_families.get(
                    identity, self.pipeline.settings.selection.family
                ) or updated.runtime.control_models.get(
                    identity, "auto"
                ) != self.pipeline.settings.runtime.control_models.get(identity, "auto")
                self.pipeline.settings = replace(
                    self.pipeline.settings,
                    selection=updated.selection,
                    runtime=replace(
                        self.pipeline.settings.runtime,
                        control_models=updated.runtime.control_models,
                        hardware=updated.runtime.hardware,
                    ),
                )
                self.pipeline.decoder = self.pipeline.settings.decoder()
                if self.pipeline.features:
                    self.pipeline.features.models = updated.runtime.control_models
                    self.pipeline.features.hardware = updated.runtime.hardware
                if (
                    profile_changed
                    and self.pipeline.features
                    and identity in self.pipeline.features.devices
                ):
                    device = self.pipeline.features.devices[identity]
                    device.profile = "pending"
                    device.values.clear()
                    device.schedules.clear()
                    device.refresh_at = 0
                if profile_changed and self.pipeline.ha:
                    await self.pipeline.ha.forget(identity)
            result = {
                "message": "Profile saved. Waiting for the next inverter reading."
                if profile_changed
                else "Inverter details saved."
            }
        elif path == "/api/migration":
            if self.supervisor is None or self.pipeline.ha is None:
                raise ValueError("Migration preview needs the Home Assistant app and MQTT")
            inventory = await self.supervisor.home_assistant()
            result = migration_preview(
                self.pipeline.ha.snapshots, self.pipeline.settings.mqtt, inventory
            )
        elif path == "/api/schedule":
            if self.pipeline.features is None:
                raise ValueError("Controls are unavailable")
            result = await self.pipeline.features.schedule_action(data)
        else:
            return 404, "application/json", b'{"error":"Not found"}'
        return 200, "application/json", json.dumps(result).encode()

    async def _handle(self, reader, writer):
        task = asyncio.current_task()
        peer = writer.get_extra_info("peername")
        if not peer or peer[0] != self.allowed_peer or len(self._tasks) >= 16:
            writer.close()
            await writer.wait_closed()
            return
        self._tasks.add(task)
        try:
            async with asyncio.timeout(45):
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
                if len(head) > 8192:
                    raise ValueError("Request headers are too large")
                lines = head.decode("ascii").split("\r\n")
                method, path, version = lines[0].split(" ")
                headers = {}
                for line in lines[1:]:
                    if line:
                        key, value = line.split(":", 1)
                        if key.lower() in headers:
                            raise ValueError("Duplicate header")
                        headers[key.lower()] = value.strip()
                size = int(headers.get("content-length", "0"))
                if not 0 <= size <= 16384 or "transfer-encoding" in headers:
                    raise ValueError("Invalid request size")
                body = await asyncio.wait_for(reader.readexactly(size), 5)
                try:
                    status, content_type, content = await self.dispatch(method, path, headers, body)
                except (ValueError, KeyError, TypeError):
                    status, content_type, content = (
                        400,
                        "application/json",
                        b'{"error":"Check the selected inverter, family and values."}',
                    )
                except (ConnectionError, TimeoutError):
                    status, content_type, content = (
                        503,
                        "application/json",
                        b'{"error":"The service did not respond. '
                        b'Check its connection and try again."}',
                    )
                disposition = (
                    'Content-Disposition: attachment; filename="ha-growatt-'
                    + ("capture" if path == "/api/capture" else "diagnostics")
                    + '.json"\r\n'
                    if path in {"/api/diagnostics", "/api/capture"}
                    else ""
                )
                writer.write(
                    (
                        f"HTTP/1.1 {status} Response\r\n"
                        f"Content-Type: {content_type}; charset=utf-8\r\n"
                        f"Content-Length: {len(content)}\r\n"
                        "Connection: close\r\nCache-Control: no-store\r\n"
                        "X-Content-Type-Options: nosniff\r\n"
                        "Content-Security-Policy: default-src 'self'; frame-ancestors 'self'; "
                        "object-src 'none'; base-uri 'self'\r\n" + disposition + "\r\n"
                    ).encode()
                    + content
                )
                await writer.drain()
        except (
            ValueError,
            UnicodeError,
            OSError,
            TimeoutError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ):
            pass
        finally:
            writer.close()
            with suppress(OSError):
                await writer.wait_closed()
            self._tasks.discard(task)

    async def start(self):
        self._server = await asyncio.start_server(self._handle, self.host, self.port, limit=8192)

    async def close(self):
        if self._server:
            self._server.close()
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        if self._server:
            await self._server.wait_closed()


_KNOWN_PROFILES = {
    "classic-2",
    "classic-5",
    "classic-6",
    "extended-5",
    "extended-6",
    "sph-5",
    "sph-6",
    "spa-6",
    "mod-6",
    "min-6",
    "tl3-6",
    "max-6",
    "spf-5",
    "spf-6",
    "pending",
}
