import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from ha_growatt.outputs import (
    Extension,
    HttpWrite,
    InfluxOutput,
    InfluxSettings,
    PublicationPolicy,
    PVOutput,
    PVOutputSettings,
    RawMqttSettings,
    influx_point,
    influx_request,
    send_http,
)
from ha_growatt.protocol import Frame
from ha_growatt.publisher import MqttSettings
from ha_growatt.telemetry import Decoder, Telemetry

FIXTURES = Path(__file__).parent / "fixtures"
CASES = json.loads((FIXTURES / "output_cases.json").read_text())


@pytest.mark.parametrize("case", CASES)
def test_recorded_output_contracts(case, monkeypatch):
    source = next(
        c
        for c in json.loads((FIXTURES / "telemetry_cases.json").read_text())
        if c["name"] == case["case"]
    )
    frame = Frame.from_bytes(bytes.fromhex(source["wire"]))
    telemetry = Decoder(source["profile"]).decode(
        Frame(
            frame.transaction,
            frame.protocol,
            frame.unit,
            80 if case["buffered"] else 4,
            frame.payload,
        )
    )
    policy = PublicationPolicy(case["time"], case["sendbuf"])
    message = policy.message(telemetry, datetime(2026, 9, 20, 9, 30))
    expected = case["captures"]
    if not expected:
        assert message is None
        return
    mode = case["mode"]
    if mode in {"raw", "topic-device"}:
        raw = RawMqttSettings(MqttSettings("localhost"), inverter_in_topic=mode == "topic-device")
        assert raw.topic_for(message) == expected[0]["topic"]
        assert message == expected[0]["message"]
    elif mode.startswith("pvoutput"):
        sink = PVOutput(
            PVOutputSettings(
                "synthetic-api-key",
                default_system="12345",
                interval_minutes=0,
                temperature=mode == "pvoutput-temp",
                omit_energy=mode == "pvoutput-nov1",
            )
        )
        request = sink.requests(message)[0]
        assert request.url == expected[0]["url"]
        assert parse_qs(request.body.decode()) == {
            k: [str(v)] for k, v in expected[0]["data"].items()
        }
        assert request.headers.items() >= expected[0]["headers"].items()
    elif mode.startswith("influx"):
        point = influx_point(message, "UTC")
        assert point == expected[0]["args"][-1][0]
    else:
        observed = []
        monkeypatch.setitem(
            __import__("sys").modules,
            "synthetic_extension",
            SimpleNamespace(
                grottext=lambda config, packet, body: observed.append((config, packet, body))
            ),
        )
        Extension("synthetic_extension", {"test": "value"}).publish(message, "aabb")
        config, packet, body = observed[0]
        assert config.extvar == expected[0]["extvar"]
        assert packet == "aabb"
        assert json.loads(body) == expected[0]["message"]


def test_pvoutput_limits_each_inverter_and_skips_unmapped_devices():
    sink = PVOutput(PVOutputSettings("synthetic", systems={"first": "1", "second": "2"}))
    message = {
        "device": "first",
        "time": "2026-09-20T09:30:00",
        "values": {"pvpowerout": 1000, "pvgridvoltage": 2300, "pvenergytoday": 15},
    }
    assert sink.requests(message, clock=0)
    assert sink.requests(message, clock=299) == []
    assert sink.requests(message | {"device": "second"}, clock=299)
    assert sink.requests(message | {"device": "unknown"}, clock=300) == []
    assert sink.requests(message, clock=300)


def test_buffered_data_without_a_valid_timestamp_is_not_relabelled_as_live():
    data = Telemetry({"pvserial": "INVERT0001"}, None, "classic-6", True)
    assert PublicationPolicy().message(data) is None
    assert PublicationPolicy("server").message(data) is None


def test_influx_wire_format_preserves_types_escaping_and_timezone():
    message = {
        "device": "test device",
        "time": "2026-09-20T09:30:00",
        "values": {"power": 42, "text field": 'a"b\\c', "float": -1.5},
    }
    point = influx_point(message, "Europe/London")
    assert point["time"] == "2026-09-20T08:30:00"
    settings = InfluxSettings(version=2, token="synthetic-token", organisation="test org")
    request = influx_request(settings, message, "Europe/London")
    assert (
        request.url == "http://localhost:8086/api/v2/write?org=test+org&bucket=grottdb&precision=s"
    )
    assert request.body.startswith(b'test\\ device power=42i,text\\ field="a\\"b\\\\c",float=-1.5 ')
    assert "synthetic-token" not in repr(settings)
    assert "synthetic-token" not in repr(request)


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("2026-10-25T01:30:00", "2026-10-25T01:30:00"),
        ("2026-03-29T01:30:00", "2026-03-29T01:30:00"),
    ],
)
def test_influx_daylight_saving_ambiguities_use_standard_time(stamp, expected):
    assert (
        influx_point({"device": "test", "time": stamp, "values": {}}, "Europe/London")["time"]
        == expected
    )


@pytest.mark.parametrize("version,exists", [(1, False), (1, True), (2, True)])
def test_influx_http_authentication_database_creation_and_repeat_writes(version, exists):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            seen.append((self.command, self.path, body, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            if self.command == "GET":
                response = {
                    "results": [{"series": [{"values": [["test database"]] if exists else []}]}]
                }
            else:
                response = {"results": [{}]}
            self.wfile.write(json.dumps(response).encode())

        do_GET = handle_request
        do_POST = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sink = InfluxOutput(
            InfluxSettings(
                f"http://127.0.0.1:{server.server_port}",
                version,
                "test database",
                "test",
                "password",
                "synthetic-token",
                "test org",
                "test bucket",
            ),
            "UTC",
        )
        message = {"device": "test", "time": "2026-09-20T09:30:00", "values": {"power": 1200}}
        sink.publish(message)
        sink.publish(message)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    if version == 1:
        assert seen[0][0:2] == ("GET", "/query?q=SHOW+DATABASES")
        if not exists:
            assert parse_qs(seen[1][2].decode()) == {"q": ['CREATE DATABASE "test database"']}
        assert all(record[3] == "Basic dGVzdDpwYXNzd29yZA==" for record in seen)
        assert len(seen) == (3 if exists else 4)
        assert seen[-1][1] == "/write?db=test+database&precision=s"
    else:
        assert len(seen) == 2
        assert seen[-1][1] == "/api/v2/write?org=test+org&bucket=test+bucket&precision=s"
        assert all(record[3] == "Token synthetic-token" for record in seen)
    assert seen[-1][2].startswith(b"test power=1200i ")


def test_http_delivery_uses_post_and_does_not_expose_failed_response():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            seen.append(self.rfile.read(int(self.headers["Content-Length"])))
            self.send_response(204 if self.path == "/ok" else 401)
            self.end_headers()
            if self.path != "/ok":
                self.wfile.write(b"synthetic-secret")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        send_http(HttpWrite(endpoint + "/ok", b"first"))
        with pytest.raises(OSError, match="Telemetry HTTP delivery failed") as error:
            send_http(HttpWrite(endpoint + "/fail", b"second"))
        assert "synthetic-secret" not in str(error.value)
        assert seen == [b"first", b"second"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
