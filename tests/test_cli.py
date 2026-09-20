import sys

import pytest

from ha_growatt.__main__ import main


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
