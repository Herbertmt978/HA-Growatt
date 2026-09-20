"""Narrow Supervisor operations for app setup and read-only HA inspection."""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from .legacy_config import _boolean, addon_options
from .settings import ConfigurationError, Settings


class Supervisor:
    def __init__(self, token: str) -> None:
        self._token = token

    def request(self, path: str, data: dict | None = None) -> dict:
        if path not in {"/services/mqtt", "/addons/self/info", "/addons/self/options"}:
            raise ValueError("Unsupported Supervisor operation")
        request = Request(
            "http://supervisor" + path,
            data=json.dumps(data).encode() if data is not None else None,
            headers={"Authorization": "Bearer " + self._token, "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=10) as response:
                content = response.read(1024 * 1024 + 1)
                if len(content) > 1024 * 1024:
                    raise ValueError("Supervisor response is too large")
                result = json.loads(content)
            if result.get("result") != "ok":
                raise ValueError("Supervisor rejected the request")
            return result.get("data", {})
        except (URLError, ValueError, OSError):
            raise ConnectionError("Home Assistant could not complete the request") from None

    async def profile(self, identity: str, family: str, controls: str) -> Settings:
        info = await asyncio.to_thread(self.request, "/addons/self/info")
        options = dict(info["options"])
        options["inverters"] = [
            item for item in options.get("inverters", []) if item["serial"] != identity
        ] + [{"serial": identity, "family": family, "controls": controls}]
        settings = Settings(*_settings_args(options))
        # Supervisor replaces the complete options object; it has no conditional
        # update endpoint. Refuse a change detected while preparing this save.
        latest = await asyncio.to_thread(self.request, "/addons/self/info")
        if latest["options"] != info["options"]:
            raise ValueError("App options changed; reload the page before saving the profile")
        await asyncio.to_thread(self.request, "/addons/self/options", {"options": options})
        return settings

    async def home_assistant(self) -> dict:
        from websockets.asyncio.client import connect

        result = {}
        try:
            async with (
                asyncio.timeout(30),
                connect(
                    "ws://supervisor/core/websocket",
                    max_size=16 * 1024 * 1024,
                    open_timeout=10,
                    proxy=None,
                ) as socket,
            ):
                if json.loads(await socket.recv()).get("type") != "auth_required":
                    raise ValueError("Unexpected Home Assistant authentication")
                await socket.send(json.dumps({"type": "auth", "access_token": self._token}))
                if json.loads(await socket.recv()).get("type") != "auth_ok":
                    raise ValueError("Home Assistant authentication failed")
                for number, (key, command) in enumerate(
                    (
                        ("entities", "config/entity_registry/list"),
                        ("devices", "config/device_registry/list"),
                        ("states", "get_states"),
                        ("statistics", "recorder/list_statistic_ids"),
                        ("energy", "energy/get_prefs"),
                    ),
                    1,
                ):
                    await socket.send(json.dumps({"id": number, "type": command}))
                    response = json.loads(await socket.recv())
                    if response.get("id") != number:
                        raise ValueError("Unexpected Home Assistant response")
                    if not response.get("success"):
                        if key == "energy" and response.get("error", {}).get("code") == "not_found":
                            result[key] = {}
                            continue
                        raise ValueError("Home Assistant inspection failed")
                    result[key] = response["result"]
        except Exception:
            raise ConnectionError(
                "Could not read Home Assistant; check that Core is running"
            ) from None
        return result


def _settings_args(options):
    relay, mqtt, selection, runtime = addon_options(options)
    return relay, mqtt, "auto", selection, runtime


def prepare_app(path: Path, *, state_directory: Path = Path("/data/state")):
    options = json.loads(path.read_text(encoding="utf-8"))
    settings = Settings(*_settings_args(options))
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    supervisor = Supervisor(token) if token else None
    # Existing explicit credentials and external brokers retain their meaning.
    if (
        _boolean(options.get("mqtt_auto", True))
        and settings.mqtt.host in {"core-mosquitto", ""}
        and not settings.mqtt.username
        and not settings.mqtt.password
    ):
        if supervisor is None:
            raise ConfigurationError(
                "Automatic MQTT needs Supervisor; enter broker settings instead"
            )
        try:
            service = supervisor.request("/services/mqtt")
            mqtt = replace(
                settings.mqtt,
                host=service["host"],
                port=int(service["port"]),
                username=service.get("username", ""),
                password=service.get("password", ""),
                tls=service.get("ssl", False),
            )
        except (ConnectionError, KeyError, ValueError, TypeError):
            raise ConfigurationError(
                "Automatic MQTT is unavailable. Start the Mosquitto app or enter broker settings."
            ) from None
        settings = replace(settings, mqtt=mqtt)
    if _boolean(options.get("restore_readings", True)):
        state_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            os.chown(state_directory, 10001, 10001)
        settings = replace(
            settings, mqtt=replace(settings.mqtt, state_path=str(state_directory / "readings.json"))
        )
    return settings, supervisor
