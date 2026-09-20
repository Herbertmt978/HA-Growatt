"""Optional telemetry destinations and their established message formats."""

from __future__ import annotations

import asyncio
import base64
import importlib
import json
import math
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

from .publisher import MqttSettings, Publisher
from .telemetry import Telemetry


@dataclass(frozen=True, slots=True)
class PublicationPolicy:
    time_source: str = "auto"
    send_buffered: bool = True
    timezone: str = "local"

    def __post_init__(self) -> None:
        if self.time_source not in {"auto", "server"}:
            raise ValueError("Time source must be auto or server")
        if self.timezone != "local":
            try:
                ZoneInfo(self.timezone)
            except (KeyError, ValueError):
                raise ValueError("Unknown telemetry timezone") from None

    def message(self, telemetry: Telemetry, now: datetime | None = None) -> dict | None:
        if telemetry.buffered and (not self.send_buffered or telemetry.recorded_at is None):
            return None
        stamp = telemetry.recorded_at
        if stamp is None or (self.time_source == "server" and not telemetry.buffered):
            stamp = now or datetime.now()
        identity = telemetry.device_id or telemetry.values.get(
            "device", telemetry.values.get("pvserial", telemetry.values.get("datalogserial"))
        )
        if not isinstance(identity, str):
            raise ValueError("Telemetry has no device identity")
        return {
            "device": identity,
            "time": stamp.replace(tzinfo=None, microsecond=0).isoformat(),
            "buffered": "yes" if telemetry.buffered else "no",
            "values": dict(telemetry.values),
        }


@dataclass(frozen=True, slots=True)
class RawMqttSettings:
    broker: MqttSettings
    topic: str = "energy/growatt"
    inverter_in_topic: bool = False
    meter_topic: str | None = None

    def topic_for(self, message: dict, *, meter: bool = False) -> str:
        if meter and self.meter_topic:
            return self.meter_topic
        return f"{self.topic}/{message['device']}" if self.inverter_in_topic else self.topic


class RawPublisher(Publisher):
    def __init__(self, settings: RawMqttSettings) -> None:
        super().__init__(settings.broker)
        self.raw_settings = settings

    async def publish_message(self, message: dict, *, meter: bool = False) -> None:
        connected = await asyncio.to_thread(self._connected.wait, self.settings.delivery_seconds)
        if not connected:
            raise ConnectionError("MQTT did not connect before the delivery deadline")
        await self._send(
            self.raw_settings.topic_for(message, meter=meter),
            json.dumps(message, allow_nan=False),
            self.settings.retain_state,
            qos=0,
        )


def _endpoint(value: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("An HTTP or HTTPS output endpoint is required")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("Output endpoints cannot contain credentials or fragments")


@dataclass(frozen=True, slots=True)
class HttpWrite:
    url: str
    body: bytes = field(repr=False)
    headers: dict[str, str] = field(default_factory=dict, repr=False)
    method: str = "POST"


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def exchange_http(write: HttpWrite, timeout: float = 8) -> bytes:
    """Bound the connection and response, and never log response bodies or secrets."""
    request = Request(
        write.url,
        data=write.body if write.method != "GET" else None,
        headers=write.headers,
        method=write.method,
    )
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout) as response:
            if not 200 <= response.status < 300:
                raise OSError("Telemetry endpoint rejected the request")
            body = response.read(1048577)
            if len(body) > 1048576:
                raise OSError("Telemetry endpoint response is too large")
            return body
    except HTTPError as error:
        error.close()
        raise OSError("Telemetry HTTP delivery failed") from None
    except Exception:
        raise OSError("Telemetry HTTP delivery failed") from None


def send_http(write: HttpWrite, timeout: float = 8) -> None:
    exchange_http(write, timeout)


@dataclass(frozen=True, slots=True)
class PVOutputSettings:
    api_key: str = field(repr=False)
    systems: dict[str, str] = field(default_factory=dict)
    default_system: str | None = None
    interval_minutes: float = 5
    temperature: bool = False
    omit_energy: bool = False
    endpoint: str = "https://pvoutput.org/service/r2/addstatus.jsp"

    def __post_init__(self) -> None:
        _endpoint(self.endpoint)
        if not math.isfinite(self.interval_minutes) or self.interval_minutes < 0:
            raise ValueError("PVOutput interval must be non-negative")
        if not self.api_key or (not self.systems and not self.default_system):
            raise ValueError("PVOutput needs an API key and a system identity")


class PVOutput:
    def __init__(self, settings: PVOutputSettings) -> None:
        self.settings = settings
        self._last: dict[str, float] = {}

    def requests(self, message: dict, *, clock: float | None = None) -> list[HttpWrite]:
        identity = message["device"]
        system = self.settings.systems.get(identity, self.settings.default_system)
        if system is None:
            return []
        clock = time.monotonic() if clock is None else clock
        if (
            identity in self._last
            and clock - self._last[identity] < self.settings.interval_minutes * 60
        ):
            return []
        values = message["values"]
        stamp = datetime.fromisoformat(message["time"])
        common = {"d": stamp.strftime("%Y%m%d"), "t": stamp.strftime("%H:%M")}
        if "pos_act_energy" in values:
            common["v6"] = values["voltage_l1"] / 10
            records = [
                common | {"v3": values["pos_act_energy"] * 100, "c1": 3},
                common | {"v4": values["pos_rev_act_power"] / 10, "n": 1},
            ]
        else:
            data = common | {"v2": values["pvpowerout"] / 10, "v6": values["pvgridvoltage"] / 10}
            if not self.settings.omit_energy:
                data["v1"] = values["pvenergytoday"] * 100
            if self.settings.temperature:
                data["v5"] = values["pvtemperature"] / 10
            records = [data]
        self._last[identity] = clock
        headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "X-Pvoutput-Apikey": self.settings.api_key,
            "X-Pvoutput-SystemId": system,
        }
        return [
            HttpWrite(self.settings.endpoint, urlencode(data).encode("ascii"), headers)
            for data in records
        ]


@dataclass(frozen=True, slots=True)
class InfluxSettings:
    endpoint: str = "http://localhost:8086"
    version: int = 1
    database: str = "grottdb"
    username: str = ""
    password: str = field(default="", repr=False)
    token: str = field(default="", repr=False)
    organisation: str = "grottorg"
    bucket: str = "grottdb"

    def __post_init__(self) -> None:
        _endpoint(self.endpoint)
        if self.version not in {1, 2}:
            raise ValueError("InfluxDB version must be 1 or 2")


def _escape_key(value: str, *, field_key: bool = False) -> str:
    if any(c in value for c in "\r\n"):
        raise ValueError("InfluxDB names cannot contain newlines")
    value = value.replace("\\", "\\\\").replace(" ", "\\ ").replace(",", "\\,")
    return value.replace("=", "\\=") if field_key else value


def influx_point(message: dict, timezone: str = "local") -> dict:
    stamp = datetime.fromisoformat(message["time"])
    if stamp.tzinfo is None and timezone != "local":
        stamp = stamp.replace(tzinfo=ZoneInfo(timezone))
        # The earlier writer used standard time when a clock hour repeats.
        alternate = stamp.replace(fold=1)
        if alternate.utcoffset() < stamp.utcoffset():
            stamp = alternate
    stamp = stamp.astimezone(UTC)
    return {
        "measurement": message["device"],
        "time": stamp.replace(tzinfo=None).isoformat(timespec="seconds"),
        "fields": dict(message["values"]),
    }


def influx_request(settings: InfluxSettings, message: dict, timezone: str = "local") -> HttpWrite:
    point = influx_point(message, timezone)
    fields = []
    for key, value in point["fields"].items():
        if isinstance(value, str):
            if any(c in value for c in "\r\n"):
                raise ValueError("InfluxDB strings cannot contain newlines")
            encoded = '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        elif type(value) is int:
            encoded = f"{value}i"
        elif type(value) is float and math.isfinite(value):
            encoded = repr(value)
        else:
            raise ValueError("Unsupported InfluxDB field value")
        fields.append(f"{_escape_key(key, field_key=True)}={encoded}")
    seconds = int(datetime.fromisoformat(point["time"]).replace(tzinfo=UTC).timestamp())
    line = f"{_escape_key(point['measurement'])} {','.join(fields)} {seconds}"
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if settings.version == 2:
        path = "/api/v2/write?" + urlencode(
            {"org": settings.organisation, "bucket": settings.bucket, "precision": "s"}
        )
        headers["Authorization"] = "Token " + settings.token
    else:
        path = "/write?" + urlencode({"db": settings.database, "precision": "s"})
        if settings.username:
            auth = base64.b64encode(f"{settings.username}:{settings.password}".encode()).decode()
            headers["Authorization"] = "Basic " + auth
    return HttpWrite(settings.endpoint.rstrip("/") + path, line.encode(), headers)


class InfluxOutput:
    def __init__(self, settings: InfluxSettings, timezone: str = "local") -> None:
        self.settings, self.timezone = settings, timezone
        self._initialised = settings.version == 2

    def publish(self, message: dict) -> None:
        request = influx_request(self.settings, message, self.timezone)
        if not self._initialised:
            endpoint = self.settings.endpoint.rstrip("/") + "/query"
            response = exchange_http(
                HttpWrite(
                    endpoint + "?" + urlencode({"q": "SHOW DATABASES"}), b"", request.headers, "GET"
                )
            )
            result = json.loads(response)
            if any("error" in item for item in result.get("results", [])):
                raise OSError("InfluxDB database lookup failed")
            names = {
                row[0]
                for item in result.get("results", [])
                for series in item.get("series", [])
                for row in series.get("values", [])
            }
            if self.settings.database not in names:
                database = self.settings.database.replace("\\", "\\\\").replace('"', '\\"')
                body = urlencode({"q": f'CREATE DATABASE "{database}"'}).encode()
                headers = request.headers | {"Content-Type": "application/x-www-form-urlencoded"}
                created = json.loads(exchange_http(HttpWrite(endpoint, body, headers)))
                if any("error" in item for item in created.get("results", [])):
                    raise OSError("InfluxDB database creation failed")
            self._initialised = True
        send_http(request)


class Extension:
    """Load an explicitly configured local extension using its established ABI."""

    def __init__(self, module: str, options: dict, configuration: dict | None = None) -> None:
        from .extensions import csv_output, http_output

        builtins = {
            "grotcsv": csv_output,
            "grottext": http_output,
            "ha_growatt.csv": csv_output,
            "ha_growatt.http": http_output,
        }
        self._callback = (
            builtins[module] if module in builtins else importlib.import_module(module).grottext
        )
        self._configuration = SimpleNamespace(**(configuration or {}) | {"extvar": options})

    def publish(self, message: dict, packet_hex: str, context: dict | None = None) -> None:
        if context:
            vars(self._configuration).update(context)
        self._callback(self._configuration, packet_hex, json.dumps(message, allow_nan=False))
