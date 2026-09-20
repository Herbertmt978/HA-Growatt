"""Run against the installed image with /tests mounted read-only."""

import argparse
import asyncio
import json
import os
import signal
import socket
import sys
import tempfile
from pathlib import Path

from ha_growatt.health import healthy
from ha_growatt.protocol import Frame, read_frame
from ha_growatt.relay import RelaySettings
from ha_growatt.sniffer import Sniffer
from ha_growatt.telemetry import Decoder


def free_port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


async def service(mode):
    received = []
    tasks = set()

    async def cloud(reader, writer):
        try:
            while packet := await read_frame(reader, 10):
                received.append(packet)
                frame = Frame.from_bytes(packet)
                writer.write(
                    Frame(
                        frame.transaction, frame.protocol, frame.unit, frame.function, b"\0"
                    ).to_bytes()
                )
                await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    def accept(reader, writer):
        task = asyncio.create_task(cloud(reader, writer))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    upstream = await asyncio.start_server(accept, "127.0.0.1", 0)
    upstream_port = upstream.sockets[0].getsockname()[1]
    listener_port, api_port = free_port(), free_port()
    with tempfile.TemporaryDirectory(prefix="ha-growatt-probe-") as directory:
        config, health = Path(directory) / "service.ini", Path(directory) / "health"
        config.write_text(
            f"[Generic]\nmode={mode}\nip=127.0.0.1\nport={listener_port}\napi_port={api_port}\nblockcmd=True\n[Growatt]\nip=127.0.0.1\nport={upstream_port}\n[MQTT]\nnomqtt=True\n"
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "ha_growatt",
            "run",
            "--config",
            str(config),
            "--health-file",
            str(health),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(15):
                while not healthy(health):
                    if process.returncode is not None:
                        raise AssertionError("Service exited before it became healthy")
                    await asyncio.sleep(0.05)
            reader, writer = await asyncio.open_connection("127.0.0.1", listener_port)
            try:
                for protocol in (2, 5, 6):
                    packet = Frame(protocol, protocol, 1, 4, bytes(200)).to_bytes()
                    writer.write(packet[:5])
                    await writer.drain()
                    writer.write(packet[5:])
                    await writer.drain()
                    response = Frame.from_bytes(await read_frame(reader, 5))
                    assert response.payload == b"\0" and response.protocol == protocol
                if mode == "proxy":
                    assert len(received) == 3
                assert healthy(health)
            finally:
                writer.close()
                await writer.wait_closed()
            process.send_signal(signal.SIGTERM)
            out, error = await asyncio.wait_for(process.communicate(), 20)
            assert process.returncode == 0, (out.decode(), error.decode())
            assert not health.exists()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            upstream.close()
            await upstream.wait_closed()
            await asyncio.gather(*tasks)


async def sniff():
    captured = []
    got_record = asyncio.Event()
    peer_closed = asyncio.Event()

    async def cloud(reader, writer):
        try:
            await reader.read()
        finally:
            writer.close()
            await writer.wait_closed()
            peer_closed.set()

    async def observe(direction, frame):
        captured.append(frame)
        got_record.set()

    server = await asyncio.start_server(cloud, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with Sniffer(RelaySettings("127.0.0.1", upstream_port=port), observe, "lo"):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            try:
                for protocol in (2, 5, 6):
                    got_record.clear()
                    frame = Frame(protocol, protocol, 1, 4, bytes(200))
                    packet = frame.to_bytes()
                    writer.write(packet[:12])
                    await writer.drain()
                    await asyncio.sleep(0.05)
                    writer.write(packet[12:])
                    await writer.drain()
                    await asyncio.wait_for(got_record.wait(), 5)
                    assert captured[-1] == frame
            finally:
                writer.close()
                await writer.wait_closed()
                await asyncio.wait_for(peer_closed.wait(), 5)
    finally:
        server.close()
        await server.wait_closed()
    assert len(captured) == 3


async def application():
    """Exercise the same options file and entry point used by the HA app."""
    health = Path("/tmp/ha-growatt.health")
    with tempfile.TemporaryDirectory(prefix="ha-growatt-app-") as directory:
        options = Path(directory) / "options.json"
        for home_assistant in (True, False):
            options.write_text(
                json.dumps(
                    {
                        "mqtt_host": "127.0.0.1",
                        "mqtt_port": free_port(),
                        "ha_plugin": home_assistant,
                        "time": "auto",
                        "sendbuf": True,
                    }
                )
            )
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "ha_growatt",
                "container",
                env=os.environ | {"HA_GROWATT_CONFIG": str(options)},
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                async with asyncio.timeout(15):
                    while not healthy(health):
                        if process.returncode is not None:
                            raise AssertionError("App exited before it became healthy")
                        await asyncio.sleep(0.05)
                process.send_signal(signal.SIGTERM)
                out, error = await asyncio.wait_for(process.communicate(), 20)
                assert process.returncode == 0, (out.decode(), error.decode())
                assert not await asyncio.to_thread(health.exists)
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()


async def supervisor_application():
    """Supervisor protects its options with mode 0600 and root ownership."""
    assert os.getuid() == 0
    health = Path("/tmp/ha-growatt.health")
    with tempfile.TemporaryDirectory(prefix="ha-growatt-supervisor-") as directory:
        options = Path(directory) / "options.json"
        options.write_text(json.dumps({"mqtt_host": "127.0.0.1", "mqtt_port": free_port()}))
        options.chmod(0o600)
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "ha_growatt",
            "app",
            "--config",
            str(options),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(15):
                while not healthy(health):
                    if process.returncode is not None:
                        raise AssertionError("App could not read Supervisor's private options")
                    await asyncio.sleep(0.05)
            status = await asyncio.to_thread(Path(f"/proc/{process.pid}/status").read_text)
            fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
            assert fields["Uid"].split() == ["10001"] * 4
            assert fields["Gid"].split() == ["10001"] * 4
            assert not fields["Groups"].strip()
            assert (await asyncio.to_thread(health.stat)).st_uid == 10001
            process.send_signal(signal.SIGTERM)
            out, error = await asyncio.wait_for(process.communicate(), 20)
            assert process.returncode == 0, (out.decode(), error.decode())
            assert not await asyncio.to_thread(health.exists)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


def installed_profiles():
    total = 0
    for filename in ("telemetry_cases.json", "extra_telemetry_cases.json", "csv_meter_cases.json"):
        for case in json.loads((Path(__file__).parent / "fixtures" / filename).read_text()):
            frame = Frame.from_bytes(bytes.fromhex(case["wire"]))
            telemetry = Decoder(
                case.get("profile", "meter-log-6"), include_all=case.get("include_all", False)
            ).decode(frame)
            assert telemetry.values == case["expected"]
            total += 1
    return total


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--sniff", action="store_true")
    parser.add_argument("--supervisor", action="store_true")
    arguments = parser.parse_args()
    if arguments.supervisor:
        asyncio.run(supervisor_application())
        print(json.dumps({"private_app_options": "passed", "privilege_drop": "passed"}))
    elif arguments.sniff:
        asyncio.run(sniff())
        print(json.dumps({"sniffer": "passed"}))
    else:
        assert os.getuid() == 10001
        profiles = installed_profiles()
        for mode in ("proxy", "server"):
            asyncio.run(service(mode))
        asyncio.run(application())
        print(
            json.dumps(
                {
                    "installed_packets": profiles,
                    "proxy": "passed",
                    "server": "passed",
                    "signal_shutdown": "passed",
                    "non_root": "passed",
                    "app_options": "passed",
                }
            )
        )
