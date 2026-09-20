import asyncio
from dataclasses import asdict, replace

import pytest

from ha_growatt.controls import controls_for
from ha_growatt.device_protocol import logger_prefix
from ha_growatt.ha_features import Device, feature_discovery
from ha_growatt.protocol import Frame
from ha_growatt.schedules import Period, addresses, read_period, schedule_keys, write_period


class Inverter:
    def __init__(self, protocol=6):
        self.protocol = protocol
        self.period = Period("23:30", "05:30", True)
        self.requests = []
        self.reject = self.discard = self.wrong_range = False
        self.busy = 0

    async def command(self, identity, function, body, *, before_send=None):
        if before_send:
            before_send()
        self.requests.append((function, body))
        address = body[:4]
        if function == 5:
            if self.busy and any(f == 16 for f, _ in self.requests):
                self.busy -= 1
                raise TimeoutError
            data = b"".join(word.to_bytes(2, "big") for word in self.period.words())
            response = (b"\0" * 4 if self.wrong_range else address) + data
        else:
            assert function == 16 and len(body) == 10
            response = address + (b"\x02" if self.reject else b"\0")
            if not self.discard and not self.reject:
                self.period = Period(
                    f"{body[4]:02d}:{body[5]:02d}", f"{body[6]:02d}:{body[7]:02d}", bool(body[9])
                )
        return Frame(
            1, self.protocol, 1, function, logger_prefix("LOGGER0001", self.protocol) + response
        )


@pytest.mark.parametrize("protocol", [2, 5, 6])
@pytest.mark.parametrize("key", ["charge_1", "charge_3", "discharge_1", "discharge_3"])
def test_whole_period_write_and_readback(protocol, key):
    async def scenario():
        inverter = Inverter(protocol)
        before = await read_period(inverter, "INVERT0001", key)
        updated = Period("22:00", "06:00", True)
        assert (
            await write_period(
                inverter, "INVERT0001", key, updated, before, before_send=lambda: None
            )
            == updated
        )
        writes = [body for function, body in inverter.requests if function == 16]
        assert writes == [addresses(key) + b"\x16\0\x06\0\0\x01"]

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["reject", "discard", "wrong_range"])
def test_failed_schedule_never_claims_success_or_retries_write(fault, monkeypatch):
    async def no_delay(_):
        pass

    monkeypatch.setattr("ha_growatt.controls.asyncio.sleep", no_delay)

    async def scenario():
        inverter = Inverter()
        original = inverter.period
        setattr(inverter, fault, True)
        with pytest.raises(ValueError):
            await write_period(
                inverter,
                "INVERT0001",
                "charge_1",
                Period("21:00", "06:00", True),
                original,
                before_send=lambda: None,
            )
        assert sum(function == 16 for function, _ in inverter.requests) <= 1

    asyncio.run(scenario())


def test_busy_flash_retries_only_confirmation_reads(monkeypatch):
    async def no_delay(_):
        pass

    monkeypatch.setattr("ha_growatt.controls.asyncio.sleep", no_delay)

    async def scenario():
        inverter = Inverter()
        inverter.busy = 2
        updated = replace(inverter.period, start="22:30")
        await write_period(
            inverter, "INVERT0001", "charge_1", updated, inverter.period, before_send=lambda: None
        )
        assert [function for function, _ in inverter.requests] == [5, 16, 5, 5, 5]

    asyncio.run(scenario())


def test_stale_editor_cannot_overwrite_a_changed_period():
    async def scenario():
        inverter = Inverter()
        stale = replace(inverter.period, end="06:00")
        with pytest.raises(ValueError, match="changed"):
            await write_period(
                inverter, "INVERT0001", "charge_1", stale, stale, before_send=lambda: None
            )
        assert all(function == 5 for function, _ in inverter.requests)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "start,end,enabled",
    [
        ("24:00", "05:00", True),
        ("23:60", "05:00", True),
        ("1:00", "05:00", True),
        ("00:00", "00:00", True),
        ("01:00", "05:00", 1),
    ],
)
def test_invalid_period_rejected(start, end, enabled):
    with pytest.raises(ValueError):
        Period(start, end, enabled)


def test_family_scope_and_experimental_gate():
    assert not schedule_keys("mod-6", "sph", True)
    assert not schedule_keys("sph-6", "auto", True)
    assert not schedule_keys("sph-6", "sph", False)
    assert len(schedule_keys("sph-6", "sph", True)) == 6
    assert len(controls_for("mod-6", "mod_tl3_xh", True)) == 5
    assert len(controls_for("mod-6", "auto", True)) == 1
    assert len(controls_for("mod-6", "mod_tl3_xh", False)) == 1
    assert not any(
        c.register in {1070, 1071, 1090, 1091, 1092}
        for c in controls_for("mod-6", "mod_tl3_xh", True)
    )


def test_schedule_discovery_has_real_state_and_connection_gates():
    configs = feature_discovery(
        Device("INVERT0001", "sph-6", 0, ""), True, model="sph", experimental=True
    )
    text = [value for topic, value in configs.items() if topic.startswith("homeassistant/text/")]
    assert len(text) == 12
    assert all(not item["retain"] and item["min"] == item["max"] == 5 for item in text)
    assert all(len(item["availability"]) == 2 for item in text)


def test_app_schedule_requires_a_fresh_connection_and_detects_changed_state():
    from types import SimpleNamespace

    from test_ha_features import Publisher

    from ha_growatt.ha_features import HomeAssistantFeatures
    from ha_growatt.telemetry import Telemetry

    async def scenario():
        inverter = Inverter()
        inverter.session_key = lambda _: 1
        inverter.connection = lambda _: "local"
        inverter.stats = SimpleNamespace(fallback_connections=0)
        features = HomeAssistantFeatures(
            Publisher(),
            inverter,
            SimpleNamespace(failures=0),
            experimental=True,
            models={"INVERT0001": "sph"},
        )
        features.remember(Telemetry({"pvserial": "INVERT0001"}, None, "sph-6"))
        request = {"serial": "INVERT0001", "mode": "charge", "slot": 1}
        result = await features.schedule_action(request | {"action": "read"})
        updated = asdict(replace(inverter.period, start="22:00"))
        result = await features.schedule_action(
            request | {"action": "write", "expected": result["period"], "period": updated}
        )
        assert result["period"] == updated
        device = features.devices["INVERT0001"]
        await device.lock.acquire()
        waiting = asyncio.create_task(features.schedule_action(request | {"action": "read"}))
        await asyncio.sleep(0)
        features.models["INVERT0001"] = "auto"
        before = len(inverter.requests)
        device.lock.release()
        with pytest.raises(ValueError, match="connection changed"):
            await waiting
        assert len(inverter.requests) == before
        features.models["INVERT0001"] = "sph"
        inverter.session_key = lambda _: None
        with pytest.raises(ValueError):
            await features.schedule_action(request | {"action": "read"})

    asyncio.run(scenario())
