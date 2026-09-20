"""CSV and HTTP outputs, plus a bounded process for user extensions."""

from __future__ import annotations

import csv
import json
import multiprocessing
from datetime import datetime
from pathlib import Path


def layout_context(decoder, telemetry) -> dict:
    """Expose legacy layout descriptors reconstructed from our observed profiles."""
    from .profiles import wire_profiles
    from .selection import layout_name

    records = {}
    for profile, schema in wire_profiles().items():
        width = 30 if schema["protocol"] == 6 else 10
        description = {"decrypt": {"value": str(schema["protocol"] != 2)}}
        divisors = {item["source"]: item.get("divisor", 1) for item in schema["sensors"].values()}
        for key, (offset, size, signed) in schema["numeric_fields"].items():
            description[key] = {
                "value": (offset + 8) * 2,
                "length": size,
                "type": "numx" if signed else "num",
                "divide": divisors.get(key.strip(), 1),
                "incl": "no" if key in schema["excluded_fields"] else "yes",
            }
        for key in schema["text_fields"]:
            description[key] = {
                "value": (8 + (width if key == "pvserial" else 0)) * 2,
                "length": 10,
                "type": "text",
            }
        if "log_fields" in schema:
            description["logstart"] = {"value": (schema["log_offset"] + 8) * 2}
            for key, (position, mode) in schema["log_fields"].items():
                description[key] = {
                    "type": {"both": "log", "positive": "logpos", "negative": "logneg"}[mode],
                    "pos": position + 1,
                    "divide": divisors.get(key, 1),
                }
            for key, value in schema.get("constants", {}).items():
                description[key] = {"value": value, "type": "def"}
        elif schema.get("timestamp") is not False:
            description["date"] = {"value": (width * 2 + 8) * 2}
        records[layout_name(profile)] = description
    for name, layout in getattr(decoder, "custom_layouts", {}).items():
        records[name] = layout.description
    name = layout_name(telemetry.profile)
    if name == "compatibility":
        records[name] = {
            key: {"divide": item.get("divisor", 1)}
            for key, item in telemetry.sensor_metadata.items()
        }
    return {"layout": name, "recorddict": records}


def csv_output(configuration, packet_hex: str, encoded: str) -> int:
    message = json.loads(encoded)
    options = configuration.extvar
    stamp = datetime.fromisoformat(message["time"])
    directory = Path(options.get("outpath", "/home/pi/grottlog")) / f"{stamp.year}-minute"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{stamp:%Y%m%d}.csv"
    columns = options.get("csvheader", "device,time," + ",".join(message["values"])).split(",")
    descriptors = configuration.recorddict[configuration.layout]
    row = []
    for key in columns:
        if key in {"device", "time"}:
            row.append(message[key])
        else:
            value = message["values"][key]
            divisor = descriptors.get(key, {}).get("divide", 1)
            row.append(
                value / divisor if divisor != 1 and isinstance(value, (int, float)) else value
            )
    new = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        if new:
            writer.writerow(columns)
        writer.writerow(row)
    return 0


def http_output(configuration, packet_hex: str, encoded: str) -> int:
    from .outputs import HttpWrite, _endpoint, send_http

    options = configuration.extvar
    endpoint = options.get("url") or f"http://{options['ip']}:{options['port']}"
    _endpoint(endpoint)
    # The original HTTP extension sends a JSON string containing JSON text.
    send_http(
        HttpWrite(endpoint, json.dumps(encoded).encode(), {"Content-Type": "application/json"})
    )
    return 0


def _worker(connection, module: str, options: dict, configuration: dict) -> None:
    from .outputs import Extension

    try:
        extension = Extension(module, options, configuration)
        while True:
            request = connection.recv()
            if request is None:
                return
            message, packet, context = request
            try:
                extension.publish(message, packet, context)
            except Exception:
                connection.send(False)
            else:
                connection.send(True)
    except (EOFError, OSError, ImportError, AttributeError):
        return
    finally:
        connection.close()


class ExtensionProcess:
    """Keep extension module state, and stop an unresponsive callback at shutdown."""

    def __init__(self, module: str, options: dict, configuration: dict, timeout: float = 8) -> None:
        self.module, self.options, self.configuration = module, options, configuration
        self.timeout = timeout
        self._process = None
        self._connection = None

    def publish(self, message: dict, packet: str, context: dict) -> None:
        if self._process is None:
            spawn = multiprocessing.get_context("spawn")
            parent, child = spawn.Pipe()
            self._process = spawn.Process(
                target=_worker,
                args=(child, self.module, self.options, self.configuration),
                daemon=True,
            )
            self._connection = parent
            self._process.start()
            child.close()
        try:
            self._connection.send((message, packet, context))
            if not self._connection.poll(self.timeout):
                raise TimeoutError("Extension delivery deadline reached")
            if not self._connection.recv():
                raise RuntimeError("Extension delivery failed")
        except (TimeoutError, EOFError, OSError):
            self.close()
            raise RuntimeError("Extension delivery failed") from None

    def close(self) -> None:
        if self._process is None:
            return
        try:
            if self._process.is_alive():
                try:
                    self._connection.send(None)
                except (OSError, EOFError):
                    pass
                self._process.join(0.2)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(1)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(1)
        finally:
            self._connection.close()
            self._process.close()
            self._process = self._connection = None
