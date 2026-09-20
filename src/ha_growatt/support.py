"""Small ingress-only setup and support page for the Home Assistant app."""

from __future__ import annotations

import asyncio
import json
from contextlib import nullcontext, suppress
from dataclasses import asdict, replace
from importlib.resources import files

from . import __version__
from .discovery import validate_identity
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
        warnings = []
        connected = bool(publisher and publisher._connected.is_set())
        if not connected:
            warnings.append(
                "MQTT is disconnected. Start the broker and check the app's broker settings."
            )
        if not stats.device_frames:
            warnings.append(
                "Waiting for the first datalogger packet. Check its destination IP and port 5279; "
                "it may be asleep overnight."
            )
        elif not features or not any(item.readings for item in features.devices.values()):
            warnings.append(
                "Packets are arriving but no fresh inverter reading has decoded. "
                "Check the inverter family. New session-key encrypted loggers are not supported."
            )
        if stats.observation_failures:
            warnings.append(
                "Some packets could not be decoded. "
                "Check the profile and download diagnostics for support."
            )
        if publisher and publisher.cache_error:
            warnings.append(
                "Restart recovery is unavailable. Check free space and app data permissions; "
                "fresh readings still work."
            )
        if stats.fallback_connections:
            warnings.append(
                "Cloud fallback has been used. Local readings continue; "
                "ShinePhone will have gaps until a new connection reaches Growatt."
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
                        device.identity, "default"
                    ),
                    "controls": self.pipeline.settings.runtime.control_models.get(
                        device.identity, "auto"
                    ),
                }
            )
        return {
            "version": __version__,
            "listener": self.transport.running,
            "mqtt_connected": connected,
            "recovery_enabled": bool(publisher and publisher._store),
            "recovery_healthy": bool(publisher and publisher._store and not publisher.cache_error),
            "experimental_controls": self.pipeline.settings.runtime.experimental_controls,
            "transport": asdict(stats),
            "output_failures": self.pipeline.failures,
            "dropped_readings": self.pipeline.dropped,
            "devices": devices,
            "warnings": warnings,
        }

    async def dispatch(self, method, path, headers, body):
        if method == "GET" and path == "/favicon.ico":
            return 204, "image/x-icon", b""
        if method == "GET" and path in {"/", "/app.js", "/style.css"}:
            name = "index.html" if path == "/" else path[1:]
            content_type = {
                "index.html": "text/html",
                "app.js": "text/javascript",
                "style.css": "text/css",
            }[name]
            return 200, content_type, files("ha_growatt").joinpath("web", name).read_bytes()
        if method == "GET" and path == "/api/status":
            return 200, "application/json", json.dumps(self.status()).encode()
        if method == "GET" and path == "/api/diagnostics":
            return (
                200,
                "application/json",
                json.dumps(self.status(redacted=True), indent=2).encode(),
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
        if path == "/api/profiles":
            identity = data.get("serial", "")
            validate_identity(identity)
            if self.supervisor is None:
                raise ValueError("Profile editing needs the Home Assistant app")
            device = (
                self.pipeline.features.devices.get(identity) if self.pipeline.features else None
            )
            async with self._profile_lock, device.lock if device else nullcontext():
                updated = await self.supervisor.profile(
                    identity, data.get("family", "default"), data.get("controls", "auto")
                )
                self.pipeline.settings = replace(
                    self.pipeline.settings,
                    selection=updated.selection,
                    runtime=replace(
                        self.pipeline.settings.runtime,
                        control_models=updated.runtime.control_models,
                    ),
                )
                self.pipeline.decoder = self.pipeline.settings.decoder()
                if self.pipeline.features:
                    self.pipeline.features.models = updated.runtime.control_models
                if self.pipeline.features and identity in self.pipeline.features.devices:
                    device = self.pipeline.features.devices[identity]
                    device.profile = "pending"
                    device.values.clear()
                    device.schedules.clear()
                    device.refresh_at = 0
                if self.pipeline.ha:
                    await self.pipeline.ha.forget(identity)
            result = {"message": "Profile saved. Waiting for the next inverter reading."}
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
                    'Content-Disposition: attachment; filename="ha-growatt-diagnostics.json"\r\n'
                    if path == "/api/diagnostics"
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
