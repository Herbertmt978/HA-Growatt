"""Read existing INI settings and Home Assistant app options."""

from __future__ import annotations

import ast
import configparser
import json
import os
from dataclasses import replace
from pathlib import Path

from .outputs import InfluxSettings, PublicationPolicy, PVOutputSettings, RawMqttSettings
from .publisher import MqttSettings
from .relay import RelaySettings
from .runtime_options import RuntimeOptions
from .selection import SelectionSettings


def _boolean(value: object) -> bool:
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in {
        "true",
        "false",
        "yes",
        "no",
        "1",
        "0",
        "on",
        "off",
    }:
        return value.lower() in {"true", "yes", "1", "on"}
    raise ValueError("A configuration switch must be true or false")


def _integer(value: object) -> int:
    if type(value) is int:
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            pass
    raise ValueError("A numeric configuration option must be an integer")


def _mapping(value: object) -> dict:
    if isinstance(value, dict):
        if all(isinstance(key, str) for key in value):
            return value
        raise ValueError("Configuration mapping keys must be text")
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError, TypeError, RecursionError):
                raise ValueError(
                    "A configuration mapping must be a JSON object or dictionary"
                ) from None
        if isinstance(parsed, dict) and all(isinstance(key, str) for key in parsed):
            return parsed
    raise ValueError("A configuration mapping must be a JSON object or dictionary")


def _selection(get) -> SelectionSettings:
    return SelectionSettings(
        family=get("Generic", "invtype", "ginvtype", "default"),
        strict=_boolean(get("Generic", "layout_strict", "glayoutstrict", False)),
        automatic=_boolean(get("Generic", "layout_auto_family", "glayoutautofamily", True)),
        minimum_score=_integer(get("Generic", "layout_min_score", "glayoutminscore", 20)),
        device_families=_mapping(get("Generic", "invtypemap", "ginvtypemap", "{}")),
    )


def _mqtt(options: dict) -> MqttSettings:
    allowed = {
        "ha_mqtt_host",
        "ha_mqtt_port",
        "ha_mqtt_user",
        "ha_mqtt_password",
        "ha_mqtt_retain",
        "ha_entity_profile",
        "ha_state_path",
    }
    if set(options) - allowed:
        raise ValueError("The Home Assistant extension contains an unsupported option")
    if any(
        not isinstance(options.get(key, ""), str)
        for key in ("ha_mqtt_host", "ha_mqtt_user", "ha_mqtt_password", "ha_state_path")
    ):
        raise ValueError("MQTT host and credentials must be text")
    return MqttSettings(
        host=options.get("ha_mqtt_host", "localhost"),
        port=_integer(options.get("ha_mqtt_port", 1883)),
        username=options.get("ha_mqtt_user", ""),
        password=options.get("ha_mqtt_password", ""),
        retain_state=_boolean(options.get("ha_mqtt_retain", False)),
        entity_profile=options.get("ha_entity_profile", "v0_1_9_standard"),
        state_path=options.get("ha_state_path", ""),
    )


def _addon(path: Path) -> tuple[RelaySettings, MqttSettings, SelectionSettings, RuntimeOptions]:
    options = json.loads(path.read_text(encoding="utf-8"))
    return addon_options(options)


def addon_options(
    options: dict,
) -> tuple[RelaySettings, MqttSettings, SelectionSettings, RuntimeOptions]:
    if not isinstance(options, dict):
        raise ValueError("Home Assistant app options must be an object")
    allowed = {
        "mode",
        "blockcmd",
        "time",
        "sendbuf",
        "invtype",
        "layout_strict",
        "layout_auto_family",
        "diagnostic_logging",
        "ha_plugin",
        "ha_entity_profile",
        "mqtt_host",
        "mqtt_port",
        "mqtt_user",
        "mqtt_password",
        "mqtt_retain",
        "cloud_fallback",
        "cloud_recovery_seconds",
        "settings_refresh_seconds",
        "buffered_events",
        "ha_features",
        "ha_controls",
        "mqtt_auto",
        "mqtt_tls",
        "restore_readings",
        "inverters",
        "dataloggers",
        "experimental_controls",
    }
    if set(options) - allowed:
        raise ValueError("The Home Assistant app contains an unsupported option")
    if options.get("mode", "proxy") != "proxy":
        raise ValueError("The Home Assistant app supports proxy mode")
    ha_enabled = _boolean(options.get("ha_plugin", True))
    mqtt = _mqtt(
        {
            "ha_mqtt_host": options.get("mqtt_host", "core-mosquitto"),
            "ha_mqtt_port": options.get("mqtt_port", 1883),
            "ha_mqtt_user": options.get("mqtt_user", ""),
            "ha_mqtt_password": options.get("mqtt_password", ""),
            "ha_mqtt_retain": options.get("mqtt_retain", False),
            "ha_entity_profile": options.get("ha_entity_profile", "v0_1_9_standard"),
        }
    )
    from dataclasses import replace

    mqtt = replace(mqtt, tls=_boolean(options.get("mqtt_tls", False)))
    inverters = options.get("inverters", [])
    if not isinstance(inverters, list) or len(inverters) > 128:
        raise ValueError("Inverters must be a list of up to 128 profiles")
    from .discovery import validate_identity

    dataloggers = {}
    logger_options = options.get("dataloggers", [])
    if not isinstance(logger_options, list) or len(logger_options) > 128:
        raise ValueError("Dataloggers must be a list of up to 128 devices")
    for item in logger_options:
        if not isinstance(item, dict) or set(item) - {"serial", "model", "firmware"}:
            raise ValueError("Invalid datalogger details")
        identity = item.get("serial", "")
        validate_identity(identity)
        if identity in dataloggers:
            raise ValueError("Each datalogger must have only one entry")
        dataloggers[identity] = {key: item[key] for key in ("model", "firmware") if key in item}
    families = {}
    models = {}
    hardware = {}
    for item in inverters:
        if not isinstance(item, dict) or set(item) - {
            "serial",
            "family",
            "controls",
            "model",
            "firmware",
        }:
            raise ValueError("Invalid inverter profile")
        validate_identity(item.get("serial", ""))
        if item["serial"] in families:
            raise ValueError("Each inverter must have only one profile")
        families[item["serial"]] = item.get("family", "default")
        models[item["serial"]] = item.get("controls", "auto")
        hardware[item["serial"]] = {key: item[key] for key in ("model", "firmware") if key in item}
    return (
        RelaySettings(
            "server.growatt.com",
            listen_host="0.0.0.0",
            block_commands=_boolean(options.get("blockcmd", True)),
            cloud_fallback=_boolean(options.get("cloud_fallback", True)),
            cloud_recovery_seconds=_integer(options.get("cloud_recovery_seconds", 300)),
        ),
        mqtt,
        SelectionSettings(
            family=options.get("invtype", "default"),
            strict=_boolean(options.get("layout_strict", False)),
            automatic=_boolean(options.get("layout_auto_family", True)),
            device_families=families,
        ),
        RuntimeOptions(
            home_assistant=ha_enabled,
            ha_features=_boolean(options.get("ha_features", True)),
            ha_controls=_boolean(options.get("ha_controls", True)),
            experimental_controls=_boolean(options.get("experimental_controls", False)),
            control_models=models,
            hardware=hardware,
            dataloggers=dataloggers,
            settings_refresh_seconds=_integer(options.get("settings_refresh_seconds", 300)),
            buffered_events=_boolean(options.get("buffered_events", True)),
            raw_mqtt=None if ha_enabled else RawMqttSettings(mqtt),
            policy=PublicationPolicy(
                options.get("time", "server"), _boolean(options.get("sendbuf", False))
            ),
            diagnostic_logging=_boolean(options.get("diagnostic_logging", False)),
        ),
    )


def load_legacy_options(
    path: Path,
    environment: dict[str, str] | None = None,
) -> tuple[RelaySettings, MqttSettings, SelectionSettings, RuntimeOptions]:
    if path.suffix.lower() == ".json":
        return _addon(path)
    config = configparser.ConfigParser(interpolation=None)
    try:
        config.read_string(path.read_text(encoding="utf-8"))
    except configparser.Error:
        raise ValueError("The INI configuration could not be read") from None
    if set(config.sections()) - {"Generic", "Growatt", "MQTT", "extension", "PVOutput", "influx"}:
        raise ValueError("The INI configuration contains an unsupported section")
    generic_options = {
        "mode",
        "ip",
        "port",
        "blockcmd",
        "noipf",
        "time",
        "sendbuf",
        "compat",
        "valueoffset",
        "inverterid",
        "invtype",
        "layout_strict",
        "layout_auto_family",
        "layout_min_score",
        "diagnostic_logging",
        "verbose",
        "minrecl",
        "decrypt",
        "includeall",
        "invtypemap",
        "experimental_controls",
        "control_models",
        "hardware",
        "dataloggers",
        "settings_refresh_seconds",
        "buffered_events",
        "cloud_recovery_seconds",
        "timezone",
        "layouts_directory",
        "sniff_interface",
        "api_host",
        "api_port",
        "trace",
        "cloud_fallback",
        "ha_features",
        "ha_controls",
    }
    if config.has_section("Generic") and set(config.options("Generic")) - generic_options:
        raise ValueError("The INI configuration contains an unsupported Generic option")

    def get(section, key, variable, default):
        variables = os.environ if environment is None else environment
        return variables.get(variable, config.get(section, key, fallback=default))

    relay = RelaySettings(
        upstream_host=get("Growatt", "ip", "ggrowattip", "server.growatt.com"),
        upstream_port=_integer(get("Growatt", "port", "ggrowattport", 5279)),
        listen_host=get("Generic", "ip", "ggrottip", "0.0.0.0"),
        listen_port=_integer(get("Generic", "port", "ggrottport", 5279)),
        block_commands=_boolean(get("Generic", "blockcmd", "gblockcmd", False)),
        allow_destination_change=_boolean(get("Generic", "noipf", "gnoipf", False)),
        cloud_fallback=_boolean(
            get("Generic", "cloud_fallback", "HA_GROWATT_CLOUD_FALLBACK", True)
        ),
        cloud_recovery_seconds=_integer(
            get("Generic", "cloud_recovery_seconds", "HA_GROWATT_CLOUD_RECOVERY_SECONDS", 300)
        ),
    )
    whitelist = path.parent / "recwl.txt"
    if not whitelist.is_file():
        whitelist = Path.cwd() / "recwl.txt"
    if whitelist.is_file():
        try:
            records = frozenset(
                int(line.strip(), 16) for line in whitelist.read_text().splitlines() if line.strip()
            )
            relay = replace(relay, permitted_records=records)
        except ValueError:
            raise ValueError("Record whitelist entries must be four hexadecimal digits") from None
    extension_enabled = _boolean(get("extension", "extension", "gextension", False))
    extension_name = get("extension", "extname", "gextname", "grottext")
    extension_options = _mapping(get("extension", "extvar", "gextvar", "{}"))
    ha = extension_enabled and extension_name in {"grottext.ha", "grott_ha"}
    mqtt = _mqtt(extension_options) if ha else MqttSettings("localhost")
    mqtt = replace(mqtt, include_all=_boolean(get("Generic", "includeall", "gincludeall", False)))
    raw = None
    variables = os.environ if environment is None else environment
    if not _boolean(get("MQTT", "nomqtt", "gnomqtt", False)):
        auth = _boolean(get("MQTT", "auth", "gmqttauth", False))
        raw = RawMqttSettings(
            MqttSettings(
                host=get("MQTT", "ip", "gmqttip", "localhost"),
                port=_integer(get("MQTT", "port", "gmqttport", 1883)),
                username=get("MQTT", "user", "gmqttuser", "grott") if auth else "",
                password=get("MQTT", "password", "gmqttpassword", "growatt2020") if auth else "",
                retain_state=_boolean(get("MQTT", "retain", "gmqttretain", False)),
                client_id=get("Generic", "inverterid", "ginverterid", "automatic"),
            ),
            topic=get("MQTT", "topic", "gmqtttopic", "energy/growatt"),
            # Existing environment overrides use non-empty text as enabled,
            # including the literal "False". Keep established topic identities.
            inverter_in_topic=bool(variables["gmqttinverterintopic"])
            if variables.get("gmqttinverterintopic")
            else _boolean(config.get("MQTT", "inverterintopic", fallback=False)),
            meter_topic=get("MQTT", "mtopicname", "gmqttmtopicname", "energy/meter")
            if _boolean(get("MQTT", "mtopic", "gmqttmtopic", False))
            else None,
        )
    pv = None
    if _boolean(get("PVOutput", "pvoutput", "gpvoutput", False)):
        count = _integer(get("PVOutput", "pvinverters", "gpvinverters", 1))
        systems = {}
        default = None
        if count == 1:
            default = get("PVOutput", "systemid", "gpvsystemid", "systemid1")
        else:
            for number in range(1, count + 1):
                identity = get("PVOutput", f"inverterid{number}", f"gpvinverterid{number}", "")
                systems[identity] = get("PVOutput", f"systemid{number}", f"gpvsystemid{number}", "")
        pv = PVOutputSettings(
            get("PVOutput", "apikey", "gpvapikey", ""),
            systems,
            default,
            _integer(get("PVOutput", "pvuplimit", "pvuplimit", 5)),
            _boolean(get("PVOutput", "pvtemp", "gpvtemp", False)),
            _boolean(get("PVOutput", "pvdisv1", "gpvdisv1", False)),
        )
    influx = None
    if _boolean(get("influx", "influx", "ginflux", False)):
        version = 2 if _boolean(get("influx", "influx2", "ginflux2", False)) else 1
        host = get("influx", "ip", "gifip", "localhost")
        if "://" not in host:
            host = f"http://{host}:{_integer(get('influx', 'port', 'gifport', 8086))}"
        influx = InfluxSettings(
            host,
            version,
            get("influx", "dbname", "gifdbname", "grottdb"),
            get("influx", "user", "gifuser", "grott"),
            get("influx", "password", "gifpassword", "growatt2020"),
            get("influx", "token", "giftoken", ""),
            get("influx", "org", "giforg", "grottorg"),
            get("influx", "bucket", "gifbucket", "grottdb"),
        )
    runtime = RuntimeOptions(
        mode=get("Generic", "mode", "gmode", "proxy"),
        home_assistant=ha,
        ha_features=_boolean(get("Generic", "ha_features", "HA_GROWATT_HA_FEATURES", True)),
        ha_controls=_boolean(get("Generic", "ha_controls", "HA_GROWATT_HA_CONTROLS", True)),
        experimental_controls=_boolean(
            get("Generic", "experimental_controls", "HA_GROWATT_EXPERIMENTAL_CONTROLS", False)
        ),
        control_models=_mapping(
            get("Generic", "control_models", "HA_GROWATT_CONTROL_MODELS", "{}")
        ),
        hardware=_mapping(get("Generic", "hardware", "HA_GROWATT_HARDWARE", "{}")),
        dataloggers=_mapping(get("Generic", "dataloggers", "HA_GROWATT_DATALOGGERS", "{}")),
        settings_refresh_seconds=_integer(
            get("Generic", "settings_refresh_seconds", "HA_GROWATT_SETTINGS_REFRESH_SECONDS", 300)
        ),
        buffered_events=_boolean(
            get("Generic", "buffered_events", "HA_GROWATT_BUFFERED_EVENTS", True)
        ),
        policy=PublicationPolicy(
            get("Generic", "time", "gtime", "auto"),
            _boolean(get("Generic", "sendbuf", "gsendbuf", True)),
            get("Generic", "timezone", "gtimezone", "local"),
        ),
        raw_mqtt=raw,
        pvoutput=pv,
        influx=influx,
        extension=extension_name if extension_enabled and not ha else None,
        extension_options=extension_options,
        minimum_record_bytes=_integer(get("Generic", "minrecl", "gminrecl", 100)),
        layouts_directory=Path(
            get("Generic", "layouts_directory", "HA_GROWATT_LAYOUTS", str(path.parent))
        ),
        sniff_interface=get("Generic", "sniff_interface", "HA_GROWATT_INTERFACE", None),
        diagnostic_logging=_boolean(
            get("Generic", "diagnostic_logging", "gdiagnosticlogging", False)
        ),
        verbose=_boolean(get("Generic", "verbose", "gverbose", False)),
        trace=_boolean(get("Generic", "trace", "gtrace", False)),
        api_host=get("Generic", "api_host", "HA_GROWATT_API_HOST", "127.0.0.1"),
        api_port=_integer(get("Generic", "api_port", "HA_GROWATT_API_PORT", 5782)),
        compatibility=_boolean(get("Generic", "compat", "gcompat", False)),
        inverter_identity=get("Generic", "inverterid", "ginverterid", "automatic"),
        value_offset=_integer(get("Generic", "valueoffset", "gvalueoffset", 6)),
        decrypt=_boolean(get("Generic", "decrypt", "gdecrypt", True)),
    )
    if runtime.compatibility:
        from .compat import CompatibilityDecoder

        CompatibilityDecoder(runtime.inverter_identity, runtime.value_offset, runtime.decrypt)
    runtime = replace(
        runtime,
        extension_context={
            "verbose": runtime.verbose,
            "trace": runtime.trace,
            "mode": runtime.mode,
            "gtime": runtime.policy.time_source,
            "sendbuf": runtime.policy.send_buffered,
            "tmzone": runtime.policy.timezone,
            "inverterid": runtime.inverter_identity,
            "invtype": _selection(get).family,
            "includeall": mqtt.include_all,
            "grottip": relay.listen_host,
            "grottport": relay.listen_port,
            "growattip": relay.upstream_host,
            "growattport": relay.upstream_port,
            "nomqtt": raw is None,
            "blockcmd": relay.block_commands,
            "noipf": relay.allow_destination_change,
            "minrecl": runtime.minimum_record_bytes,
            "compat": runtime.compatibility,
            "offset": runtime.value_offset,
            "decrypt": runtime.decrypt,
        },
    )
    return relay, mqtt, _selection(get), runtime
