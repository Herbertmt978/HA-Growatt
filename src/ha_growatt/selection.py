"""Choose a decoding profile using observed compatibility rules."""

from __future__ import annotations

from dataclasses import dataclass

from .protocol import Frame, ProtocolError
from .telemetry import Decoder, Telemetry, default_profile


@dataclass(frozen=True, slots=True)
class SelectionSettings:
    family: str = "default"
    strict: bool = False
    automatic: bool = True
    minimum_score: int = 20

    def __post_init__(self) -> None:
        if self.family not in {"default", "sph", "mod", "min", "tl3"}:
            raise ValueError("This inverter family has not been verified")
        if not isinstance(self.strict, bool) or not isinstance(self.automatic, bool):
            raise ValueError("Layout switches must be true or false")
        if type(self.minimum_score) is not int:
            raise ValueError("The minimum layout score must be an integer")


# Bounds and weights come from controlled input/output comparisons. A missing
# field contributes nothing; zero voltage and frequency are valid overnight.
_RANGES = (
    (
        ("pvpowerin", "pv1watt", "pv2watt", "pvpowerout", "pvgridpower"),
        10,
        -100_000,
        100_000,
        5,
        -20,
        False,
    ),
    (
        ("pv1voltage", "pv2voltage", "pvgridvoltage", "pvgridvoltage2", "pvgridvoltage3"),
        10,
        50,
        1_000,
        4,
        -18,
        True,
    ),
    (("pvfrequentie",), 100, 45, 65, 4, -18, True),
    (
        ("pvenergytoday", "pvenergytotal", "epvtotal", "epv1today", "epv2today"),
        10,
        0,
        10_000_000,
        3,
        -20,
        False,
    ),
    (("SOC",), 1, 1, 100, 8, -20, False),
)


def plausibility(telemetry: Telemetry) -> int:
    """Score fields after decoding has checked packet bounds and identities."""
    values = telemetry.values
    score = 3 + 15 * sum(key in values for key in ("pvserial", "datalogserial"))
    for names, divisor, low, high, accepted, rejected, allow_zero in _RANGES:
        for key in names:
            if key in values and (key != "SOC" or telemetry.profile == "sph-6"):
                value = values[key] / divisor
                valid = low <= value <= high or (allow_zero and value == 0)
                score += accepted if valid else rejected
    if (
        telemetry.profile == "sph-6"
        and values.get("pvpowerout", 0) > 1_000
        and values.get("SOC", 0) == 0
        and values.get("vbat", 0) == 0
    ):
        score -= 25
    return score


class FamilyDecoder:
    """Select separately for each packet, so mixed devices can share a relay."""

    def __init__(self, settings: SelectionSettings, *, include_all: bool = False) -> None:
        self.settings = settings
        self.include_all = include_all

    def decode(self, frame: Frame) -> Telemetry:
        generic_profile = default_profile(frame)
        extended = generic_profile == "extended-6"
        preferred = f"{self.settings.family}-6" if self.settings.family != "default" else None
        if self.settings.strict:
            if preferred is not None:
                if not extended:
                    raise ProtocolError("No verified family profile matches this frame")
                generic_profile = preferred
            generic = Decoder(generic_profile, include_all=self.include_all).decode(frame)
            if self.settings.minimum_score and (
                plausibility(generic) + (4 if preferred is not None else 0)
                < self.settings.minimum_score
            ):
                raise ProtocolError("Telemetry does not meet the minimum layout score")
            return generic

        profiles = [generic_profile]
        if extended:
            if preferred is not None:
                profiles.insert(0, preferred)
            if self.settings.automatic:
                profiles.extend(["sph-6", "tl3-6", "min-6", "mod-6"])
        candidates = []
        for profile in dict.fromkeys(profiles):
            try:
                candidate = Decoder(profile, include_all=self.include_all).decode(frame)
            except ProtocolError:
                continue
            score = plausibility(candidate) + (4 if profile == preferred else 0)
            candidates.append((score, candidate))
        if not candidates:
            raise ProtocolError("No verified profile matches this frame")
        score, chosen = max(candidates, key=lambda item: item[0])
        if self.settings.minimum_score and score < self.settings.minimum_score:
            raise ProtocolError("Telemetry does not meet the minimum layout score")
        return chosen
