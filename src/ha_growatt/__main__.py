"""Offline packet inspection and explicit development relay commands."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
from contextlib import ExitStack, redirect_stderr, redirect_stdout
from pathlib import Path

from . import __version__
from .health import clear_health, healthy, write_health
from .pipeline import Pipeline
from .protocol import Frame, FrameBuffer, ProtocolError
from .publisher import Publisher
from .registers import parse_register_report
from .relay import Relay, RelaySettings
from .server import Server
from .settings import ConfigurationError, load_settings
from .sniffer import Sniffer


def inspect_capture(path: Path) -> None:
    parser = FrameBuffer()
    index = 0
    with path.open("rb") as stream:
        while chunk := stream.read(65536):
            for wire in parser.feed(chunk):
                index += 1
                frame = Frame.from_bytes(wire)
                result: dict[str, object] = {
                    "frame": index,
                    "protocol": frame.protocol,
                    "function": frame.function,
                    "bytes": len(wire),
                }
                try:
                    report = parse_register_report(frame)
                except ProtocolError:
                    result["register_report"] = False
                else:
                    result.update(
                        {
                            "register_report": True,
                            "namespace": report.namespace,
                            "register_count": len(report.registers),
                        }
                    )
                print(json.dumps(result))
    parser.finish()


async def _relay(arguments: argparse.Namespace) -> None:
    settings = RelaySettings(
        upstream_host=arguments.upstream,
        upstream_port=arguments.upstream_port,
        listen_host=arguments.listen,
        listen_port=arguments.port,
    )
    async with Relay(settings):
        print("Development relay listening; Home Assistant publication is not enabled.", flush=True)
        await asyncio.Event().wait()


async def _wait_for_shutdown(transport, mode: str, health_path: Path | None) -> None:
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    registered = False
    try:
        loop.add_signal_handler(signal.SIGTERM, stopped.set)
        registered = True
    except NotImplementedError:
        pass
    try:
        while not stopped.is_set():
            if not getattr(transport, "running", True):
                raise RuntimeError("The selected transport stopped")
            if health_path:
                write_health(health_path, mode)
            try:
                await asyncio.wait_for(stopped.wait(), 5)
            except TimeoutError:
                continue
    finally:
        if registered:
            loop.remove_signal_handler(signal.SIGTERM)
        if health_path:
            clear_health(health_path)


async def _run(
    path: Path, environment: dict | None = None, health_path: Path | None = None
) -> None:
    settings = load_settings(path, environment)
    if settings.runtime.verbose:
        logging.getLogger().setLevel(logging.DEBUG)
    pipeline = Pipeline(settings, ha_publisher=Publisher)
    if settings.runtime.mode == "server":
        transport = Server(
            settings.relay,
            pipeline.observe,
            api_host=settings.runtime.api_host,
            api_port=settings.runtime.api_port,
        )
    elif settings.runtime.mode == "sniff":
        transport = Sniffer(settings.relay, pipeline.observe, settings.runtime.sniff_interface)
    else:
        transport = Relay(settings.relay, pipeline.observe)
    pipeline.start()
    try:
        async with transport:
            print(f"HA Growatt {settings.runtime.mode} service started.", flush=True)
            await _wait_for_shutdown(transport, settings.runtime.mode, health_path)
    finally:
        await pipeline.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Growatt telemetry and local device services")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("-c", dest="legacy_config", type=Path, help="INI configuration file")
    parser.add_argument("-m", choices=["proxy", "server", "sniff"], help="Transport mode")
    parser.add_argument("-i", help="Inverter identity for compatibility decoding")
    parser.add_argument("-o", type=Path, help="Write console output to this file")
    for flags, destination in [
        (("-v", "--verbose"), "gverbose"),
        (("-nm", "--nomqtt"), "gnomqtt"),
        (("-p", "--pvoutput"), "gpvoutput"),
        (("-b", "--blockcmd"), "gblockcmd"),
        (("-n", "--noipf"), "gnoipf"),
        (("--diagnostic-logging",), "gdiagnosticlogging"),
        (("-t", "--trace"), "gtrace"),
    ]:
        parser.add_argument(*flags, dest=destination, action="store_true")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("container", help="Use the mounted Docker or Home Assistant configuration")
    inspect = commands.add_parser("inspect", help="Validate raw binary TCP-stream frames offline")
    inspect.add_argument("path", type=Path)
    relay = commands.add_parser("relay", help="Start the development TCP relay")
    relay.add_argument("--upstream", required=True)
    relay.add_argument("--upstream-port", type=int, default=5279)
    relay.add_argument("--listen", default="127.0.0.1")
    relay.add_argument("--port", type=int, default=5279)
    run = commands.add_parser("run", help="Run the configured service")
    run.add_argument("--config", type=Path, required=True)
    run.add_argument("--health-file", type=Path)
    server = commands.add_parser(
        "server", help="Run a standalone datalogger server and register API"
    )
    server.add_argument("--config", type=Path, required=True)
    server.add_argument("--port", type=int, default=5781)
    server.add_argument("--api-host", default="127.0.0.1")
    server.add_argument("--api-port", type=int, default=5782)
    server.add_argument("--health-file", type=Path)
    health = commands.add_parser("healthcheck", help="Read passive service liveness")
    health.add_argument("--file", type=Path, default=Path("/tmp/ha-growatt.health"))
    arguments = parser.parse_args()
    try:
        if arguments.command == "healthcheck":
            parser.exit(0 if healthy(arguments.file) else 1)
        elif arguments.command == "inspect":
            inspect_capture(arguments.path)
        elif arguments.command == "relay":
            asyncio.run(_relay(arguments))
        else:
            environment = dict(os.environ)
            for key, value in vars(arguments).items():
                if key.startswith("g") and value is True:
                    environment[key] = "True"
            if arguments.m:
                environment["gmode"] = arguments.m
            if arguments.i:
                environment["ginverterid"] = arguments.i
            if arguments.command == "server":
                environment.update(
                    gmode="server",
                    ggrottport=str(arguments.port),
                    HA_GROWATT_API_HOST=arguments.api_host,
                    HA_GROWATT_API_PORT=str(arguments.api_port),
                )
            path = (
                getattr(arguments, "config", None)
                or arguments.legacy_config
                or Path("ha-growatt.ini")
            )
            health_path = getattr(arguments, "health_file", None)
            if arguments.command == "container":
                path = Path(
                    environment.get(
                        "HA_GROWATT_CONFIG",
                        "/data/options.json"
                        if Path("/data/options.json").is_file()
                        else "/app/config/ha-growatt.ini",
                    )
                )
                health_path = Path("/tmp/ha-growatt.health")
            with ExitStack() as stack:
                if arguments.o:
                    stream = stack.enter_context(
                        arguments.o.open("a", encoding="utf-8", buffering=1)
                    )
                    stack.enter_context(redirect_stdout(stream))
                    stack.enter_context(redirect_stderr(stream))
                logging.basicConfig(
                    level=logging.DEBUG if arguments.gverbose else logging.INFO,
                    format="%(levelname)s: %(message)s",
                )
                asyncio.run(_run(path, environment, health_path))
    except KeyboardInterrupt:
        pass
    except ConfigurationError as error:
        parser.exit(2, f"Configuration error: {error}\n")
    except (ProtocolError, ValueError, OSError, RuntimeError, ImportError) as error:
        parser.exit(2, f"{type(error).__name__}: operation could not be completed\n")


if __name__ == "__main__":
    main()
