import asyncio
import json
from types import SimpleNamespace

from ha_growatt.diagnostics import ObservationStats
from ha_growatt.discovery import discovery_messages
from ha_growatt.ha_features import Device, HomeAssistantFeatures, feature_discovery
from ha_growatt.telemetry import Telemetry


class Publisher:
    generation = 1

    def __init__(self):
        self.messages = []

    async def _send(self, topic, payload, retain):
        self.messages.append((topic, payload, retain))


def features(controls=True):
    publisher = Publisher()
    relay = SimpleNamespace(
        connection=lambda _: "cloud", stats=SimpleNamespace(fallback_connections=0)
    )
    relay.session_key = lambda _: 1
    return HomeAssistantFeatures(
        publisher,
        relay,
        SimpleNamespace(failures=0, observations=ObservationStats()),
        controls=controls,
    )


def packet(profile="mod-6"):
    return Telemetry({"pvserial": "INVERT0001"}, None, profile)


def test_discovery_preserves_telemetry_topics_and_device_identity():
    existing = discovery_messages("INVERT0001", wire_profile="mod-6")
    extra = feature_discovery(Device("INVERT0001", "mod-6", 0, ""), True)
    assert not existing.keys() & extra.keys()
    assert len(existing) == 32
    assert all(config["device"]["identifiers"] == ["INVERT0001"] for config in extra.values())
    assert len([t for t in extra if t.startswith("homeassistant/number/")]) == 1
    for topic, config in extra.items():
        assert config["availability"][0]["topic"] == "ha_growatt/service/status"
        if topic.startswith(("homeassistant/number/", "homeassistant/switch/")):
            assert config["optimistic"] is False and config["retain"] is False
            assert "expire_after" not in config


def test_status_tracks_freshness_separately_from_socket_and_keeps_readings():
    async def scenario():
        service = features()
        service.remember(packet())
        device = service.devices["INVERT0001"]
        service.transport.connection = lambda _: "disconnected"
        await service.publish_status(device)
        state = json.loads(service.publisher.messages[-1][1])
        assert state["connected"] and not state["socket_connected"]
        device.last_seen -= 901
        await service.publish_status(device)
        state = json.loads(service.publisher.messages[-1][1])
        assert not state["connected"] and state["readings"] == 1
        assert not any(topic.endswith("/state") for topic, *_ in service.publisher.messages)

    asyncio.run(scenario())


def test_birth_and_profile_change_republish_and_remove_old_controls():
    async def scenario():
        service = features()
        service.remember(packet("sph-6"))
        device = service.devices["INVERT0001"]
        await service.publish_status(device)
        service.publisher.messages.clear()
        await service.publish_status(device)
        assert len(service.publisher.messages) == 1
        service.publisher.generation += 1
        await service.publish_status(device)
        assert any(topic.endswith("/config") for topic, *_ in service.publisher.messages)
        device.values["charge_rate"] = 50
        service.remember(packet("spf-6"))
        assert not device.values
        service.publisher.messages.clear()
        await service.publish_status(device)
        removed = {
            topic
            for topic, payload, retain in service.publisher.messages
            if payload == "" and retain
        }
        assert "homeassistant/number/ha_growatt/INVERT0001_charge_rate/config" in removed
        assert "homeassistant/number/ha_growatt/INVERT0001_output_limit/config" in removed

    asyncio.run(scenario())


def test_retained_duplicate_and_oversized_commands_are_ignored():
    async def scenario():
        service = features()
        service._loop = asyncio.get_running_loop()
        defaults = {
            "topic": "ha_growatt/INVERT0001/command/output_limit",
            "payload": b"50",
            "retain": False,
            "dup": False,
        }
        for change in ({"retain": True}, {"dup": True}, {"payload": b"x" * 33}):
            service._message(SimpleNamespace(**(defaults | change)))
        await asyncio.sleep(0)
        assert service._commands.empty()
        service._message(SimpleNamespace(**defaults))
        await asyncio.sleep(0)
        assert service._commands.qsize() == 1
        for _ in range(30):
            service._message(SimpleNamespace(**defaults))
        await asyncio.sleep(0)
        assert service._commands.qsize() == 16 and service.rejected_commands == 15

    asyncio.run(scenario())


def test_disabling_controls_clears_their_discovery_without_sending_commands():
    async def scenario():
        service = features(False)
        service.remember(packet())
        await service.execute("ha_growatt/INVERT0001/command/output_limit", b"50")
        assert not service.publisher.messages
        await service.publish_status(service.devices["INVERT0001"])
        assert any(
            topic.startswith("homeassistant/button/") and payload == ""
            for topic, payload, _ in service.publisher.messages
        )
        assert not any(
            topic.startswith("homeassistant/number/") and payload
            for topic, payload, _ in service.publisher.messages
        )

    asyncio.run(scenario())


def test_feature_state_has_no_raw_packets_addresses_or_credentials():
    async def scenario():
        service = features()
        service.remember(packet())
        await service.publish_status(service.devices["INVERT0001"])
        state = json.loads(service.publisher.messages[-1][1])
        assert set(state) == {
            "connected",
            "socket_connected",
            "connection",
            "profile",
            "readings",
            "decode_errors",
            "last_record",
            "fallbacks",
            "output_failures",
            "command_result",
            "settings",
            "schedules",
            "rejected_commands",
            "schema",
        }

    asyncio.run(scenario())
