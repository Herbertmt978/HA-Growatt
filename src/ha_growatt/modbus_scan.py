"""Paced, read-only direct Modbus TCP discovery for an explicitly named gateway.

This is separate from the Shine datalogger upload connection. Only Modbus
functions 03 (holding) and 04 (input) can be sent by this module.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
from datetime import UTC, datetime
from pathlib import Path

MAX_WORDS = 32
MAX_ADDRESSES = 512
MIN_DELAY = 0.5
FUNCTIONS = {"holding": 3, "input": 4}


def parse_range(value: str) -> tuple[int, int]:
    try:
        first, count = (int(part) for part in value.split(":"))
    except (ValueError, TypeError) as error:
        raise argparse.ArgumentTypeError(
            "Use START:COUNT in decimal, for example 3000:125"
        ) from error
    if not 0 <= first <= 65535 or not 1 <= count <= MAX_ADDRESSES or first + count > 65536:
        raise argparse.ArgumentTypeError("A range must contain 1–512 addresses within 0–65535")
    return first, count


class ReadError(Exception):
    """The named register block could not be read or validated."""

    def __init__(self, message: str, *, splittable: bool = False):
        super().__init__(message)
        self.splittable = splittable


class ModbusReader:
    def __init__(self, host: str, port: int, unit: int, delay: float, timeout: float):
        if not host or not 1 <= port <= 65535 or not 1 <= unit <= 247:
            raise ValueError("Enter a gateway address, TCP port and Modbus unit 1–247")
        if delay < MIN_DELAY or not 0.5 <= timeout <= 10:
            raise ValueError(
                "Use at least 0.5 seconds between requests and a 0.5–10 second timeout"
            )
        self.host, self.port, self.unit = host, port, unit
        self.delay, self.timeout = delay, timeout
        self._last_send = 0.0

    async def read(self, kind: str, first: int, count: int) -> list[int]:
        if (
            kind not in FUNCTIONS
            or not 0 <= first <= 65535
            or not 1 <= count <= MAX_WORDS
            or first + count > 65536
        ):
            raise ValueError("Invalid read-only register block")
        loop = asyncio.get_running_loop()
        await asyncio.sleep(max(0, self.delay - (loop.time() - self._last_send)))
        function = FUNCTIONS[kind]
        transaction = secrets.randbelow(65536)
        request = (
            transaction.to_bytes(2, "big")
            + b"\x00\x00\x00\x06"
            + bytes((self.unit, function))
            + first.to_bytes(2, "big")
            + count.to_bytes(2, "big")
        )
        writer = None
        try:
            async with asyncio.timeout(self.timeout):
                reader, writer = await asyncio.open_connection(self.host, self.port)
                writer.write(request)
                self._last_send = loop.time()
                await writer.drain()
                header = await reader.readexactly(7)
                if (
                    header[:2] != request[:2]
                    or header[2:4] != b"\x00\x00"
                    or header[6] != self.unit
                ):
                    raise ReadError("Mismatched Modbus reply")
                size = int.from_bytes(header[4:6], "big")
                if not 3 <= size <= 3 + MAX_WORDS * 2:
                    raise ReadError("Invalid Modbus reply length")
                payload = await reader.readexactly(size - 1)
                if payload[:1] == bytes((function | 0x80,)) and len(payload) == 2:
                    raise ReadError(
                        f"Modbus exception {payload[1]}",
                        splittable=payload[1] == 2,
                    )
                if payload[:2] != bytes((function, count * 2)) or len(payload) != 2 + count * 2:
                    raise ReadError("Mismatched Modbus values")
                return [
                    int.from_bytes(payload[i : i + 2], "big") for i in range(2, len(payload), 2)
                ]
        except (OSError, TimeoutError, asyncio.IncompleteReadError) as error:
            raise ReadError(type(error).__name__) from error
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except OSError:
                    pass


async def scan(
    reader: ModbusReader,
    ranges: list[tuple[int, int]],
    kinds: tuple[str, ...] = ("input", "holding"),
    max_requests: int = 128,
) -> dict:
    if not ranges or sum(count for _, count in ranges) > MAX_ADDRESSES:
        raise ValueError("Choose at most 512 addresses across all ranges")
    if not kinds or any(kind not in FUNCTIONS for kind in kinds) or not 1 <= max_requests <= 256:
        raise ValueError("Choose input and/or holding with a request limit of 1–256")
    if any(
        not 0 <= first <= 65535 or not 1 <= count or first + count > 65536
        for first, count in ranges
    ):
        raise ValueError("Invalid register range")
    results = {kind: {"values": {}, "unavailable": []} for kind in kinds}
    requests = 0
    limit_reached = False

    async def block(kind: str, first: int, count: int) -> None:
        nonlocal requests, limit_reached
        if requests >= max_requests:
            limit_reached = True
            results[kind]["unavailable"].append(
                {"start": first, "count": count, "reason": "request limit"}
            )
            return
        requests += 1
        try:
            words = await reader.read(kind, first, count)
        except ReadError as error:
            if count == 1 or not error.splittable:
                results[kind]["unavailable"].append(
                    {"start": first, "count": count, "reason": str(error)}
                )
            else:
                left = count // 2
                await block(kind, first, left)
                await block(kind, first + left, count - left)
        else:
            results[kind]["values"].update(
                {str(first + index): word for index, word in enumerate(words)}
            )

    for kind in kinds:
        for first, count in ranges:
            for offset in range(0, count, MAX_WORDS):
                await block(kind, first + offset, min(MAX_WORDS, count - offset))
    return {
        "schema": 1,
        "private": True,
        "transport": "direct Modbus TCP",
        "recorded_at": datetime.now(UTC).isoformat(),
        "ranges": [{"start": first, "count": count} for first, count in ranges],
        "functions": {kind: FUNCTIONS[kind] for kind in kinds},
        "requests": requests,
        "request_limit_reached": limit_reached,
        "results": results,
        "notice": (
            "Raw words may contain serial numbers. Keep this file private; "
            "reads do not prove HA Growatt Shine compatibility."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only direct Modbus TCP input and holding register scanner"
    )
    parser.add_argument("--host", required=True, help="Explicit Modbus TCP gateway address")
    parser.add_argument("--port", type=int, default=502)
    parser.add_argument(
        "--unit", type=int, default=1, help="Modbus inverter unit address (default: 1)"
    )
    parser.add_argument(
        "--range",
        dest="ranges",
        type=parse_range,
        action="append",
        required=True,
        metavar="START:COUNT",
    )
    parser.add_argument(
        "--kind",
        choices=tuple(FUNCTIONS),
        action="append",
        dest="kinds",
        help="Default: both input and holding",
    )
    parser.add_argument(
        "--delay", type=float, default=0.5, help="Seconds between requests; minimum 0.5"
    )
    parser.add_argument("--timeout", type=float, default=3.0, help="Seconds per request; 0.5–10")
    parser.add_argument("--max-requests", type=int, default=128)
    parser.add_argument("--output", type=Path, required=True, help="Private JSON report path")
    args = parser.parse_args()
    try:
        reader = ModbusReader(args.host, args.port, args.unit, args.delay, args.timeout)
        result = asyncio.run(
            scan(reader, args.ranges, tuple(args.kinds or FUNCTIONS), args.max_requests)
        )
    except ValueError as error:
        parser.error(str(error))
    try:
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(json.dumps(result, indent=2) + "\n")
    except FileExistsError:
        parser.error("The private report already exists; choose a new output path")
    print(f"Saved private scan: {args.output} ({result['requests']} read requests).")


if __name__ == "__main__":
    main()
