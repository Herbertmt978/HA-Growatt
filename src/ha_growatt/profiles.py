"""Observed protocol facts shared by decoding and sensor discovery."""

import json
from functools import cache
from importlib.resources import files


@cache
def wire_profiles() -> dict:
    return json.loads(files("ha_growatt").joinpath("wire_profiles.json").read_text())


def output_fields(profile: str, include_all: bool = False) -> set[str]:
    schema = wire_profiles()[profile]
    keys = set(schema["numeric_fields"]) | set(schema["text_fields"])
    keys |= set(schema.get("log_fields", {})) | set(schema.get("constants", {}))
    if not include_all:
        keys -= set(schema["excluded_fields"])
    return {key.strip() for key in keys}
