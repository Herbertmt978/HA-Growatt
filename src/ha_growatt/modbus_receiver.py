"""Read documented Growatt input registers from an explicit Modbus TCP gateway.

The register numbers and scales follow published V1.24, V1.39 and V3.14
input tables. The read-only scanner supplies TCP framing and request limits.
Automatic selection accepts only supported device-type codes; other devices
need an explicit profile.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from datetime import UTC, datetime

from .discovery import STANDARD_SENSORS, validate_identity
from .modbus_scan import ModbusReader, ReadError
from .native_receiver import NativeReading
from .recovery import ReadingStore, Snapshot
from .telemetry import Telemetry

_LOG = logging.getLogger(__name__)

# These blocks contain documented PV, grid and generation readings. Each
# request is at most 32 words. Battery and meter readings depend on model and
# wiring, so they are not inferred from a matching base register range.
PROFILES: dict[str, tuple[tuple[int, int], ...]] = {
    "min-3000-v124": ((3000, 30), (3047, 32)),
    "min-three-string-v124": ((3000, 30), (3047, 32)),
    "tl3-three-phase-v139": ((3000, 30), (3030, 11), (3047, 32)),
    "mic-0-v314": ((0, 32), (32, 26)),
    "legacy-0-v124": ((0, 11), (35, 32)),
}
OPTIONAL_BLOCKS = {
    "min-three-string-v124": ((3086, 23),),
    "tl3-three-phase-v139": ((3086, 23),),
}
AUTO_PROFILE = "auto"
INVESTIGATION_PROFILE = "investigate-raw"
PROFILE_CHOICES = (AUTO_PROFILE, *PROFILES, INVESTIGATION_PROFILE)

_SENSOR_KEYS = frozenset(
    {
        "pvstatus",
        "pvpowerin",
        "pv1voltage",
        "pv1current",
        "pv1watt",
        "pv2voltage",
        "pv2current",
        "pv2watt",
        "pvpowerout",
        "pvfrequentie",
        "pvgridvoltage",
        "pvgridcurrent",
        "pvgridvoltage2",
        "pvgridcurrent2",
        "pvgridvoltage3",
        "pvgridcurrent3",
        "pvenergytoday",
        "pvenergytotal",
        "epvtotal",
        "epv1today",
        "epv1total",
        "epv2today",
        "epv2total",
        "totworktime",
        "pvtemperature",
        "pvipmtemperature",
    }
)


def _sensor_metadata(values: dict[str, int]) -> dict[str, dict]:
    """Use the native entity names and units, limited to fields actually read."""
    result = {}
    for sensor in STANDARD_SENSORS:
        if sensor.key in _SENSOR_KEYS and sensor.key in values:
            definition = asdict(sensor)
            definition.pop("key")
            definition["source"] = sensor.key
            result[sensor.key] = definition
    for suffix, label, unit, device_class in (
        ("voltage", "Voltage", "V", "voltage"),
        ("current", "Current", "A", "current"),
        ("watt", "Power", "W", "power"),
    ):
        key = f"pv3{suffix}"
        if key in values:
            result[key] = {
                "label": f"PV3 {label}",
                "source": key,
                "divisor": 10,
                "unit": unit,
                "device_class": device_class,
                "state_class": "measurement",
            }
    for key, label in (
        ("epv3today", "Solar PV3 production"),
        ("epv3total", "Solar PV3 production (Total)"),
    ):
        if key in values:
            result[key] = {
                "label": label,
                "source": key,
                "divisor": 10,
                "unit": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing" if key == "epv3total" else "total",
            }
    if "pvboosttemperature" in values:
        result["pvboosttemperature"] = {
            "label": "Boost temperature",
            "source": "pvboosttemperature",
            "divisor": 10,
            "unit": "°C",
            "device_class": "temperature",
            "state_class": "measurement",
        }
    for key, label in (
        ("pvfaultcode", "Fault code"),
        ("pvwarningcode", "Warning code"),
        ("pvderatingmode", "Derating mode code"),
    ):
        if key in values:
            result[key] = {
                "label": label,
                "source": key,
                "entity_category": "diagnostic",
                "icon": "mdi:alert-circle-outline",
            }
    for index in (1, 2, 3):
        key = "pvgridapparentpower" + (str(index) if index > 1 else "")
        if key in values:
            result[key] = {
                "label": f"Phase {index} apparent power",
                "source": key,
                "divisor": 10,
                "unit": "VA",
                "state_class": "measurement",
            }
    for pair in ("rs", "st", "tr"):
        key = f"pvgridlinevoltage_{pair}"
        if key in values:
            result[key] = {
                "label": f"Line voltage {pair.upper()}",
                "source": key,
                "divisor": 10,
                "unit": "V",
                "device_class": "voltage",
                "state_class": "measurement",
            }
    return result


def _raw_sensor_metadata(values: dict[str, int]) -> dict[str, dict]:
    return {
        key: {
            "label": f"{key.split('_')[1].title()} register {key.rsplit('_', 1)[1]}",
            "source": key,
            "entity_category": "diagnostic",
            "icon": "mdi:database-search",
        }
        for key in values
    }


def _u32(words: dict[int, int], high: int) -> int:
    return (words[high] << 16) | words[high + 1]


async def detect_input_profile(reader: ModbusReader) -> str:
    """Identify only DTC families for which this receiver has a matching map.

    Both DTC locations and the input probe are read-only. A register replying
    with zero still counts as present; sleeping inverters may report zeros.
    Shared or unfamiliar DTCs are left for explicit profile selection.
    """
    dtc = None
    for address in (30000, 43):
        try:
            dtc = (await reader.read("holding", address, 1))[0]
        except ReadError as error:
            if error.splittable:
                continue  # This address is absent; try the legacy DTC address.
            raise
        if dtc:
            break
    if dtc not in {5100, 5200, 5201}:
        raise ValueError("Automatic Modbus identification needs a supported device type code")
    try:
        await reader.read("input", 3003, 1)
        has_3000_input = True
    except ReadError as error:
        if not error.splittable:
            raise  # A timeout must not be mistaken for a different inverter.
        has_3000_input = False
    if dtc == 5200 and not has_3000_input:
        return "mic-0-v314"
    if not has_3000_input:
        raise ValueError("Automatic Modbus identification found no matching input range")
    return "min-three-string-v124" if dtc == 5201 else "min-3000-v124"


def decode_input_registers(profile: str, words: dict[int, int]) -> dict[str, int]:
    """Return raw integer values with the existing HA sensor scaling."""
    if profile not in PROFILES:
        raise ValueError("Unknown Modbus input profile")
    if profile in {"min-3000-v124", "min-three-string-v124", "tl3-three-phase-v139"}:
        values = {
            "pvstatus": words[3000] & 0xFF,
            "pvpowerin": _u32(words, 3001),
            "pv1voltage": words[3003],
            "pv1current": words[3004],
            "pv1watt": _u32(words, 3005),
            "pv2voltage": words[3007],
            "pv2current": words[3008],
            "pv2watt": _u32(words, 3009),
            "pvpowerout": _u32(words, 3023),
            "pvfrequentie": words[3025],
            "pvgridvoltage": words[3026],
            "pvgridcurrent": words[3027],
            "totworktime": _u32(words, 3047),
            "pvenergytoday": _u32(words, 3049),
            "pvenergytotal": _u32(words, 3051),
            "epvtotal": _u32(words, 3053),
            "epv1today": _u32(words, 3055),
            "epv1total": _u32(words, 3057),
            "epv2today": _u32(words, 3059),
            "epv2total": _u32(words, 3061),
        }
        if profile in {"min-three-string-v124", "tl3-three-phase-v139"}:
            values.update(
                pv3voltage=words[3011],
                pv3current=words[3012],
                pv3watt=_u32(words, 3013),
                epv3today=_u32(words, 3063),
                epv3total=_u32(words, 3065),
            )
            if profile == "tl3-three-phase-v139":
                values.update(
                    pvgridapparentpower=_u32(words, 3028),
                    pvgridvoltage2=words[3030],
                    pvgridcurrent2=words[3031],
                    pvgridapparentpower2=_u32(words, 3032),
                    pvgridvoltage3=words[3034],
                    pvgridcurrent3=words[3035],
                    pvgridapparentpower3=_u32(words, 3036),
                    pvgridlinevoltage_rs=words[3038],
                    pvgridlinevoltage_st=words[3039],
                    pvgridlinevoltage_tr=words[3040],
                )
            if 3086 in words:
                values.update(
                    pvderatingmode=words[3086],
                    pvtemperature=words[3093],
                    pvipmtemperature=words[3094],
                    pvboosttemperature=words[3095],
                    pvfaultcode=words[3105],
                    pvwarningcode=words[3106],
                )
    elif profile == "mic-0-v314":
        values = {
            "pvstatus": words[0],
            "pvpowerin": _u32(words, 1),
            "pv1voltage": words[3],
            "pv1current": words[4],
            "pv1watt": _u32(words, 5),
            "pvpowerout": _u32(words, 11),
            "pvfrequentie": words[13],
            "pvgridvoltage": words[14],
            "pvgridcurrent": words[15],
            "pvenergytoday": _u32(words, 26),
            "pvenergytotal": _u32(words, 28),
            "totworktime": _u32(words, 30),
            "pvtemperature": words[32],
            "pvipmtemperature": words[41],
            "epv1today": _u32(words, 48),
            "epv1total": _u32(words, 50),
            "epvtotal": _u32(words, 56),
            "pvfaultcode": words[40],
            "pvderatingmode": words[47],
        }
    else:
        values = {
            "pvstatus": words[0],
            "pvpowerin": _u32(words, 1),
            "pv1voltage": words[3],
            "pv1current": words[4],
            "pv1watt": _u32(words, 5),
            "pv2voltage": words[7],
            "pv2current": words[8],
            "pv2watt": _u32(words, 9),
            "pvpowerout": _u32(words, 35),
            "pvfrequentie": words[37],
            "pvgridvoltage": words[38],
            "pvgridcurrent": words[39],
            "pvenergytoday": _u32(words, 53),
            "pvenergytotal": _u32(words, 55),
            "totworktime": _u32(words, 57),
            "epv1today": _u32(words, 59),
            "epv1total": _u32(words, 61),
            "epv2today": _u32(words, 63),
            "epv2total": _u32(words, 65),
        }
    # Protocol II V1.39 also uses display state 2 for off-grid operation.
    valid_status = {0, 1, 3, 4}
    if profile != "mic-0-v314":
        valid_status.add(2)
    if values["pvstatus"] not in valid_status:
        raise ValueError("The selected Modbus input layout does not match the reply")
    if values["pvpowerin"] > 100_000_000 or values["pvpowerout"] > 100_000_000:
        raise ValueError("The selected Modbus input layout produced an invalid power value")
    if not any(values[key] for key in ("pvpowerin", "pvpowerout", "pv1voltage", "pvenergytotal")):
        raise ValueError("The selected Modbus input layout returned no identifying readings")
    if values["pvenergytoday"] > values["pvenergytotal"]:
        raise ValueError("The selected Modbus input layout has inconsistent energy readings")
    return values


def _safe_error(error: ReadError | KeyError | ValueError) -> str:
    """Keep diagnostics useful without exposing gateway or register contents."""
    if isinstance(error, ReadError):
        if str(error) == "TimeoutError":
            return "Modbus gateway timed out"
        if str(error).startswith("Modbus exception"):
            return "Modbus gateway rejected a register block"
        return "Modbus gateway could not be reached or returned an invalid reply"
    if isinstance(error, KeyError):
        return "The selected Modbus profile did not match the reply"
    if "lifetime energy" in str(error):
        return "The inverter's lifetime energy reading went backwards"
    if "Automatic Modbus identification" in str(error):
        return "Automatic identification was inconclusive; choose a documented profile"
    return "The selected Modbus profile did not match the reply"


class ModbusReceiver:
    """Poll a single named inverter without taking over its Shine connection."""

    def __init__(
        self,
        *,
        host: str,
        identity: str,
        profile: str,
        port: int = 502,
        unit: int = 1,
        interval: float = 60.0,
        timeout: float = 3.0,
        delay: float = 1.0,
        block_words: int = 32,
        state_path: str = "",
        investigation_kind: str = "input",
        investigation_start: int = 0,
        investigation_count: int = 32,
    ) -> None:
        if profile not in PROFILE_CHOICES:
            raise ValueError("Choose a documented Modbus input profile")
        if (
            investigation_kind not in {"input", "holding"}
            or type(investigation_start) is not int
            or type(investigation_count) is not int
            or not 0 <= investigation_start <= 65535
            or not 1 <= investigation_count <= 32
            or investigation_start + investigation_count > 65536
        ):
            raise ValueError("Choose one read-only block of 1–32 registers")
        validate_identity(identity)
        if (
            not isinstance(host, str)
            or not host
            or len(host) > 253
            or any(character.isspace() or character in "/\\@" for character in host)
        ):
            raise ValueError("Enter a gateway hostname or IP address")
        if type(interval) not in {int, float} or not 30 <= interval <= 3600:
            raise ValueError("Poll interval must be between 30 and 3600 seconds")
        if type(block_words) is not int or not 4 <= block_words <= 32:
            raise ValueError("Modbus requests must contain 4–32 words at most")
        self.host, self.port, self.unit = host, port, unit
        self.identity, self.profile = identity, profile
        self.detected_profile: str | None = None
        self.investigation_kind = investigation_kind
        self.investigation_start = investigation_start
        self.investigation_count = investigation_count
        self.interval = float(interval)
        self.block_words = block_words
        self.reader = ModbusReader(host, port, unit, delay, timeout)
        self.snapshots: dict[str, Snapshot] = {}
        self.measurements = 0
        self.failed_measurements = 0
        self.connected = False
        self.last_error: str | None = None
        self.cache_error = False
        self._listeners: set = set()
        self._restored_ids: set[str] = set()
        self._store = (
            ReadingStore(state_path, ("modbus", host, port, unit, identity, profile))
            if state_path and profile != INVESTIGATION_PROFILE
            else None
        )
        self._poll_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def subscribe(self, listener):
        self._listeners.add(listener)
        for identity, snapshot in self.snapshots.items():
            listener(NativeReading(identity, snapshot, restored=identity in self._restored_ids))
        return lambda: self._listeners.discard(listener)

    async def start(self) -> None:
        if self.running:
            raise RuntimeError("The Modbus receiver is already started")
        if self._store is not None:
            try:
                self.snapshots = await asyncio.to_thread(self._store.load)
                self._restored_ids = set(self.snapshots)
            except (OSError, ValueError, KeyError, TypeError):
                self.cache_error = True
                _LOG.warning("Saved Modbus readings could not be restored")
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="ha-growatt-modbus-poll")

    async def poll_once(self) -> bool:
        """Read bounded blocks; a failed block never publishes partial data."""
        async with self._poll_lock:
            try:
                active_profile = self.profile
                if self.profile == INVESTIGATION_PROFILE:
                    words = await self._read_block(
                        self.investigation_kind,
                        self.investigation_start,
                        self.investigation_count,
                    )
                    values = {
                        f"raw_{self.investigation_kind}_{address}": value
                        for address, value in words.items()
                    }
                    metadata = _raw_sensor_metadata(values)
                else:
                    if active_profile == AUTO_PROFILE:
                        active_profile = self.detected_profile or await detect_input_profile(
                            self.reader
                        )
                    words: dict[int, int] = {}
                    for first, count in PROFILES[active_profile]:
                        words.update(await self._read_block("input", first, count))
                    for first, count in OPTIONAL_BLOCKS.get(active_profile, ()):
                        try:
                            optional_words = await self._read_block("input", first, count)
                        except ReadError:
                            continue
                        words.update(optional_words)
                    values = decode_input_registers(active_profile, words)
                    previous = self.snapshots.get(self.identity)
                    if (
                        previous is not None
                        and previous.telemetry.profile == f"modbus-{active_profile}"
                    ):
                        last_total = previous.telemetry.values.get("pvenergytotal")
                        if type(last_total) is int and values["pvenergytotal"] < last_total:
                            raise ValueError(
                                "The inverter's lifetime energy reading went backwards"
                            )
                    metadata = _sensor_metadata(values)
                    if self.profile == AUTO_PROFILE:
                        self.detected_profile = active_profile
            except (ReadError, KeyError, ValueError) as error:
                self.failed_measurements += 1
                self.connected = False
                self.last_error = _safe_error(error)
                return False
            telemetry = Telemetry(
                values=values,
                recorded_at=None,
                profile=f"modbus-{active_profile}",
                sensor_metadata=metadata,
                device_id=self.identity,
            )
            snapshot = Snapshot(telemetry, datetime.now(UTC))
            self.snapshots[self.identity] = snapshot
            self._restored_ids.discard(self.identity)
            self.measurements += 1
            self.connected = True
            self.last_error = None
            if self._store is not None:
                try:
                    await asyncio.to_thread(self._store.save, dict(self.snapshots))
                    self.cache_error = False
                except (OSError, ValueError):
                    self.cache_error = True
                    _LOG.warning(
                        "Modbus readings are live, but restart recovery could not be saved"
                    )
            reading = NativeReading(self.identity, snapshot)
            for listener in tuple(self._listeners):
                try:
                    listener(reading)
                except Exception:
                    _LOG.warning("A Modbus reading listener failed")
            return True

    async def _read_block(self, kind: str, first: int, count: int) -> dict[int, int]:
        words = {}
        for offset in range(0, count, self.block_words):
            size = min(self.block_words, count - offset)
            values = await self.reader.read(kind, first + offset, size)
            words.update((first + offset + index, value) for index, value in enumerate(values))
        return words

    async def _run(self) -> None:
        while not self._stop.is_set():
            await self.poll_once()
            try:
                await asyncio.wait_for(self._stop.wait(), self.interval)
            except TimeoutError:
                pass

    async def close(self) -> None:
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self.connected = False
        self._listeners.clear()
