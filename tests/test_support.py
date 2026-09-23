import asyncio
import json
from contextlib import suppress
from dataclasses import replace
from types import SimpleNamespace

import pytest

from ha_growatt.ha_features import HomeAssistantFeatures
from ha_growatt.legacy_config import addon_options
from ha_growatt.pipeline import Pipeline
from ha_growatt.publisher import MqttSettings
from ha_growatt.relay import RelaySettings, RelayStats
from ha_growatt.settings import ConfigurationError, Settings
from ha_growatt.supervisor import Supervisor, prepare_app
from ha_growatt.support import SupportServer
from ha_growatt.telemetry import Telemetry


def support():
    settings = Settings(
        RelaySettings("private-cloud.example"),
        MqttSettings("private-broker.example", username="private-user", password="secret-test"),
        "auto",
    )
    pipeline = Pipeline(settings)
    transport = SimpleNamespace(
        stats=RelayStats(device_frames=5),
        running=True,
        connection=lambda _: "local",
        session_key=lambda _: 1,
    )
    pipeline.features = HomeAssistantFeatures(pipeline.ha, transport, pipeline)
    pipeline.features.remember(
        Telemetry({"pvserial": "PRIVATE001", "pvpowerout": 4567}, None, "mod-6")
    )
    return SupportServer(
        pipeline, transport, None, host="127.0.0.1", port=0, allowed_peer="127.0.0.1"
    )


def test_diagnostics_are_allowlisted_not_replacements():
    async def scenario():
        service = support()
        data = service.status(redacted=True)
        text = json.dumps(data)
        for secret in (
            "PRIVATE001",
            "private-cloud",
            "private-broker",
            "private-user",
            "secret-test",
            "4567",
        ):
            assert secret not in text
        assert data["devices"][0]["identity"] == "Inverter 1"
        assert data["devices"][0]["profile"] == "mod-6"
        assert data["warnings"]

    asyncio.run(scenario())


def test_actual_http_routes_headers_and_ingress_boundary():
    async def scenario():
        service = support()
        await service.start()
        port = service._server.sockets[0].getsockname()[1]

        async def request(path, method="GET", body=b"", extra=b""):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(
                (
                    f"{method} {path} HTTP/1.1\r\nHost: localhost\r\n"
                    f"Content-Length: {len(body)}\r\n"
                ).encode()
                + extra
                + b"\r\n"
                + body
            )
            await writer.drain()
            try:
                result = await reader.read()
            except ConnectionResetError:
                result = b""
            writer.close()
            with suppress(ConnectionResetError):
                await writer.wait_closed()
            return result

        try:
            page = await request("/")
            assert b"200" in page.splitlines()[0] and b"HA Growatt" in page
            assert b"Content-Security-Policy:" in page
            diagnostics = await request("/api/diagnostics")
            assert b"Content-Disposition: attachment" in diagnostics
            assert b"PRIVATE001" not in diagnostics
            for path in ("/api/shareable-capture", "/api/serial-redacted-capture"):
                download = await request(path)
                assert b"200" in download.splitlines()[0]
                assert b"Content-Disposition: attachment" in download
                assert b"Cache-Control: no-store" in download
                assert b"PRIVATE001" not in download
            assert b"405" in (await request("/api/profiles", "POST", b"{}")).splitlines()[0]
            assert (
                b"400"
                in (
                    await request(
                        "/api/profiles",
                        "POST",
                        b"{}",
                        b"Content-Type: application/json\r\nX-HA-Growatt: 1\r\n",
                    )
                ).splitlines()[0]
            )
            service.allowed_peer = "172.30.32.2"
            assert await request("/api/status") == b""
        finally:
            await service.close()

    asyncio.run(scenario())


def test_supervisor_broker_auto_setup_and_explicit_credentials(tmp_path, monkeypatch):
    path = tmp_path / "options.json"
    monkeypatch.setenv("SUPERVISOR_TOKEN", "private-test-token")
    requests = []

    def mqtt(self, endpoint, data=None):
        requests.append(endpoint)
        return {
            "host": "broker.internal",
            "port": 1883,
            "username": "test-user",
            "password": "test-secret",
            "ssl": False,
        }

    monkeypatch.setattr(Supervisor, "request", mqtt)
    path.write_text("{}")
    settings, client = prepare_app(path, state_directory=tmp_path / "state")
    assert requests == ["/services/mqtt"]
    assert settings.mqtt.username == "test-user" and settings.mqtt.state_path
    assert "test-secret" not in repr(settings)
    path.write_text(json.dumps({"mqtt_user": "existing", "mqtt_password": "existing-secret"}))
    settings, _ = prepare_app(path, state_directory=tmp_path / "state")
    assert settings.mqtt.username == "existing" and len(requests) == 1
    path.write_text(
        json.dumps({"mqtt_auto": False, "mqtt_host": "external", "restore_readings": False})
    )
    settings, _ = prepare_app(path, state_directory=tmp_path / "state")
    assert settings.mqtt.host == "external" and settings.mqtt.state_path == ""


def test_missing_broker_reports_no_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    path = tmp_path / "options.json"
    path.write_text("{}")
    with pytest.raises(ConfigurationError, match="Automatic MQTT needs Supervisor"):
        prepare_app(path, state_directory=tmp_path / "state")


@pytest.mark.parametrize(
    "inverters",
    [
        [{"serial": "bad/serial"}],
        [{"serial": "INVERT0001", "family": "wrong"}],
        [{"serial": "INVERT0001", "controls": "unsafe"}],
        [{"serial": "INVERT0001"}, {"serial": "INVERT0001"}],
    ],
)
def test_profile_options_reject_invalid_or_ambiguous_values(inverters):
    with pytest.raises(ValueError):
        addon_options({"inverters": inverters})


def test_profile_save_validates_before_touching_supervisor():
    class Client(Supervisor):
        def __init__(self):
            self.calls = []

        def request(self, path, data=None):
            self.calls.append((path, data))
            return {
                "options": {"mqtt_user": "preserved", "mqtt_password": "private", "inverters": []}
            }

    async def scenario():
        client = Client()
        result = await client.profile("INVERT0001", "sph", "sph")
        assert result.selection.device_families == {"INVERT0001": "sph"}
        assert client.calls[2][1]["options"]["mqtt_user"] == "preserved"
        client.calls.clear()
        with pytest.raises(ValueError):
            await client.profile("INVERT0001", "wrong", "sph")
        assert len(client.calls) == 1

    asyncio.run(scenario())


def test_profile_save_refuses_a_concurrent_options_change():
    class Client(Supervisor):
        def __init__(self):
            self.calls = 0

        def request(self, path, data=None):
            assert data is None, "Must not overwrite changed options"
            self.calls += 1
            return {"options": {"mqtt_host": "first" if self.calls == 1 else "changed"}}

    async def scenario():
        with pytest.raises(ValueError, match="options changed"):
            await Client().profile("INVERT0001", "sph", "sph")

    asyncio.run(scenario())


def test_hardware_edit_preserves_profile_cache_and_unknown_details():
    class Client(Supervisor):
        def __init__(self):
            self.options = {}

        def request(self, path, data=None):
            if data:
                self.options = data["options"]
            return {"options": self.options}

    async def scenario():
        server = support()
        server.supervisor = Client()
        device = server.pipeline.features.devices["PRIVATE001"]
        before = device.profile, device.last_record, device.readings
        device.health = {"fault_description": "Old model fault interpretation"}

        async def forbidden(_):
            raise AssertionError("A hardware label must not discard saved readings")

        server.pipeline.ha.forget = forbidden
        code, _, response = await server.dispatch(
            "POST",
            "/api/profiles",
            {"x-ha-growatt": "1", "content-type": "application/json"},
            json.dumps(
                {
                    "serial": "PRIVATE001",
                    "family": "default",
                    "controls": "auto",
                    "model": "Confirmed inverter model",
                    "firmware": "GH1.02",
                }
            ).encode(),
        )
        assert code == 200
        assert (device.profile, device.last_record, device.readings) == before
        assert device.health == {}
        assert server.pipeline.features.hardware["PRIVATE001"]["firmware"] == "GH1.02"
        assert "Confirmed inverter model" not in json.dumps(server.status(redacted=True))

    asyncio.run(scenario())


def test_setup_waits_for_a_reading_under_the_changed_profile():
    class Client(Supervisor):
        def __init__(self):
            self.options = {}

        def request(self, path, data=None):
            if data:
                self.options = data["options"]
            return {"options": self.options}

    async def scenario():
        server = support()
        server.supervisor = Client()
        assert server.status()["installation"]["all_seen_inverters_fresh"] is True
        code, _, _ = await server.dispatch(
            "POST",
            "/api/profiles",
            {"x-ha-growatt": "1", "content-type": "application/json"},
            json.dumps({"serial": "PRIVATE001", "family": "sph", "controls": "auto"}).encode(),
        )
        assert code == 200
        pending = server.status()
        assert pending["devices"][0]["readings"] == 1
        assert pending["devices"][0]["recent"] is True
        assert pending["devices"][0]["profile"] == "pending"
        assert pending["installation"]["fresh_inverters"] == 0
        assert pending["installation"]["all_seen_inverters_fresh"] is False
        server.pipeline.features.remember(
            Telemetry({"pvserial": "PRIVATE001", "pvpowerout": 4567}, None, "sph-6")
        )
        assert server.status()["installation"]["all_seen_inverters_fresh"] is True

    asyncio.run(scenario())


def test_firmware_read_preserves_the_global_reading_profile():
    class Client(Supervisor):
        def __init__(self):
            self.options = {"invtype": "sph"}

        def request(self, path, data=None):
            if data:
                self.options = data["options"]
            return {"options": self.options}

    async def scenario():
        from ha_growatt.protocol import Frame

        server = support()
        server.supervisor = Client()
        settings = server.pipeline.settings
        server.pipeline.settings = replace(
            settings, selection=replace(settings.selection, family="sph")
        )

        async def command(identity, function, body):
            assert function == 5
            return Frame(1, 6, 1, 5, bytes(30) + body + b"GH1.02GH2.01")

        server.transport.command = command
        device = server.pipeline.features.devices["PRIVATE001"]
        device.values["output_limit"] = 50
        server.pipeline.features.hardware = {"PRIVATE001": {"firmware": "GH0.0"}}
        old_context = server.pipeline.features.command_context(device)
        code, _, _ = await server.dispatch(
            "POST",
            "/api/hardware/read",
            {"x-ha-growatt": "1", "content-type": "application/json"},
            b'{"serial":"PRIVATE001"}',
        )
        assert code == 200
        assert server.supervisor.options["inverters"][0]["family"] == "sph"
        assert server.pipeline.settings.selection.family == "sph"
        assert device.firmware_changed and not device.values
        assert old_context != server.pipeline.features.command_context(device)

    asyncio.run(scenario())


def test_private_capture_requires_consent_and_never_enters_redacted_downloads():
    from ha_growatt.protocol import Frame

    async def scenario():
        service = support()
        headers = {"x-ha-growatt": "1", "content-type": "application/json"}
        with pytest.raises(ValueError):
            await service.dispatch("POST", "/api/private-capture/start", headers, b"{}")
        await service.dispatch(
            "POST", "/api/private-capture/start", headers, b'{"acknowledge_private_data":true}'
        )
        service.pipeline.private_capture.record(Frame(1, 6, 2, 4, b"SECRET0001" + bytes(100)))
        private = json.loads((await service.dispatch("GET", "/api/private-capture", {}, b""))[2])
        assert len(private["frames"]) == 1
        shared = json.loads((await service.dispatch("GET", "/api/shareable-capture", {}, b""))[2])
        assert shared["format"] == "ha-growatt-shareable-1"
        assert len(shared["records"]) == 1
        redacted = json.loads(
            (await service.dispatch("GET", "/api/serial-redacted-capture", {}, b""))[2]
        )
        assert redacted["format"] == "ha-growatt-serial-redacted-1"
        for route in ("/api/diagnostics", "/api/capture", "/api/shareable-capture"):
            text = (await service.dispatch("GET", route, {}, b""))[2].decode()
            assert "534543524554" not in text and private["frames"][0] not in text
        await service.dispatch("POST", "/api/private-capture/clear", headers, b"{}")
        assert not service.pipeline.private_capture.records

    asyncio.run(scenario())
