import asyncio
import json
from contextlib import suppress
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
        stats=RelayStats(device_frames=5), running=True, connection=lambda _: "local"
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
