from dataclasses import replace

import pytest

from ha_growatt.protocol import Frame
from ha_growatt.write_audit import WriteAudit


def frame(sequence, function, *words, unit=2):
    return Frame(
        sequence,
        6,
        unit,
        function,
        b"LOGGER0001" + bytes(20) + b"".join(word.to_bytes(2, "big") for word in words),
    )


def local(audit):
    request = frame(1, 6, 3, 80)
    audit.sent(request, "local")
    audit.reply(request)


def read(audit, value, sequence=3):
    audit.sent(frame(sequence, 5, 3, 3), "local")
    audit.reply(frame(sequence, 5, 3, 3, value))
    return audit.status(2)


def test_cloud_attribution_needs_forwarded_write_ack_and_matching_later_read():
    audit = WriteAudit()
    local(audit)
    request = frame(2, 6, 3, 50)
    audit.sent(request, "cloud")
    audit.reply(request)
    assert read(audit, 50).startswith("Cloud write confirmed")
    assert read(audit, 80, 4) == "Local setting matches readback"


@pytest.mark.parametrize(
    "case",
    [
        "no_cloud",
        "no_ack",
        "rejected",
        "wrong_unit",
        "wrong_value",
        "old_read",
        "expired",
        "reused_transaction",
    ],
)
def test_mismatch_does_not_invent_cloud_cause(case, monkeypatch):
    audit = WriteAudit()
    local(audit)
    request = frame(2, 6, 3, 50)
    if case == "old_read":
        audit.sent(frame(3, 5, 3, 3), "local")
    if case != "no_cloud":
        audit.sent(request, "cloud")
    if case == "reused_transaction":
        audit.sent(request, "cloud")
    if case == "expired":
        monkeypatch.setattr("ha_growatt.write_audit.time.monotonic", lambda: float("inf"))
    if case != "no_ack":
        reply = frame(2, 6, 3, 51) if case == "rejected" else request
        audit.reply(replace(reply, unit=3) if case == "wrong_unit" else reply)
    if case == "old_read":
        audit.reply(frame(3, 5, 3, 3, 50))
        result = audit.status(2)
    else:
        result = read(audit, 60 if case == "wrong_value" else 50)
    assert not result.startswith("Cloud write confirmed")


def test_group_write_ack_and_mixed_registers_do_not_clear_a_conflict():
    audit = WriteAudit()
    local_request = frame(1, 16, 1100, 1102, 256, 512, 1)
    audit.sent(local_request, "local")
    ack = replace(local_request, payload=local_request.payload[:34] + b"\0")
    audit.reply(ack)
    cloud_request = frame(2, 16, 1100, 1102, 768, 512, 1)
    audit.sent(cloud_request, "cloud")
    audit.reply(replace(ack, transaction=2))
    audit.sent(frame(3, 5, 1100, 1102), "local")
    audit.reply(frame(3, 5, 1100, 1102, 768, 512, 1))
    assert audit.status(2).startswith("Cloud write confirmed")


def test_empty_group_ack_does_not_establish_local_baseline():
    audit = WriteAudit()
    request = frame(1, 16, 3, 4, 80, 70)
    audit.sent(request, "local")
    audit.reply(replace(request, payload=request.payload[:30]))
    assert not audit.registers


def test_unrelated_successful_read_does_not_hide_existing_conflict():
    audit = WriteAudit()
    local(audit)
    cloud = frame(2, 6, 3, 50)
    audit.sent(cloud, "cloud")
    audit.reply(cloud)
    assert read(audit, 50).startswith("Cloud write confirmed")
    another = frame(4, 6, 10, 20)
    audit.sent(another, "local")
    audit.reply(another)
    audit.sent(frame(5, 5, 10, 10), "local")
    audit.reply(frame(5, 5, 10, 10, 20))
    assert audit.status(2).startswith("Cloud write confirmed")
