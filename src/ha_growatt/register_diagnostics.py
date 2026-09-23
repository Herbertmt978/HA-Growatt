"""Bounded, explicit holding-register reads. No write operation is accepted."""

import asyncio
import re
import secrets
from datetime import UTC, datetime

from .controls import response_body

MANUFACTURER = "Growatt Modbus RTU V1.24, holding register table"
VPP = "Growatt VPP V2.03 table, as published by Growatt_ModbusTCP"
# These are family hints, not exact models or decoder selections. DTC 5200
# deliberately includes both MIC and MIN; a shared code cannot distinguish them.
FAMILIES = {
    5100: "MIN TL-XH / XH2 / XHE / XA",
    5200: "MIC TL-X / X2 or MIN TL-X / X2",
    5400: "MOD TL3-XH / BP or MID TL3-XH / XHL",
}


def read_range(start, count):
    if type(start) is not int or type(count) is not int:
        raise ValueError("Register address and count must be whole numbers")
    if not 0 <= start <= 65535 or not 1 <= count <= 32 or start + count > 65536:
        raise ValueError("Read 1 to 32 holding registers within addresses 0 to 65535")
    return start.to_bytes(2, "big") + (start + count - 1).to_bytes(2, "big")


async def read_words(transport, identity, start, count, guard):
    request = read_range(start, count)
    guard()
    response = await transport.command(identity, 5, request, before_send=guard)
    guard()
    body = response_body(response)
    if len(body) != 4 + count * 2 or body[:4] != request:
        raise ValueError("The inverter did not return the requested holding registers")
    return [int.from_bytes(body[i : i + 2], "big") for i in range(4, len(body), 2)]


def ascii_words(words):
    try:
        return b"".join(word.to_bytes(2, "big") for word in words).decode("ascii").strip("\0 ")
    except UnicodeError:
        return ""


class RegisterDiagnostics:
    def __init__(self, features):
        self.features = features
        self.snapshots = {}
        self.next_read = {}
        self.active = 0

    async def run(self, data):
        if not isinstance(data, dict) or data.get("operation") not in ("identify", "read"):
            raise ValueError("Choose identify or read; register writes are not supported")
        identity = data.get("identity")
        if not isinstance(identity, str) or identity not in self.features.devices:
            raise ValueError("Choose an inverter already discovered by this app")
        start, count = data.get("start", 0), data.get("count", 1)
        if data["operation"] == "read":
            read_range(start, count)
        previous = data.get("previous")
        if previous is not None and (not isinstance(previous, str) or len(previous) != 32):
            raise ValueError("The comparison snapshot is invalid; take a new first reading")
        loop = asyncio.get_running_loop()
        now = loop.time()
        self.snapshots = {k: v for k, v in self.snapshots.items() if v[0] > now}
        device = self.features.devices[identity]
        transport = self.features.transport
        session = transport.session_key(identity)
        context = self.features.command_context(device)
        deadline = now + 45

        def guard():
            if (
                session is None
                or transport.session_key(identity) != session
                or self.features.command_context(device) != context
                or loop.time() >= deadline
            ):
                raise ValueError("Connection or inverter details changed; start a new reading")

        guard()
        if self.active >= 4 or device.lock.locked() or self.next_read.get(identity, 0) > now:
            raise ValueError("Inverter is busy; wait a few seconds and try again")
        baseline = self.snapshots.get(identity)
        if previous is not None and (
            baseline is None
            or baseline[1] != previous
            or baseline[2:6] != (session, context, start, count)
        ):
            raise ValueError("Comparison expired or the device, range or connection changed")
        self.active += 1
        self.next_read[identity] = now + 5
        try:
            async with asyncio.timeout(45), device.lock:
                if data["operation"] == "identify":
                    return await self.identify(identity, device, guard)
                words = await read_words(transport, identity, start, count, guard)
                token = secrets.token_hex(16)
                stamp = datetime.now(UTC).isoformat()
                result = {
                    "schema": 1,
                    "identity": identity,
                    "private": True,
                    "register_type": "holding",
                    "start": start,
                    "count": count,
                    "recorded_at": stamp,
                    "snapshot": token,
                    "words": words,
                    "changes": None,
                }
                if previous is not None:
                    result["compared_with"] = baseline[6]
                    result["changes"] = [
                        {"address": start + i, "before": old, "after": new}
                        for i, (old, new) in enumerate(zip(baseline[7], words, strict=True))
                        if old != new
                    ]
                self.snapshots[identity] = (
                    loop.time() + 600,
                    token,
                    session,
                    context,
                    start,
                    count,
                    stamp,
                    words,
                )
                return result
        finally:
            self.active -= 1

    async def identify(self, identity, device, guard):
        evidence = []
        result = {
            "schema": 1,
            "identity": identity,
            "private": True,
            "recorded_at": datetime.now(UTC).isoformat(),
            "reading_profile": device.profile,
            "model": None,
            "firmware": None,
            "family_hint": None,
            "evidence": evidence,
            "notice": "Check the inverter label. No profile, metadata or controls were changed.",
        }
        for field, start, count, source in (
            ("firmware", 9, 6, MANUFACTURER),
            ("model", 125, 8, MANUFACTURER),
            ("dtc", 30000, 1, VPP),
            ("vpp_version", 30099, 1, VPP),
        ):
            try:
                words = await read_words(self.features.transport, identity, start, count, guard)
            except (ValueError, TimeoutError, ConnectionError):
                guard()
                evidence.append(
                    {
                        "field": field,
                        "start": start,
                        "count": count,
                        "status": "unavailable",
                        "source": source,
                    }
                )
                continue
            value = None
            if field == "firmware":
                versions = [ascii_words(words[:3]), ascii_words(words[3:])]
                value = (
                    " / ".join(
                        dict.fromkeys(
                            v
                            for v in versions
                            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,5}", v)
                        )
                    )
                    or None
                )
            elif field == "model":
                text = ascii_words(words)
                if re.fullmatch(
                    r"(?:MIC|MIN|MOD|MID|MAX|SPH|SPA|SPF|WIT|WIS) [0-9][A-Z0-9 ./-]{2,12}", text
                ):
                    value = text
            elif words[0] not in {0, 65535}:
                value = words[0]
            result[field] = value
            evidence.append(
                {
                    "field": field,
                    "start": start,
                    "count": count,
                    "status": "reported" if value is not None else "unrecognised",
                    "source": source,
                }
            )
        result["family_hint"] = FAMILIES.get(result.get("dtc"))
        result["confidence"] = (
            "reported model; label confirmation needed"
            if result["model"]
            else "exact model unconfirmed"
        )
        return result
