"""Compatibility policy for forwarding Growatt control records."""

from .protocol import Frame

PERMITTED_RECORDS = frozenset(
    (unit << 8) | function
    for unit, functions in {
        1: (3, 4, 5, 22, 25, 32, 80),
        80: (3, 4, 5, 22, 25, 27, 80),
        81: (3, 4, 5, 22, 25, 41, 80),
        82: (22, 25, 41, 80),
    }.items()
    for function in functions
)


def may_forward(
    wire: bytes,
    verified: Frame | None,
    permitted: frozenset[int] = PERMITTED_RECORDS,
    allow_destination_change: bool = False,
) -> bool:
    if int.from_bytes(wire[6:8], "big") in permitted:
        return True
    if verified is None or verified.protocol not in {5, 6} or verified.function != 0x18:
        return False
    offset = 30 if verified.protocol == 6 else 10
    command = verified.payload[offset : offset + 2]
    return command == b"\x00\x1f" or (allow_destination_change and command == b"\x00\x11")
