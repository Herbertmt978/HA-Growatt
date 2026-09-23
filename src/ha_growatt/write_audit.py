"""Session-local evidence of writes; no retries and no inferred cloud activity."""

import time
from collections import OrderedDict

from .controls import response_body


def words(body, function):
    if function == 6 and len(body) == 4:
        return {int.from_bytes(body[:2], "big"): int.from_bytes(body[2:], "big")}
    if function not in {5, 16} or len(body) < 6:
        return {}
    first, last = int.from_bytes(body[:2], "big"), int.from_bytes(body[2:4], "big")
    if not 0 <= last - first < 125 or len(body) != 4 + 2 * (last - first + 1):
        return {}
    return {
        first + i: int.from_bytes(body[4 + 2 * i : 6 + 2 * i], "big")
        for i in range(last - first + 1)
    }


class WriteAudit:
    def __init__(self):
        self.pending = OrderedDict()
        self.registers = OrderedDict()
        self.sequence = 0
        self.cloud_requests = set()

    def sent(self, frame, source):
        if frame.function not in {5, 6, 16} or (source == "cloud" and frame.function == 5):
            return
        body = response_body(frame)
        if frame.function == 5:
            if len(body) != 4:
                return
        elif not words(body, frame.function):
            return
        self.sequence += 1
        key = (frame.transaction, frame.unit, frame.function)
        if source == "cloud":
            if key in self.cloud_requests or len(self.cloud_requests) >= 256:
                self.pending.pop(key, None)
                return
            self.cloud_requests.add(key)
        self.pending[key] = (frame, source, self.sequence, time.monotonic())
        if len(self.pending) > 128:
            self.pending.popitem(last=False)

    def reply(self, frame):
        key = (frame.transaction, frame.unit, frame.function)
        pending = self.pending.pop(key, None)
        if pending is None:
            return
        request, source, sequence, sent = pending
        width = 30 if frame.protocol == 6 else 10
        if (
            time.monotonic() - sent > 30
            or frame.protocol != request.protocol
            or frame.payload[:width] != request.payload[:width]
        ):
            return
        body, asked = response_body(frame), response_body(request)
        if frame.function == 5:
            if source != "local" or len(asked) != 4 or body[:4] != asked:
                return
            for address, value in words(body, 5).items():
                row = self.registers.get((frame.unit, address))
                if row is None or sequence <= row["sequence"]:
                    continue
                if value != row["expected"]:
                    cloud = row.get("cloud")
                    confirmed = bool(
                        cloud
                        and cloud[0] > row["sequence"]
                        and sequence > cloud[0]
                        and value == cloud[1]
                    )
                    row["finding"] = "cloud" if confirmed else "unconfirmed"
                else:
                    row["finding"] = "matches"
            return
        written = words(asked, frame.function)
        prefix = asked[:2] if frame.function == 6 else asked[:4]
        accepted = {prefix + b"\0"}
        if frame.function == 6:
            accepted.add(asked)
        if not written or body not in accepted:
            return
        for address, value in written.items():
            key = (frame.unit, address)
            if source == "local":
                self.registers[key] = {"expected": value, "sequence": sequence}
                if len(self.registers) > 256:
                    self.registers.popitem(last=False)
            elif key in self.registers and sequence > self.registers[key]["sequence"]:
                self.registers[key]["cloud"] = (sequence, value)

    def status(self, unit):
        findings = {
            row.get("finding") for (device, _), row in self.registers.items() if device == unit
        }
        if "unconfirmed" in findings:
            return "Setting changed; source is unconfirmed"
        if "cloud" in findings:
            return "Cloud write confirmed by readback; differs from requested local setting"
        if "matches" in findings:
            return "Local setting matches readback"
        return "No setting conflict observed this connection"
