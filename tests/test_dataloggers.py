import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_ha_features import features

from ha_growatt.dataloggers import Logger, discovery, logger_identifier
from ha_growatt.ha_features import HomeAssistantFeatures, feature_discovery
from ha_growatt.legacy_config import addon_options
from ha_growatt.protocol import Frame
from ha_growatt.relay import Relay, RelaySession, RelaySettings
from ha_growatt.runtime_options import RuntimeOptions
from ha_growatt.telemetry import Telemetry


def test_logger_heartbeats_count_connections_not_records_and_bound_history():
    relay = Relay(RelaySettings("cloud"))
    heartbeat = Frame(1, 6, 1, 22, b"LOGGER0001")
    for _ in range(15):
        session = RelaySession(SimpleNamespace(is_closing=lambda: False))
        relay._connections.add(session)
        relay._identify(heartbeat, session)
        relay._identify(heartbeat, session)
    logger = relay.loggers["LOGGER0001"]
    assert logger.connections == 15 and len(logger.reconnects) == 10
    assert logger.last_contact
    assert relay.logger_connection(logger.identity) == "local"
    session.cloud = True
    assert relay.logger_connection(logger.identity) == "cloud"
    relay._connections.clear()
    assert relay.logger_connection(logger.identity) == "disconnected"


def test_logger_device_links_without_changing_inverter_identity():
    async def scenario():
        service = features()
        telemetry = Telemetry(
            {"pvserial": "INVERT0001", "datalogserial": "LOGGER0001"}, None, "mod-6"
        )
        service.remember(telemetry)
        device = service.devices["INVERT0001"]
        device.last_seen -= 300
        service.remember(telemetry)
        assert 300 <= device.upload_interval < 301
        configs = feature_discovery(device, True)
        assert all(c["device"]["identifiers"] == ["INVERT0001"] for c in configs.values())
        assert all(
            c["device"]["via_device"] == logger_identifier("LOGGER0001") for c in configs.values()
        )
        service.transport.loggers = {"LOGGER0001": Logger("LOGGER0001")}
        service.transport.logger_connection = lambda _: "cloud"
        await service.publish_loggers()
        state = json.loads(service.publisher.messages[-1][1])
        assert state["upload_interval"] >= 300
        service.publisher.messages.clear()
        await service.publish_loggers()
        assert not any(topic.endswith("/history") for topic, _, _ in service.publisher.messages)
        service.publisher.generation += 1
        await service.publish_loggers()
        assert any(topic.endswith("/history") for topic, _, _ in service.publisher.messages)
        service.transport.session_key = lambda _: 2
        assert service.logger_statuses()["LOGGER0001"]["upload_interval"] is None
        service.remember(telemetry)
        assert device.upload_interval is None

    asyncio.run(scenario())


def test_logger_metadata_is_separate_from_inverter_firmware():
    config = next(iter(discovery("LOGGER0001", {"model": "ShineWiFi-X"}).values()))
    assert config["device"]["model"] == "ShineWiFi-X"
    assert "sw_version" not in config["device"]
    config = next(iter(discovery("LOGGER0001", {"firmware": "test-logger-fw"}).values()))
    assert config["device"]["sw_version"] == "test-logger-fw"
    options = addon_options({"dataloggers": [{"serial": "LOGGER0001", "model": "ShineWiFi-X"}]})[-1]
    assert options.dataloggers == {"LOGGER0001": {"model": "ShineWiFi-X"}}
    assert not options.hardware


@pytest.mark.parametrize(
    "value",
    [
        [],
        {"bad/id": {}},
        {"LOGGER0001": {"password": "secret"}},
        {"LOGGER0001": {"firmware": "x\n"}},
    ],
)
def test_invalid_logger_metadata_rejected(value):
    with pytest.raises(ValueError):
        RuntimeOptions(dataloggers=value)


def test_restored_logger_is_linked_without_inventing_live_contact():
    telemetry = Telemetry({"pvserial": "INVERT0001", "datalogserial": "LOGGER0001"}, None, "mod-6")
    publisher = SimpleNamespace(
        snapshots={
            "INVERT0001": SimpleNamespace(telemetry=telemetry, received_at=datetime.now(UTC))
        }
    )
    service = HomeAssistantFeatures(publisher, Relay(RelaySettings("cloud")), None)
    assert service.devices["INVERT0001"].logger == "LOGGER0001"
    state = service.logger_statuses()["LOGGER0001"]
    assert state["connection"] == "disconnected"
    assert state["last_contact"] is None and state["upload_interval"] is None
    assert state["reconnects"] == 0 and not state["history"]


def test_logger_identity_and_custom_firmware_are_redacted():
    from test_support import support

    async def scenario():
        server = support()
        service = server.pipeline.features
        service.devices["PRIVATE001"].logger = "LOGGER0001"
        service.dataloggers = {"LOGGER0001": {"firmware": "private-fw"}}
        redacted = json.dumps(server.status(redacted=True))
        assert "LOGGER0001" not in redacted and "private-fw" not in redacted
        assert "Datalogger 1" in redacted

    asyncio.run(scenario())
