import pytest

from ha_growatt.publisher import MqttSettings
from ha_growatt.relay import RelaySettings
from ha_growatt.selection import FamilyDecoder
from ha_growatt.settings import load_settings


def test_toml_loads_discovery_options_and_private_password(tmp_path, monkeypatch):
    monkeypatch.setenv("HA_GROWATT_MQTT_PASSWORD", "test-only-password")
    path = tmp_path / "bridge.toml"
    path.write_text("""wire_profile = "mod-6"
[relay]
upstream_host = "upstream.invalid"
block_commands = true
[mqtt]
host = "broker.invalid"
entity_profile = "all"
include_all = true
""")
    settings = load_settings(path)
    assert settings.mqtt.password == "test-only-password"
    assert "test-only-password" not in repr(settings)
    assert settings.relay.block_commands
    assert settings.mqtt.include_all
    assert settings.mqtt.entity_profile == "all"


def test_auto_profile_uses_configured_family_selection(tmp_path):
    path = tmp_path / "bridge.toml"
    path.write_text("""wire_profile = "auto"
[selection]
family = "sph"
strict = true
automatic = false
minimum_score = 98
[relay]
upstream_host = "upstream.invalid"
[mqtt]
host = "broker.invalid"
""")
    settings = load_settings(path)
    decoder = settings.decoder()
    assert isinstance(decoder, FamilyDecoder)
    assert decoder.settings.family == "sph"
    assert decoder.settings.strict
    assert not decoder.settings.automatic
    assert decoder.settings.minimum_score == 98


def test_explicit_profile_cannot_silently_ignore_selection(tmp_path):
    path = tmp_path / "bridge.toml"
    path.write_text('wire_profile = "mod-6"\n[selection]\nfamily = "sph"')
    with pytest.raises(ValueError, match="requires"):
        load_settings(path)


@pytest.mark.parametrize("extra", ["unexpected = true", '[mqtt]\npassword = "test-only"'])
def test_unknown_options_and_inline_credentials_fail_before_startup(tmp_path, extra):
    path = tmp_path / "bad.toml"
    path.write_text('wire_profile = "auto"\n' + extra)
    with pytest.raises(ValueError):
        load_settings(path)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0, True, "5"])
def test_deadlines_must_be_finite_positive_numbers(value):
    with pytest.raises(ValueError):
        MqttSettings("broker.invalid", delivery_seconds=value)
    with pytest.raises(ValueError):
        RelaySettings("upstream.invalid", frame_seconds=value)


@pytest.mark.parametrize("value", [True, "1883", -1, 65536])
def test_invalid_ports_are_rejected(value):
    with pytest.raises(ValueError):
        MqttSettings("broker.invalid", port=value)
    with pytest.raises(ValueError):
        RelaySettings("upstream.invalid", upstream_port=value)
