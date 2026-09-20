"""Shared datalogger replies and register command envelopes."""

from datetime import datetime

from .discovery import validate_identity
from .protocol import Frame


def logger_prefix(identity: str, protocol: int) -> bytes:
    validate_identity(identity)
    encoded = identity.encode("ascii")
    if len(encoded) != 10:
        raise ValueError("A datalogger identity must contain ten characters")
    return encoded.ljust(30 if protocol == 6 else 10, b"\0")


def time_command(identity: str, protocol: int, sequence: int, now: datetime) -> Frame:
    stamp = now.strftime("%Y-%m-%d %H:%M:%S").encode("ascii")
    payload = (
        logger_prefix(identity, protocol) + b"\x00\x1f" + len(stamp).to_bytes(2, "big") + stamp
    )
    return Frame(sequence, protocol, 1, 24, payload)


def acknowledgement(frame: Frame) -> Frame | None:
    if frame.function == 22:
        return frame
    if frame.function in {3, 4, 27, 32, 80}:
        return Frame(frame.transaction, frame.protocol, frame.unit, frame.function, b"\0")
    return None


def register_command(
    logger: str,
    protocol: int,
    unit: int,
    sequence: int,
    method: str,
    target: str,
    options: dict[str, str],
    now: datetime | None = None,
) -> Frame:
    command = options.get("command", "")
    prefix = logger_prefix(logger, protocol)
    if method == "PUT" and command == "datetime":
        if target != "datalogger":
            raise ValueError("datetime command not allowed for inverter")
        return time_command(logger, protocol, sequence, now or datetime.now())
    if method == "PUT" and command == "multiregister" and target == "inverter":
        start, end = int(options["startregister"]), int(options["endregister"])
        if not 0 <= start <= end <= 65535:
            raise ValueError("Invalid register range")
        values = bytes.fromhex(options["value"])
        if len(values) != (end - start + 1) * 2:
            raise ValueError("Register range does not match the supplied values")
        return Frame(
            sequence,
            protocol,
            unit,
            16,
            prefix + start.to_bytes(2, "big") + end.to_bytes(2, "big") + values,
        )
    if command != "register" or method not in {"GET", "PUT"}:
        raise ValueError("no valid command entered")
    register = int(options["register"])
    if not 0 <= register <= 65535:
        raise ValueError("Invalid register")
    address = register.to_bytes(2, "big")
    if method == "GET":
        return Frame(
            sequence, protocol, unit, 25 if target == "datalogger" else 5, prefix + address * 2
        )
    value = options["value"]
    if target == "datalogger":
        encoded = value.encode("utf-8")
        return Frame(
            sequence,
            protocol,
            unit,
            24,
            prefix + address + len(encoded).to_bytes(2, "big") + encoded,
        )
    format_ = options.get("format", "dec")
    if format_ == "text":
        encoded = value.encode("utf-8")
    elif format_ in {"hex", "dec"}:
        encoded = int(value, 16 if format_ == "hex" else 10).to_bytes(2, "big")
    else:
        raise ValueError("Invalid register format")
    return Frame(sequence, protocol, unit, 6, prefix + address + encoded)


def register_response(frame: Frame) -> tuple[str, dict] | None:
    offset = 30 if frame.protocol == 6 else 10
    body = frame.payload[offset:]
    if frame.function in {5, 6, 24, 25} and len(body) >= 3:
        key = body[:2].hex()
        if frame.function == 5 and len(body) >= 6:
            return key, {"value": body[4:].hex()}
        if frame.function == 6 and len(body) >= 4:
            return key, {"value": body[2:].hex()}
        if frame.function == 24:
            return key, {"result": body[2:3].hex()}
        if frame.function == 25 and len(body) >= 4:
            length = int.from_bytes(body[2:4], "big")
            if len(body) >= 4 + length:
                return key, {"value": body[4 : 4 + length].decode("utf-8", errors="replace")}
    if frame.function == 16 and len(body) >= 5:
        return body[:4].hex(), {"value": body[4:5].hex()}
    return None
