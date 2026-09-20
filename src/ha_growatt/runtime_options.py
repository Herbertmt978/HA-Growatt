"""Settings shared by the command line, transports and output workers."""

from dataclasses import dataclass, field
from pathlib import Path

from .outputs import InfluxSettings, PublicationPolicy, PVOutputSettings, RawMqttSettings


@dataclass(frozen=True, slots=True)
class RuntimeOptions:
    mode: str = "proxy"
    home_assistant: bool = True
    ha_features: bool = True
    ha_controls: bool = True
    experimental_controls: bool = False
    control_models: dict[str, str] = field(default_factory=dict)
    hardware: dict[str, dict[str, str]] = field(default_factory=dict)
    settings_refresh_seconds: int = 300
    buffered_events: bool = True
    policy: PublicationPolicy = PublicationPolicy("server", False)
    raw_mqtt: RawMqttSettings | None = None
    pvoutput: PVOutputSettings | None = None
    influx: InfluxSettings | None = None
    extension: str | None = None
    extension_options: dict = field(default_factory=dict, repr=False)
    extension_context: dict = field(default_factory=dict, repr=False)
    minimum_record_bytes: int = 100
    layouts_directory: Path | None = None
    sniff_interface: str | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 5782
    diagnostic_logging: bool = False
    verbose: bool = False
    trace: bool = False
    compatibility: bool = False
    inverter_identity: str = "automatic"
    value_offset: int = 6
    decrypt: bool = True

    def __post_init__(self) -> None:
        if type(self.settings_refresh_seconds) is not int or not (
            self.settings_refresh_seconds == 0 or 30 <= self.settings_refresh_seconds <= 86400
        ):
            raise ValueError("Settings refresh must be zero or between 30 and 86400 seconds")
        if type(self.buffered_events) is not bool:
            raise ValueError("Buffered events must be a boolean")
        if not isinstance(self.hardware, dict) or len(self.hardware) > 128:
            raise ValueError("Hardware details must be an inverter mapping")
        from .discovery import validate_identity

        for identity, details in self.hardware.items():
            validate_identity(identity)
            if not isinstance(details, dict) or set(details) - {"model", "firmware"}:
                raise ValueError("Hardware details accept model and firmware")
            if any(
                not isinstance(value, str)
                or len(value) > 80
                or any(not c.isprintable() for c in value)
                for value in details.values()
            ):
                raise ValueError("Model and firmware must be printable text of up to 80 characters")
        if not isinstance(self.control_models, dict):
            raise ValueError("Control profiles must be an inverter-to-model mapping")
        from .discovery import validate_identity

        for identity in self.control_models:
            validate_identity(identity)
        if type(self.experimental_controls) is not bool or any(
            value not in {"auto", "sph", "spa", "min_tl_xh", "mod_tl3_xh"}
            for value in self.control_models.values()
        ):
            raise ValueError("Invalid experimental control profile")
        if type(self.ha_features) is not bool or type(self.ha_controls) is not bool:
            raise ValueError("Home Assistant feature switches must be booleans")
        if self.mode not in {"proxy", "sniff", "server"}:
            raise ValueError("Mode must be proxy, sniff or server")
        if type(self.minimum_record_bytes) is not int or self.minimum_record_bytes < 8:
            raise ValueError("Minimum record length must be at least eight bytes")
        if type(self.api_port) is not int or not 0 <= self.api_port <= 65535:
            raise ValueError("HTTP API port is out of range")
