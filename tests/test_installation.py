import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest
from test_support import support

from ha_growatt.hardware_matrix import render_matrix
from ha_growatt.installation import compatibility_catalogue, installation_checks, mapped_port


@pytest.mark.parametrize(
    ("devices", "fresh", "all_fresh"),
    [
        ([], 0, False),
        ([{"readings": 0, "recent": True, "restored": True}], 0, False),
        ([{"readings": 1, "recent": False, "restored": False}], 0, False),
        ([{"readings": 1, "recent": True, "restored": False}], 1, True),
        (
            [
                {"readings": 1, "recent": True, "restored": False},
                {"readings": 0, "recent": True, "restored": True},
            ],
            1,
            False,
        ),
        ([{"readings": 1, "recent": True, "restored": False}] * 2, 2, True),
    ],
)
def test_first_readings_need_every_device_fresh(devices, fresh, all_fresh):
    status = {
        "devices": devices,
        "listener": True,
        "mqtt_connected": False,
        "transport": {"device_frames": 3},
        "recovery_healthy": False,
    }
    result = installation_checks(status, discovery_enabled=False, features_enabled=False)
    assert result["fresh_inverters"] == fresh
    assert result["all_seen_inverters_fresh"] is all_fresh
    assert result["inverters_seen"] == len(devices)
    assert result["packets"] is True
    assert result["broker"] is False
    assert result["discovery"] is False
    assert result["device_status"] is False


@pytest.mark.parametrize("value", [None, 0, -1, 65536, True, [], "5279/tcp", "５２７９"])
def test_unpublished_or_invalid_port_is_not_guessed(value):
    assert mapped_port({"network": {"5279/tcp": value}}, 5279) is None


def test_mapped_host_port_is_used_without_disclosing_options():
    assert mapped_port({"network": {"5279/tcp": "15279"}}, 5279) == 15279
    assert mapped_port({"network": {"5279/tcp": 15279}}, 5279) == 15279
    assert mapped_port({"network": None}, 5279) is None
    assert mapped_port({}, 5279) is None
    assert mapped_port({"network": {"5279/tcp": 15279}}, 5278) is None


def test_support_setup_routes_are_read_only_and_do_not_return_supervisor_secrets():
    async def scenario():
        service = support()
        calls = []

        def request(path):
            calls.append(path)
            return {"network": {"5279/tcp": 15279}, "options": {"password": "private-test"}}

        service.supervisor = SimpleNamespace(request=request)
        code, mime, body = await service.dispatch("GET", "/api/installation", {}, b"")
        assert code == 200 and mime == "application/json"
        assert json.loads(body) == {"host_port": 15279}
        assert calls == ["/addons/self/info"]
        assert b"private-test" not in body
        _, _, body = await service.dispatch("GET", "/api/compatibility", {}, b"")
        assert json.loads(body) == compatibility_catalogue()
        _, mime, body = await service.dispatch("GET", "/setup.js", {}, b"")
        assert mime == "text/javascript" and b"renderInstallation" in body
        assert "installation" in service.status()
        assert "installation" not in service.status(redacted=True)

    asyncio.run(scenario())


def test_supervisor_unavailable_keeps_setup_page_useful():
    async def scenario():
        service = support()

        def request(path):
            raise ConnectionError("offline")

        for supervisor in (None, SimpleNamespace(request=request)):
            service.supervisor = supervisor
            code, _, body = await service.dispatch("GET", "/api/installation", {}, b"")
            assert code == 200 and json.loads(body) == {"host_port": None}

    asyncio.run(scenario())


def test_published_matrix_matches_packaged_evidence():
    root = Path(__file__).resolve().parents[1]
    assert (root / "docs/hardware-matrix.md").read_text(encoding="utf-8") == render_matrix()
    catalogue = compatibility_catalogue()
    entries = catalogue["entries"]
    assert len({entry["id"] for entry in entries}) == len(entries)
    for entry in entries:
        assert all(
            entry[key]
            for key in (
                "id",
                "model",
                "firmware",
                "datalogger",
                "connection",
                "project",
                "level",
                "telemetry",
                "controls",
                "profile",
                "limits",
                "sources",
            )
        )
        for source in entry["sources"]:
            assert urlparse(source["url"]).scheme == "https"
        if entry["level"] == "Verified installation":
            assert entry["project"].startswith("HA Growatt")
            assert "unconfirmed" in entry["firmware"]
            assert "no battery" in entry["controls"].lower()
