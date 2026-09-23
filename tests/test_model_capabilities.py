import asyncio
from contextlib import suppress

import pytest
from test_ha_features import features, packet

from ha_growatt.capabilities import capabilities
from ha_growatt.ha_features import feature_discovery


@pytest.mark.parametrize(
    "profile,selected",
    [("sph-6", "sph"), ("spa-6", "spa"), ("mod-6", "mod_tl3_xh"), ("min-6", "min_tl_xh")],
)
def test_mic_never_exposes_battery_controls_or_schedules(profile, selected):
    allowed = capabilities(profile, selected, True, "Growatt MIC 2000TL-X")
    assert [c.key for c in allowed.controls] == ["output_limit"]
    assert not allowed.schedules
    assert allowed.battery == "Not supported"


@pytest.mark.parametrize(
    "profile,selected,experimental,expected",
    [
        ("mod-6", "mod_tl3_xh", True, False),
        ("sph-6", "sph", True, False),
        ("min-6", "min_tl_xh", False, False),
        ("min-6", "auto", True, False),
        ("min-6", "min_tl_xh", True, True),
    ],
)
def test_min_requires_existing_compatible_explicit_profile(
    profile, selected, experimental, expected
):
    allowed = capabilities(profile, selected, experimental, "MIN 2500TL-XH")
    assert bool([c for c in allowed.controls if c.key.startswith("xh_")]) == expected
    assert not allowed.schedules
    assert all(c.key not in {"charge_rate", "discharge_soc"} for c in allowed.controls)


def test_unknown_model_keeps_legacy_profile_behaviour():
    allowed = capabilities("sph-6", "sph", True, "Unverified device")
    assert len(allowed.controls) == 6 and len(allowed.schedules) == 6
    assert "unconfirmed" in allowed.explanation


def test_model_constraints_apply_to_discovery_refresh_and_commands():
    async def scenario():
        service = features()
        service.experimental = True
        service.models = {"INVERT0001": "sph"}
        service.hardware = {"INVERT0001": {"model": "MIC 2000TL-X"}}
        service.remember(packet("sph-6"))
        device = service.devices["INVERT0001"]
        configs = feature_discovery(
            device, True, model="sph", experimental=True, hardware=service.hardware[device.identity]
        )
        assert not any("charge" in topic for topic in configs)
        calls = []

        async def command(identity, function, body, **kwargs):
            calls.append((function, body))
            raise TimeoutError

        service.transport.command = command
        await service.refresh(device)
        assert calls == [(5, b"\0\3\0\3")]
        calls.clear()
        device.values["charge_rate"] = 50  # Previously cached before the model was corrected.
        await service.execute("ha_growatt/INVERT0001/command/charge_rate", b"60")
        assert not calls
        with pytest.raises(ValueError, match="no enabled"):
            await service.schedule_action(
                {"serial": device.identity, "mode": "charge", "slot": 1, "action": "read"}
            )
        assert not calls

    asyncio.run(scenario())


def test_queued_command_is_rejected_after_model_changes():
    async def scenario():
        service = features()
        service._loop = asyncio.get_running_loop()
        service.remember(packet())
        service._enqueue("ha_growatt/INVERT0001/command/output_limit", b"50")
        service.hardware = {"INVERT0001": {"model": "MIC 2000TL-X"}}

        async def forbidden(*args, **kwargs):
            raise AssertionError("A command queued under different capabilities must not execute")

        service.execute = forbidden
        task = asyncio.create_task(service._control_loop())
        try:
            await asyncio.wait_for(service._commands.join(), 1)
            assert service.rejected_commands == 1
        finally:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
