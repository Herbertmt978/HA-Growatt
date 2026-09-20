import hashlib
import json
from pathlib import Path

import pytest

from ha_growatt.protocol import Frame, ProtocolError
from ha_growatt.selection import FamilyDecoder, SelectionSettings, plausibility
from ha_growatt.telemetry import Decoder

FIXTURES = Path(__file__).parent / "fixtures"
PACKETS = {
    case["name"]: case
    for name in ("telemetry_cases.json", "extra_telemetry_cases.json")
    for case in json.loads((FIXTURES / name).read_text())
}
OBSERVED = json.loads((FIXTURES / "selection_cases.json").read_text())
EXTRA = json.loads((FIXTURES / "extra_selection_cases.json").read_text())
for section in OBSERVED:
    OBSERVED[section].extend(EXTRA[section])
LAYOUTS = {
    "classic-2": "T02NNNN",
    "classic-5": "T05NNNN",
    "classic-6": "T06NNNN",
    "extended-6": "T06NNNNX",
    **{f"{family}-6": f"T06NNNNX{family.upper()}" for family in ("sph", "mod", "min", "tl3")},
    "extended-5": "T05NNNNX",
    "sph-5": "T05NNNNXSPH",
    "spf-5": "T05NNNNSPF",
    "spf-6": "T06NNNNSPF",
    "spa-6": "T06NNNNXSPA",
    "meter-6": "T060120",
}


@pytest.mark.parametrize("observed", OBSERVED["scores"], ids=lambda item: item["case"])
def test_observed_plausibility_scores(observed):
    case = PACKETS[observed["case"]]
    telemetry = Decoder(case["profile"], include_all=case["include_all"]).decode(
        Frame.from_bytes(bytes.fromhex(case["wire"]))
    )
    assert plausibility(telemetry) == observed["score"]


@pytest.mark.parametrize("observed", OBSERVED["selections"])
def test_observed_family_selection(observed):
    case = PACKETS[observed["case"]]
    decoder = FamilyDecoder(
        SelectionSettings(
            family=observed["family"],
            strict=observed["strict"],
            automatic=observed["automatic"],
            minimum_score=observed.get("minimum_score", 20),
        )
    )
    frame = Frame.from_bytes(bytes.fromhex(case["wire"]))
    if observed["layout"] is None:
        with pytest.raises(ProtocolError):
            decoder.decode(frame)
    else:
        result = decoder.decode(frame)
        assert LAYOUTS[result.profile] == observed["layout"]
        digest = hashlib.sha256(
            json.dumps(result.values, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        assert digest == observed["digest"]
