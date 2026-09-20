"""Read manufacturer-documented identity fields without changing registers."""

import re

from .controls import response_body


async def read_firmware(transport, identity):
    # Growatt Modbus V1.24 holding registers 9–11 and 12–14 contain
    # two six-character ASCII firmware identifiers. Do not guess binary values.
    request = b"\x00\x09\x00\x0e"
    response = await transport.command(identity, 5, request)
    body = response_body(response)
    if len(body) != 16 or body[:4] != request:
        raise ValueError("The inverter did not return its firmware registers")
    versions = []
    for raw in (body[4:10], body[10:16]):
        try:
            version = raw.decode("ascii").strip("\x00 ")
        except UnicodeError:
            continue
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{2,5}", version) and version not in versions:
            versions.append(version)
    if not versions:
        raise ValueError("Firmware text is unavailable; enter the version shown by the inverter")
    return " / ".join(versions)
