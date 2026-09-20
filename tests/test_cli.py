import asyncio
import json
import sys
from pathlib import Path

import pytest

from ha_growatt.__main__ import main
from ha_growatt.protocol import Frame


def test_unsupported_config_has_a_useful_message_without_credentials(tmp_path, monkeypatch, capsys):
    path = tmp_path / "bridge.toml"
    path.write_text('wire_profile = "unverified-family"\n')
    monkeypatch.setenv("HA_GROWATT_MQTT_PASSWORD", "synthetic-password")
    monkeypatch.setattr(sys, "argv", ["ha-growatt", "run", "--config", str(path)])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    diagnostic = capsys.readouterr().err
    assert "Unknown wire profile" in diagnostic
    assert "synthetic-password" not in diagnostic
    assert "unverified-family" not in diagnostic


def test_invalid_file_does_not_echo_its_contents(tmp_path, monkeypatch, capsys):
    path = tmp_path / "options.json"
    path.write_text('{"mqtt_password": "synthetic-secret')
    monkeypatch.setattr(sys, "argv", ["ha-growatt", "run", "--config", str(path)])
    with pytest.raises(SystemExit):
        main()
    diagnostic = capsys.readouterr().err
    assert "Check the file syntax" in diagnostic
    assert "synthetic-secret" not in diagnostic


@pytest.mark.parametrize(
    "observed",
    json.loads((Path(__file__).parent / "fixtures" / "publication_cases.json").read_text()),
    ids=lambda case: f"function-{case['function']}",
)
def test_observed_bridge_publication(tmp_path, monkeypatch, observed):
    from ha_growatt import __main__ as entry

    case = next(
        case
        for case in json.loads(
            (Path(__file__).parent / "fixtures" / "telemetry_cases.json").read_text()
        )
        if case["name"] == observed["case"]
    )
    source = Frame.from_bytes(bytes.fromhex(case["wire"]))
    config = tmp_path / "bridge.toml"
    config.write_text("""wire_profile = "auto"
[relay]
upstream_host = "upstream.invalid"
[mqtt]
host = "broker.invalid"
""")
    published = []
    lifecycle = []

    class Finished(Exception):
        pass

    class Broker:
        def __init__(self, settings):
            pass

        def start(self):
            lifecycle.append("start")

        async def publish(self, telemetry):
            published.append(telemetry.values)

        async def close(self):
            lifecycle.append("close")

    class Connection:
        def __init__(self, settings, observer):
            self.observer = observer

        async def __aenter__(self):
            for direction in ("device", "cloud"):
                await self.observer(
                    direction,
                    Frame(
                        source.transaction,
                        source.protocol,
                        source.unit,
                        observed["function"],
                        source.payload,
                    ),
                )
            raise Finished

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(entry, "Publisher", Broker)
    monkeypatch.setattr(entry, "Relay", Connection)
    with pytest.raises(Finished):
        asyncio.run(entry._run(config))
    assert published == observed["states"]
    assert lifecycle == ["start", "close"]
