"""Choose a decoding profile using observed compatibility rules."""

from __future__ import annotations

from dataclasses import dataclass, field

from .profiles import wire_profiles
from .protocol import Frame, ProtocolError
from .telemetry import Decoder, Telemetry


@dataclass(frozen=True, slots=True)
class SelectionSettings:
    family: str = "default"
    strict: bool = False
    automatic: bool = True
    minimum_score: int = 20
    device_families: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        families = {"default", "sph", "spf", "spa", "mod", "min", "tl3", "max"}
        if self.family not in families or any(
            not isinstance(identity, str) or family not in families
            for identity, family in self.device_families.items()
        ):
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
    score = 0 if telemetry.profile.startswith("meter-") else 3
    score += 15 * sum(key in values for key in ("pvserial", "datalogserial"))
    score -= telemetry.decode_errors * 3
    battery = telemetry.profile.startswith(("sph-", "spa-")) or telemetry.profile.endswith(
        ("SPH", "SPA")
    )
    for names, divisor, low, high, accepted, rejected, allow_zero in _RANGES:
        for key in names:
            if key in values and (key != "SOC" or battery):
                actual_divisor = (
                    (telemetry.sensor_metadata or {}).get(key, {}).get("divisor", divisor)
                )
                value = values[key] / actual_divisor
                valid = low <= value <= high or (allow_zero and value == 0)
                score += accepted if valid else rejected
    if (
        battery
        and values.get("pvpowerout", 0) > 1_000
        and values.get("SOC", 0) == 0
        and values.get("vbat", 0) == 0
    ):
        score -= 25
    return score


def layout_name(profile: str) -> str:
    if profile == "compatibility":
        return "compatibility"
    if profile.startswith("custom:"):
        return profile.removeprefix("custom:")
    if profile == "meter-6":
        return "T060120"
    if profile == "meter-log-6":
        return "T06501b"
    family, protocol = profile.rsplit("-", 1)
    prefix = f"T{int(protocol):02x}NNNN"
    if family == "classic":
        return prefix
    if family == "spf":
        return prefix + "SPF"
    return prefix + "X" + (family.upper() if family != "extended" else "")


class FamilyDecoder:
    """Select separately for each packet, including explicitly supplied layouts."""

    def __init__(
        self, settings: SelectionSettings, *, include_all: bool = False, custom_layouts=None
    ):
        self.settings = settings
        self.include_all = include_all
        self.custom_layouts = custom_layouts or {}
        self._builtins = {layout_name(profile): profile for profile in wire_profiles()}

    def _decode(self, name: str, frame: Frame) -> Telemetry:
        if name in self.custom_layouts:
            return self.custom_layouts[name].decode(frame, include_all=self.include_all)
        if name not in self._builtins:
            raise ProtocolError("No verified profile matches this frame")
        return Decoder(self._builtins[name], include_all=self.include_all).decode(frame)

    def decode(self, frame: Frame) -> Telemetry:
        meter = (frame.protocol, frame.unit, frame.function) in {(6, 1, 32), (6, 80, 27)}
        exact = f"T{frame.protocol:02x}{frame.unit:02x}{frame.function:02x}"
        extended = len(frame.to_bytes()) > 375 and not meter
        exact += "X" if extended else ""
        generic = f"T{frame.protocol:02x}NNNN" + ("X" if extended else "")
        if meter:
            return self._decode(exact, frame)
        width = 30 if frame.protocol == 6 else 10
        identity = frame.payload[width : width + 10].decode("ascii", errors="replace").rstrip("\0 ")
        family = self.settings.device_families.get(identity, self.settings.family)
        preferred = (
            [] if family == "default" else [generic + family.upper(), exact + family.upper()]
        )
        if self.settings.strict:
            available = self.custom_layouts.keys() | self._builtins.keys()
            names = [exact, generic] if not preferred else [preferred[1], preferred[0]]
            name = next((name for name in names if name in available), None)
            if name is None:
                raise ProtocolError("No verified family profile matches this frame")
            result = self._decode(name, frame)
            score = plausibility(result) + (4 if preferred else 0)
            if self.settings.minimum_score and score < self.settings.minimum_score:
                raise ProtocolError("Telemetry does not meet the minimum layout score")
            return result
        names = preferred + [exact, generic]
        if self.settings.automatic:
            names.extend(generic + suffix for suffix in ("SPH", "SPF", "TL3", "SPA", "MIN", "MOD"))
        candidates = []
        for name in dict.fromkeys(names):
            try:
                candidate = self._decode(name, frame)
            except ProtocolError:
                continue
            score = plausibility(candidate) + (4 if name in preferred else 0)
            candidates.append((score, candidate))
        if not candidates:
            raise ProtocolError("No verified profile matches this frame")
        score, chosen = max(candidates, key=lambda item: item[0])
        if self.settings.minimum_score and score < self.settings.minimum_score:
            raise ProtocolError("Telemetry does not meet the minimum layout score")
        return chosen
