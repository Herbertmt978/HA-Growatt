import json
import os
from pathlib import Path

import pytest

from ha_growatt.settings import load_settings

CASES = json.loads((Path(__file__).parent / "fixtures" / "configuration_cases.json").read_text())


@pytest.fixture(autouse=True)
def clear_configuration_environment(monkeypatch):
    for key in list(os.environ):
        if key.lower().startswith("g") or key == "HA_GROWATT_MQTT_PASSWORD":
            monkeypatch.delenv(key)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["name"])
def test_observed_ini_and_environment_precedence(case, tmp_path, monkeypatch):
    path = tmp_path / "grott.ini"
    path.write_text(case["ini"])
    for key, value in case["environment"].items():
        monkeypatch.setenv(key, value)
    settings = load_settings(path)
    expected = case["expected"]
    assert settings.relay.listen_host == expected["grottip"]
    assert settings.relay.listen_port == expected["grottport"]
    assert settings.relay.upstream_host == expected["growattip"]
    assert settings.relay.upstream_port == expected["growattport"]
    assert settings.relay.block_commands == expected["blockcmd"]
    assert settings.relay.allow_destination_change == expected["noipf"]
    assert settings.selection.family == expected["invtype"]
    assert settings.selection.strict == expected["layout_strict"]
    assert settings.selection.automatic == expected["layout_auto_family"]
    assert settings.selection.minimum_score == expected["layout_min_score"]
    assert settings.mqtt.include_all == expected["includeall"]
    mqtt = expected["extvar"]
    assert settings.mqtt.host == mqtt["ha_mqtt_host"]
    assert settings.mqtt.port == mqtt.get("ha_mqtt_port", 1883)
    assert settings.mqtt.username == mqtt.get("ha_mqtt_user", "")
    assert settings.mqtt.password == mqtt.get("ha_mqtt_password", "")
    assert settings.mqtt.retain_state == mqtt.get("ha_mqtt_retain", False)
    assert settings.mqtt.entity_profile == mqtt.get("ha_entity_profile", "v0_1_9_standard")
    assert "synthetic-password" not in repr(settings)
    assert "override-synthetic" not in repr(settings)


@pytest.mark.parametrize("environment", [{"gcompat": "True"}, {"gpvoutput": "True"}])
def test_incomplete_or_unimplemented_configuration_stops_before_startup(
    environment, tmp_path, monkeypatch
):
    path = tmp_path / "grott.ini"
    path.write_text(CASES[0]["ini"])
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        load_settings(path)


def test_optional_outputs_and_record_policy_are_loaded(tmp_path, monkeypatch):
    path = tmp_path / "grott.ini"
    path.write_text(CASES[0]["ini"])
    for key, value in {
        "gtime": "auto",
        "gsendbuf": "True",
        "gnomqtt": "False",
        "gmqttip": "raw-broker.invalid",
        "gmqttinverterintopic": "True",
        "ginflux": "True",
        "ginflux2": "True",
        "giftoken": "synthetic-token",
        "gifip": "http://influx.invalid:8086",
        "gminrecl": "120",
        "ginvtypemap": '{"INVERT0001":"spf"}',
        "gpvoutput": "True",
        "gpvapikey": "synthetic-key",
        "gpvsystemid": "12345",
    }.items():
        monkeypatch.setenv(key, value)
    settings = load_settings(path)
    assert settings.runtime.home_assistant
    assert settings.runtime.policy.time_source == "auto"
    assert settings.runtime.policy.send_buffered
    assert settings.runtime.raw_mqtt.broker.host == "raw-broker.invalid"
    assert settings.runtime.raw_mqtt.inverter_in_topic
    assert settings.runtime.influx.version == 2
    assert settings.runtime.influx.endpoint == "http://influx.invalid:8086"
    assert settings.runtime.pvoutput.default_system == "12345"
    assert settings.runtime.minimum_record_bytes == 120
    assert settings.selection.device_families == {"INVERT0001": "spf"}
    assert "synthetic-token" not in repr(settings)
    assert "synthetic-key" not in repr(settings)


def test_mapping_cannot_execute_code_or_disclose_its_contents(tmp_path, monkeypatch):
    path = tmp_path / "grott.ini"
    path.write_text(CASES[0]["ini"])
    marker = tmp_path / "executed"
    expression = f"__import__('pathlib').Path({str(marker)!r}).touch() or {{}}"
    monkeypatch.setenv("gextvar", expression)
    with pytest.raises(ValueError) as error:
        load_settings(path)
    assert expression not in str(error.value)
    assert not marker.exists()


def test_password_containing_percent_is_not_interpolated(tmp_path):
    path = tmp_path / "grott.ini"
    path.write_text(CASES[0]["ini"].replace("synthetic-password", "synthetic%password"))
    settings = load_settings(path)
    assert settings.mqtt.password == "synthetic%password"


def test_unknown_configuration_is_not_silently_ignored(tmp_path):
    path = tmp_path / "grott.ini"
    path.write_text(CASES[0]["ini"].replace("[Generic]", "[Generic]\nunknown = True"))
    with pytest.raises(ValueError, match="unsupported"):
        load_settings(path)


def test_addon_options_keep_broker_settings_and_family_choice(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(
        json.dumps(
            {
                "mode": "proxy",
                "time": "server",
                "sendbuf": False,
                "blockcmd": True,
                "invtype": "mod",
                "layout_strict": True,
                "layout_auto_family": False,
                "ha_plugin": True,
                "mqtt_host": "broker.invalid",
                "mqtt_port": 2883,
                "mqtt_user": "synthetic-user",
                "mqtt_password": "synthetic-password",
                "mqtt_retain": True,
                "ha_entity_profile": "all",
            }
        )
    )
    settings = load_settings(path)
    assert settings.mqtt.host == "broker.invalid"
    assert settings.mqtt.port == 2883
    assert settings.mqtt.username == "synthetic-user"
    assert settings.mqtt.password == "synthetic-password"
    assert settings.mqtt.retain_state
    assert settings.mqtt.entity_profile == "all"
    assert settings.selection.family == "mod"
    assert settings.selection.strict
    assert not settings.selection.automatic
    assert settings.relay.listen_host == "0.0.0.0"
    assert "synthetic-password" not in repr(settings)


@pytest.mark.parametrize(
    "options",
    [[], {"unknown": True}, {"ha_plugin": "invalid"}, {"sendbuf": 1}, {"time": "invalid"}],
)
def test_invalid_addon_options_do_not_start(tmp_path, options):
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    with pytest.raises(ValueError):
        load_settings(path)


def test_app_raw_output_and_buffered_time_policy(tmp_path):
    path = tmp_path / "options.json"
    path.write_text(json.dumps({"ha_plugin": False, "sendbuf": True, "time": "auto"}))
    settings = load_settings(path)
    assert not settings.runtime.home_assistant
    assert settings.runtime.raw_mqtt.broker.host == "core-mosquitto"
    assert settings.runtime.policy.send_buffered
    assert settings.runtime.policy.time_source == "auto"


@pytest.mark.parametrize(
    "case",
    json.loads((Path(__file__).parent / "fixtures/optional_configuration_cases.json").read_text()),
    ids=lambda case: case["name"],
)
def test_observed_optional_ini_options_and_overrides(case, tmp_path):
    path = tmp_path / "ha-growatt.ini"
    path.write_text(case["ini"])
    settings = load_settings(path, case["environment"])
    runtime, expected = settings.runtime, case["expected"]
    assert runtime.mode == expected["mode"]
    assert runtime.verbose == expected["verbose"]
    assert runtime.policy.timezone == expected["tmzone"]
    assert runtime.policy.time_source == expected["gtime"]
    assert runtime.policy.send_buffered == expected["sendbuf"]
    assert runtime.inverter_identity == expected["inverterid"]
    if runtime.compatibility:
        assert runtime.value_offset == expected["offset"]
    assert settings.relay.block_commands == expected["blockcmd"]
    assert settings.selection.strict == expected["layout_strict"]
    assert settings.selection.automatic == expected["layout_auto_family"]
    raw = runtime.raw_mqtt
    assert raw.broker.host == expected["mqttip"]
    assert raw.broker.port == expected["mqttport"]
    assert raw.broker.username == expected["mqttuser"]
    assert raw.broker.password == expected["mqttpsw"]
    assert raw.broker.retain_state == expected["mqttretain"]
    assert raw.broker.client_id == expected["inverterid"]
    assert raw.topic == expected["mqtttopic"]
    assert raw.inverter_in_topic == bool(expected["mqttinverterintopic"])
    assert raw.meter_topic == (expected["mqttmtopicname"] if expected["mqttmtopic"] else None)
    assert [
        raw.topic_for({"device": "INVERT0001"}),
        raw.topic_for({"device": "LOGGER0001"}, meter=True),
    ] == case["topics"]
    if runtime.pvoutput:
        assert runtime.pvoutput.interval_minutes == expected["pvuplimit"]
        assert runtime.pvoutput.temperature == expected["pvtemp"]
        assert runtime.pvoutput.omit_energy == expected["pvdisv1"]
        if len(expected["pvsystemid"]) == 1:
            assert runtime.pvoutput.default_system == expected["pvsystemid"]["1"]
        else:
            assert runtime.pvoutput.systems == {
                expected["pvinverterid"][key]: value
                for key, value in expected["pvsystemid"].items()
            }
    if runtime.influx:
        assert runtime.influx.endpoint == f"http://{expected['ifip']}:{expected['ifport']}"
        assert runtime.influx.version == (2 if expected["influx2"] else 1)
        assert runtime.influx.database == expected["ifdbname"]
        assert runtime.influx.organisation == expected["iforg"]
        assert runtime.influx.bucket == expected["ifbucket"]
    assert runtime.extension == (expected["extname"] if expected["extension"] else None)
