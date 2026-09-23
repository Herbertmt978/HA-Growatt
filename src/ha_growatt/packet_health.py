"""Bounded layout observations and explicitly private offline replay evidence."""

import asyncio
import re
import time
from collections import OrderedDict

from .profiles import wire_profiles
from .protocol import Frame, ProtocolError

_BLOCK_BYTES = 32
_MAX_REPORTED_BLOCKS = 64
_DECODE_ISSUES = {
    "Frame does not match the selected telemetry profile": "selected_profile_mismatch",
    "Truncated telemetry identity or timestamp": "short_measurement",
    "Telemetry identity is invalid": "invalid_identity",
    "No verified default profile matches this frame": "no_default_profile",
    "No verified profile matches this frame": "no_verified_profile",
    "No verified family profile matches this frame": "no_verified_family_profile",
    "Telemetry does not meet the minimum layout score": "low_layout_score",
}


def _reported_blocks(offsets):
    blocks = sorted(offsets)
    return blocks[:_MAX_REPORTED_BLOCKS], max(0, len(blocks) - _MAX_REPORTED_BLOCKS)


class PacketHealth:
    def __init__(self):
        self.sources = OrderedDict()

    def observe(self, frame, telemetry=None):
        if frame.function != 4:
            return
        # Kept internally only; exports use ordinal labels, including for invalid IDs.
        key = (frame.payload[:10], frame.unit)
        if key not in self.sources:
            if len(self.sources) >= 128:
                self.sources.popitem(last=False)
            self.sources[key] = {"formats": [], "changes": 0, "failures": 0, "state": "Waiting"}
        row = self.sources[key]
        signature = [
            frame.protocol,
            len(frame.payload),
            (telemetry.profile if telemetry.profile in wire_profiles() else "custom")
            if telemetry
            else None,
        ]
        if telemetry is None or telemetry.decode_errors:
            row["failures"] += 1
            row["state"] = "Measurement could not be fully decoded; check profile or firmware"
        else:
            if signature not in row["formats"]:
                if row["formats"]:
                    row["changes"] += 1
                row["formats"] = (row["formats"] + [signature])[-8:]
            row["state"] = (
                "Decoding; a format change was observed" if row["changes"] else "Decoding"
            )

    def export(self):
        return [
            {"source": f"Feed {index}", **row} for index, row in enumerate(self.sources.values(), 1)
        ]


class PrivateCapture:
    """At most 256 valid framed measurements / 2 MiB, removed after 30 minutes."""

    def __init__(self):
        self.clear()

    def clear(self):
        timer = getattr(self, "_timer", None)
        if timer:
            timer.cancel()
        self._timer = None
        self.records = []
        self.size = 0
        self.until = self.expires = 0

    def start(self):
        self.clear()
        self.until = time.monotonic() + 600
        self.expires = time.monotonic() + 1800
        try:
            self._timer = asyncio.get_running_loop().call_later(1800, self.clear)
        except RuntimeError:
            pass

    def stop(self):
        self.until = 0

    @property
    def active(self):
        if self.expires and time.monotonic() >= self.expires:
            self.clear()
        return time.monotonic() < self.until

    def record(self, frame):
        if not self.active or frame.function not in {3, 4, 27, 32, 80}:
            return
        wire = frame.to_bytes()
        if len(self.records) >= 256 or self.size + len(wire) > 2 * 1024 * 1024:
            self.stop()
            return
        self.records.append(wire.hex())
        self.size += len(wire)

    def export(self):
        return {
            "format": "ha-growatt-private-1",
            "active": self.active,
            "warning": (
                "Private: contains serial numbers and household readings. Do not post publicly."
            ),
            "frames": list(self.records),
        }

    def export_shareable(self, decoder):
        """Describe captured frames without copying their contents or readings.

        Thirty-two-byte activity and variation blocks help locate unfamiliar
        record sections without exposing individual bytes or their values.
        """
        if self.expires and time.monotonic() >= self.expires:
            self.clear()
        labels = {}
        records = []
        profiles = wire_profiles()
        configured_profile = getattr(decoder, "profile", None)
        selected_profile = (
            configured_profile
            if isinstance(configured_profile, str)
            and (configured_profile in profiles or configured_profile == "auto")
            else None
        )
        unknown_layouts = {}
        for wire_hex in self.records:
            frame = Frame.from_bytes(bytes.fromhex(wire_hex))
            source = frame.payload[:10]
            if source not in labels:
                labels[source] = f"Feed {len(labels) + 1}"
            active_blocks = {
                offset
                for offset in range(0, len(frame.payload), _BLOCK_BYTES)
                if any(frame.payload[offset : offset + _BLOCK_BYTES])
            }
            visible_blocks, omitted_blocks = _reported_blocks(active_blocks)
            row = {
                "source": labels[source],
                "protocol": frame.protocol,
                "unit": frame.unit,
                "function": frame.function,
                "payload_bytes": len(frame.payload),
                "active_blocks": visible_blocks,
                "active_blocks_omitted": omitted_blocks,
            }
            if selected_profile is not None:
                row["selected_profile"] = selected_profile
            try:
                telemetry = decoder.decode(frame)
            except ProtocolError as error:
                row["result"] = "decode_failed"
                shape = (
                    labels[source],
                    frame.protocol,
                    frame.unit,
                    frame.function,
                    len(frame.payload),
                )
                layout = unknown_layouts.get(shape)
                if layout is None:
                    layout = {
                        "source": labels[source],
                        "protocol": frame.protocol,
                        "unit": frame.unit,
                        "function": frame.function,
                        "payload_bytes": len(frame.payload),
                        "frames": 0,
                        "decode_issues": set(),
                        "selected_profile": selected_profile,
                        "header_compatible_profiles": [
                            name
                            for name, schema in profiles.items()
                            if frame.protocol == schema["protocol"]
                            and frame.function in schema.get("functions", [3, 4, 80])
                        ],
                        "active_blocks": set(),
                        "changing_blocks": set(),
                        "reference": frame.payload,
                    }
                    unknown_layouts[shape] = layout
                layout["frames"] += 1
                layout["decode_issues"].add(_DECODE_ISSUES.get(str(error), "other"))
                layout["active_blocks"].update(active_blocks)
                reference = layout["reference"]
                layout["changing_blocks"].update(
                    offset
                    for offset in range(0, len(frame.payload), _BLOCK_BYTES)
                    if frame.payload[offset : offset + _BLOCK_BYTES]
                    != reference[offset : offset + _BLOCK_BYTES]
                )
            else:
                row.update(
                    result="decoded",
                    profile=telemetry.profile if telemetry.profile in profiles else "custom",
                    decoded_fields=len(telemetry.values),
                    incomplete_fields=telemetry.decode_errors,
                )
                if telemetry.profile in profiles:
                    fields = profiles[telemetry.profile]["numeric_fields"]
                    row["missing_fields"] = [
                        name.strip()
                        for name, (offset, size, _) in fields.items()
                        if offset + size > len(frame.payload)
                    ]
            records.append(row)
        unknown = []
        for layout in unknown_layouts.values():
            active, active_omitted = _reported_blocks(layout["active_blocks"])
            changing, changing_omitted = _reported_blocks(layout["changing_blocks"])
            unknown.append(
                {
                    "layout": f"Undecoded layout {len(unknown) + 1}",
                    "source": layout["source"],
                    "protocol": layout["protocol"],
                    "unit": layout["unit"],
                    "function": layout["function"],
                    "payload_bytes": layout["payload_bytes"],
                    "frames": layout["frames"],
                    "decode_issues": sorted(layout["decode_issues"]),
                    "selected_profile": layout["selected_profile"],
                    "header_compatible_profiles": layout["header_compatible_profiles"],
                    "active_blocks": active,
                    "active_blocks_omitted": active_omitted,
                    "changing_blocks": changing,
                    "changing_blocks_omitted": changing_omitted,
                }
            )
        return {
            "format": "ha-growatt-shareable-1",
            "active": self.active,
            "limit": 256,
            "notice": (
                "Contains packet structure and decode outcomes only. Block offsets show "
                "activity and changes, not values. No packet bytes, serial numbers, "
                "exact times or measurement values are included."
            ),
            "records": records,
            "undecoded_layouts": unknown,
        }

    def export_serial_redacted(self, decoder):
        """Make replayable packets with only known numeric fields and fake identities."""
        if self.expires and time.monotonic() >= self.expires:
            self.clear()
        profiles = wire_profiles()
        loggers, inverters = {}, {}
        frames = []
        skipped = 0
        for wire_hex in self.records:
            frame = Frame.from_bytes(bytes.fromhex(wire_hex))
            if frame.function not in {4, 32, 80}:
                skipped += 1
                continue
            try:
                telemetry = decoder.decode(frame)
            except ProtocolError:
                skipped += 1
                continue
            schema = profiles.get(telemetry.profile)
            if schema is None or "log_fields" in schema:
                skipped += 1
                continue
            width = 30 if frame.protocol == 6 else 10
            if len(frame.payload) < width * 2 + 6:
                skipped += 1
                continue
            payload = bytearray(len(frame.payload))
            for name, offset, seen, prefix in (
                ("datalogserial", 0, loggers, "LOGGER"),
                ("pvserial", width, inverters, "INVERT"),
            ):
                if name not in schema["text_fields"]:
                    continue
                identity = frame.payload[offset : offset + 10]
                if identity not in seen:
                    seen[identity] = f"{prefix}{len(seen) + 1:04d}".encode()
                payload[offset : offset + 10] = seen[identity]
            payload[width * 2 : width * 2 + 6] = bytes((24, 1, 1, 12, 0, 0))
            for offset, size, _signed in schema["numeric_fields"].values():
                if offset >= width * 2 + 6 and offset + size <= len(payload):
                    payload[offset : offset + size] = frame.payload[offset : offset + size]
            # A wrong-but-decodable layout could place an identity inside a
            # numeric range. Remove long text runs before making valid wire bytes.
            body_start = width * 2 + 6
            for match in re.finditer(rb"[A-Za-z0-9]{8,}", payload[body_start:]):
                start = body_start + match.start()
                payload[start : body_start + match.end()] = bytes(len(match.group()))
            clean = Frame(0, frame.protocol, frame.unit, frame.function, bytes(payload))
            frames.append({"profile": telemetry.profile, "wire": clean.to_bytes().hex()})
        return {
            "format": "ha-growatt-serial-redacted-1",
            "warning": (
                "Serials, exact times and unknown bytes are replaced, but real numeric "
                "readings remain. Review before sharing."
            ),
            "frames": frames,
            "skipped": skipped,
        }


def replay_capture(data, decoder):
    if not isinstance(data, dict) or data.get("format") not in {
        "ha-growatt-private-1",
        "ha-growatt-serial-redacted-1",
    }:
        raise ValueError("Not a HA Growatt replay capture")
    redacted = data["format"] == "ha-growatt-serial-redacted-1"
    frames = data.get("frames")
    if not isinstance(frames, list) or len(frames) > 256:
        raise ValueError("Invalid capture frame count")
    results = []
    size = 0
    if redacted:
        from .telemetry import Decoder

    for item in frames:
        if redacted:
            if not isinstance(item, dict) or item.get("profile") not in wire_profiles():
                raise ValueError("Invalid redacted capture profile")
            selected_decoder = Decoder(item["profile"])
            item = item.get("wire")
        else:
            selected_decoder = decoder
        if not isinstance(item, str) or len(item) > 131086:
            raise ValueError("Invalid captured frame")
        size += len(item)
        if size > 4 * 1024 * 1024:
            raise ValueError("Capture exceeds its size limit")
        try:
            frame = Frame.from_bytes(bytes.fromhex(item))
            telemetry = selected_decoder.decode(frame)
        except (ValueError, ProtocolError):
            results.append({"result": "decode_failed"})
        else:
            results.append(
                {
                    "result": "decoded",
                    "fields": len(telemetry.values),
                    "incomplete_fields": telemetry.decode_errors,
                }
            )
    return {"frames": results, "writes": 0}
