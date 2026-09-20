"""Offline packet inspection and explicit development relay commands."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from . import __version__
from .protocol import Frame, FrameBuffer, ProtocolError
from .publisher import Publisher
from .registers import parse_register_report
from .relay import Relay, RelaySettings
from .settings import ConfigurationError, load_settings


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


async def _run(path: Path) -> None:
    settings = load_settings(path)
    decoder = settings.decoder()
    publisher = Publisher(settings.mqtt)

    async def observe(direction, frame):
        if direction == "device" and frame.function in {3, 4}:
            await publisher.publish(decoder.decode(frame))

    publisher.start()
    try:
        async with Relay(settings.relay, observe):
            print("HA Growatt development bridge listening.", flush=True)
            await asyncio.Event().wait()
    finally:
        await publisher.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="HA Growatt development tools")
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect", help="Validate raw binary TCP-stream frames offline")
    inspect.add_argument("path", type=Path)
    relay = commands.add_parser("relay", help="Start the development TCP relay")
    relay.add_argument("--upstream", required=True)
    relay.add_argument("--upstream-port", type=int, default=5279)
    relay.add_argument("--listen", default="127.0.0.1")
    relay.add_argument("--port", type=int, default=5279)
    run = commands.add_parser("run", help="Run the development telemetry bridge")
    run.add_argument("--config", type=Path, required=True)
    arguments = parser.parse_args()
    try:
        if arguments.command == "inspect":
            inspect_capture(arguments.path)
        elif arguments.command == "relay":
            asyncio.run(_relay(arguments))
        else:
            asyncio.run(_run(arguments.config))
    except KeyboardInterrupt:
        pass
    except ConfigurationError as error:
        parser.exit(2, f"Configuration error: {error}\n")
    except (ProtocolError, ValueError, OSError) as error:
        parser.exit(2, f"{type(error).__name__}: operation could not be completed\n")


if __name__ == "__main__":
    main()
