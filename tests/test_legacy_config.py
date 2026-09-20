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


@pytest.mark.parametrize(
    "environment",
    [
        {"gmode": "sniff"},
        {"gtime": "auto"},
        {"gsendbuf": "True"},
        {"gnomqtt": "False"},
        {"gextension": "False"},
        {"gextname": "custom_extension"},
        {"gcompat": "True"},
        {"gpvoutput": "True"},
        {"ginflux": "True"},
        {"gminrecl": "120"},
        {"gvalueoffset": "8"},
        {"ginvtypemap": '{"INVERT0001":"sph"}'},
    ],
)
def test_unsupported_paths_are_reported_before_startup(environment, tmp_path, monkeypatch):
    path = tmp_path / "grott.ini"
    path.write_text(CASES[0]["ini"])
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ValueError):
        load_settings(path)


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
    "options", [[], {"unknown": True}, {"ha_plugin": False}, {"sendbuf": True}, {"time": "auto"}]
)
def test_unimplemented_or_invalid_addon_options_do_not_start(tmp_path, options):
    path = tmp_path / "options.json"
    path.write_text(json.dumps(options))
    with pytest.raises(ValueError):
        load_settings(path)
