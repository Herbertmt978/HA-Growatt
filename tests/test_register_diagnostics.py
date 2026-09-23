import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from test_ha_features import features, packet
from test_support import support

from ha_growatt.device_protocol import logger_prefix
from ha_growatt.protocol import Frame
from ha_growatt.register_diagnostics import read_range


class Inverter:
    def __init__(self):
        self.words = dict.fromkeys(range(65536), 0)
        self.calls = []
        self.session = 1
        self.failure = None
        self.malformed = False
        self.change_session = False
        for start, text in ((9, "GH1.0 GH1.0 "), (125, "MIC 2000TL-X")):
            raw = text.encode().ljust(12 if start == 9 else 16, b"\0")
            for i in range(0, len(raw), 2):
                self.words[start + i // 2] = int.from_bytes(raw[i : i + 2], "big")
        self.words[30000] = 5200
        self.words[30099] = 203

    def session_key(self, _):
        return self.session

    def connection(self, _):
        return "local" if self.session else "disconnected"

    async def command(self, identity, function, body, *, before_send):
        before_send()
        assert function == 5
        start, end = int.from_bytes(body[:2], "big"), int.from_bytes(body[2:], "big")
        self.calls.append((identity, start, end))
        if self.failure:
            raise self.failure
        if self.change_session:
            self.session += 1
        raw = body + b"".join(self.words[i].to_bytes(2, "big") for i in range(start, end + 1))
        if self.malformed:
            raw = raw[:-1]
        return Frame(1, 6, 2, function, logger_prefix("TESTLOG001", 6) + raw)


def service():
    app = features(controls=False)
    app.remember(packet())
    app.transport = Inverter()
    return app


def request(**kwargs):
    return {"identity": "INVERT0001", "operation": "read", "start": 9, "count": 6} | kwargs


@pytest.mark.parametrize(
    "start,count", [(-1, 1), (65535, 2), (0, 33), (0, 0), (True, 1), (0, False), (1.5, 1), ("9", 2)]
)
def test_invalid_ranges_never_reach_transport(start, count):
    with pytest.raises(ValueError):
        read_range(start, count)


def test_identification_is_evidence_only_and_shared_dtc_is_ambiguous():
    async def scenario():
        app = service()
        result = await app.diagnostics.run(request(operation="identify"))
        assert result["model"] == "MIC 2000TL-X"
        assert result["firmware"] == "GH1.0"
        assert result["dtc"] == 5200 and result["vpp_version"] == 203
        assert "MIC" in result["family_hint"] and "MIN" in result["family_hint"]
        assert all(item["status"] == "reported" for item in result["evidence"])
        assert len(app.transport.calls) == 4
        assert not app.hardware and not app.models and not app.controls
        assert app.devices["INVERT0001"].profile == "mod-6"

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", [TimeoutError(), ConnectionError(), ValueError()])
def test_unsupported_registers_do_not_claim_a_model(fault):
    async def scenario():
        app = service()
        app.transport.failure = fault
        result = await app.diagnostics.run(request(operation="identify"))
        assert result["model"] is None and result["firmware"] is None
        assert all(e["status"] == "unavailable" for e in result["evidence"])
        assert not app.hardware

    asyncio.run(scenario())


def test_zero_filled_or_unrecognised_registers_are_not_identity_evidence():
    async def scenario():
        app = service()
        app.transport.words = dict.fromkeys(range(65536), 0)
        result = await app.diagnostics.run(request(operation="identify"))
        assert all(e["status"] == "unrecognised" for e in result["evidence"])
        assert result["family_hint"] is None

    asyncio.run(scenario())


def test_compare_reports_only_changes_and_never_writes():
    async def scenario():
        app = service()
        first = await app.diagnostics.run(request(start=0, count=32))
        assert first["private"] and first["changes"] is None
        app.transport.words[5] = 1234
        app.diagnostics.next_read.clear()
        second = await app.diagnostics.run(request(start=0, count=32, previous=first["snapshot"]))
        assert second["changes"] == [{"address": 5, "before": 0, "after": 1234}]
        assert second["compared_with"] == first["recorded_at"]
        assert len(app.transport.calls) == 2
        app.diagnostics.next_read.clear()
        third = await app.diagnostics.run(request(start=0, count=32, previous=second["snapshot"]))
        assert third["changes"] == []

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["session", "context", "range", "expired", "old_token"])
def test_comparison_rejects_wrong_context_without_reading(change):
    async def scenario():
        app = service()
        first = await app.diagnostics.run(request())
        args = request(previous=first["snapshot"])
        app.diagnostics.next_read.clear()
        if change == "session":
            app.transport.session += 1
        if change == "context":
            app.hardware["INVERT0001"] = {"model": "MIN 2500TL-XH"}
        if change == "range":
            args["start"] = 10
        if change == "expired":
            value = app.diagnostics.snapshots["INVERT0001"]
            app.diagnostics.snapshots["INVERT0001"] = (-1,) + value[1:]
        if change == "old_token":
            args["previous"] = "0" * 32
        with pytest.raises(ValueError, match="Comparison expired"):
            await app.diagnostics.run(args)
        assert len(app.transport.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "args", [{"operation": "write"}, {"operation": []}, {"identity": "UNKNOWN"}, {"count": 33}]
)
def test_invalid_operations_do_not_send(args):
    async def scenario():
        app = service()
        with pytest.raises(ValueError):
            await app.diagnostics.run(request(**args))
        assert not app.transport.calls

    asyncio.run(scenario())


def test_busy_disconnected_and_rate_limits_do_not_queue_work():
    async def scenario():
        app = service()
        app.transport.session = None
        with pytest.raises(ValueError):
            await app.diagnostics.run(request())
        app.transport.session = 1
        async with app.devices["INVERT0001"].lock:
            with pytest.raises(ValueError, match="busy"):
                await app.diagnostics.run(request())
        await app.diagnostics.run(request())
        with pytest.raises(ValueError, match="busy"):
            await app.diagnostics.run(request())
        assert len(app.transport.calls) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize("fault", ["malformed", "change_session"])
def test_invalid_reply_or_mid_read_reconnect_cannot_be_success(fault):
    async def scenario():
        app = service()
        setattr(app.transport, fault, True)
        with pytest.raises(ValueError):
            await app.diagnostics.run(request())
        assert not app.diagnostics.snapshots
        assert app.diagnostics.active == 0 and not app.devices["INVERT0001"].lock.locked()

    asyncio.run(scenario())


def test_mqtt_read_without_write_controls_and_duplicate_expiry_guards():
    async def scenario():
        app = service()
        app._loop = asyncio.get_running_loop()
        worker = asyncio.create_task(app._diagnostic_loop())
        try:
            data = request(request_id="a" * 32, expires=time.time() + 50)
            message = SimpleNamespace(
                topic="ha_growatt/diagnostics/request",
                payload=json.dumps(data).encode(),
                retain=True,
                dup=False,
            )
            app._message(message)
            await asyncio.sleep(0)
            assert app._diagnostic_requests.empty()
            message.retain = False
            app._message(message)
            app._message(message)
            await asyncio.sleep(0)
            await app._diagnostic_requests.join()
            assert len(app.transport.calls) == 1
            topic, payload, retain = app.publisher.messages[-1]
            assert topic.endswith("/response/" + "a" * 32) and not retain
            assert json.loads(payload)["result"]["private"] is True
            app._diagnostic_enqueue(
                json.dumps(data | {"request_id": "b" * 32, "expires": time.time() - 1})
            )
            assert app._diagnostic_requests.empty()
            app._diagnostic_enqueue(json.dumps(data | {"request_id": "c" * 32}))
            app.transport.session += 1
            await app._diagnostic_requests.join()
            assert "expired" in json.loads(app.publisher.messages[-1][1])["result"]["error"]
            assert len(app.transport.calls) == 1
        finally:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(scenario())


def test_private_register_reads_never_enter_redacted_diagnostics():
    async def scenario():
        server = support()
        server.pipeline.features.transport = Inverter()
        headers = {"x-ha-growatt": "1", "content-type": "application/json"}
        code, _, body = await server.dispatch(
            "POST",
            "/api/register-diagnostics",
            headers,
            json.dumps(request(identity="PRIVATE001")).encode(),
        )
        assert code == 200 and json.loads(body)["private"]
        assert "words" not in json.dumps(server.status(redacted=True))
        code, _, body = await server.dispatch(
            "POST",
            "/api/register-diagnostics",
            headers,
            json.dumps(request(identity="PRIVATE001")).encode(),
        )
        assert code == 400 and "busy" in json.loads(body)["error"]

    asyncio.run(scenario())
