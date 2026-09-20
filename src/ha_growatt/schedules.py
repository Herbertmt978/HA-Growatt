"""SPH/SPA time periods, sent as one holding-register range."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .controls import confirm_read, response_body

_TIME = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]\Z")


@dataclass(frozen=True)
class Period:
    start: str
    end: str
    enabled: bool

    def __post_init__(self):
        if (
            not isinstance(self.start, str)
            or not isinstance(self.end, str)
            or not _TIME.fullmatch(self.start)
            or not _TIME.fullmatch(self.end)
            or type(self.enabled) is not bool
        ):
            raise ValueError("Use 24-hour times and an enabled switch")
        if self.enabled and self.start == self.end:
            raise ValueError("An enabled period needs different start and end times")

    def words(self):
        return tuple((int(value[:2]) << 8) | int(value[3:]) for value in (self.start, self.end)) + (
            int(self.enabled),
        )


def schedule_keys(profile: str, model: str, experimental: bool) -> tuple[str, ...]:
    if not experimental or not (
        (profile in {"sph-5", "sph-6"} and model == "sph")
        or (profile == "spa-6" and model == "spa")
    ):
        return ()
    return tuple(f"{mode}_{slot}" for mode in ("charge", "discharge") for slot in range(1, 4))


def addresses(key: str) -> bytes:
    if key not in {f"{mode}_{slot}" for mode in ("charge", "discharge") for slot in range(1, 4)}:
        raise ValueError("Unsupported period")
    mode, slot = key.split("_")
    start = (1100 if mode == "charge" else 1080) + (int(slot) - 1) * 3
    return start.to_bytes(2, "big") + (start + 2).to_bytes(2, "big")


async def read_period(transport, identity: str, key: str) -> Period:
    address = addresses(key)
    response = await transport.command(identity, 5, address)
    body = response_body(response)
    if len(body) != 10 or body[:4] != address:
        raise ValueError("The inverter did not return this complete period")
    words = [int.from_bytes(body[index : index + 2], "big") for index in (4, 6, 8)]
    if words[2] not in {0, 1}:
        raise ValueError("The period uses an unsupported enable value")
    return Period(*(f"{word >> 8:02d}:{word & 255:02d}" for word in words[:2]), bool(words[2]))


async def write_period(transport, identity, key, period: Period, expected: Period, *, before_send):
    # Read again to catch another user or the cloud changing the displayed slot.
    before_send()
    if await read_period(transport, identity, key) != expected:
        raise ValueError("This period changed; read it again before saving")
    address = addresses(key)
    words = b"".join(word.to_bytes(2, "big") for word in period.words())
    response = await transport.command(identity, 16, address + words, before_send=before_send)
    if response_body(response) != address + b"\0":
        raise ValueError("The inverter rejected this period; no write was retried")
    return await confirm_read(
        lambda: read_period(transport, identity, key), period, before_send=before_send
    )
