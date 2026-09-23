"""Model constraints layered over the existing, explicitly selected register profiles."""

import re
from dataclasses import dataclass

from .controls import Control, controls_for
from .schedules import schedule_keys


@dataclass(frozen=True)
class Capabilities:
    controls: tuple[Control, ...]
    schedules: tuple[str, ...]
    model_family: str
    battery: str
    explanation: str


def capabilities(profile, selected="auto", experimental=False, exact_model=""):
    # Accept catalogue-style names, not fuzzy substrings or the packet family.
    name = re.sub(r"\s+", " ", exact_model.strip().upper()).removeprefix("GROWATT ")
    mic = bool(re.fullmatch(r"MIC \d+TL-X", name))
    min_xh = bool(re.fullmatch(r"MIN \d+TL-XH", name))
    standard = controls_for(profile, selected, experimental)
    periods = schedule_keys(profile, selected, experimental)
    family = "MIC TL-X" if mic else "MIN TL-XH" if min_xh else "Unconfirmed"
    if mic:
        return Capabilities(
            tuple(c for c in standard if c.key == "output_limit"),
            (),
            family,
            "Not supported",
            "MIC TL-X has no battery controls. Output limit needs a successful readback.",
        )
    if min_xh:
        # A MIN name cannot authorise MOD/SPH registers or change the decoder.
        standard = tuple(
            c
            for c in standard
            if c.key == "output_limit"
            or (c.key.startswith("xh_") and selected == "min_tl_xh" and profile == "min-6")
        )
        if any(c.key.startswith("xh_") for c in standard):
            battery = "Experimental"
            reason = (
                "MIN battery controls are experimental and require readback; "
                "battery hardware is not verified."
            )
        else:
            battery = "Not enabled"
            reason = (
                "MIN battery controls need the MIN TL-XH control profile, "
                "a compatible decoded record and experimental controls enabled."
            )
        return Capabilities(
            standard,
            (),
            family,
            battery,
            reason + " Charging schedules are not supported for this model.",
        )
    return Capabilities(
        standard,
        periods,
        family,
        "Profile-dependent",
        "Exact model is unconfirmed. Controls follow the selected profile and require readback; "
        "this is not hardware qualification.",
    )
