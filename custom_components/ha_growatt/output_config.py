"""Translate Home Assistant options into isolated optional destinations."""

from ha_growatt.native_outputs import NativeOutputSettings
from ha_growatt.outputs import InfluxSettings, PVOutputSettings, RawMqttSettings
from ha_growatt.publisher import MqttSettings


def destination_secret(submitted, saved, previous, selected):
    """Never carry a saved credential to a different destination."""
    return submitted or (saved if previous == selected else "")


def native_output_settings(options):
    raw_mqtt = None
    if host := options.get("output_mqtt_host", "").strip():
        topic = options.get("output_mqtt_topic", "energy/growatt").strip()
        if not topic or "#" in topic or "+" in topic:
            raise ValueError("Choose a raw MQTT topic without wildcards")
        raw_mqtt = RawMqttSettings(
            broker=MqttSettings(
                host=host,
                port=options.get("output_mqtt_port", 1883),
                username=options.get("output_mqtt_username", ""),
                password=options.get("output_mqtt_password", ""),
                tls=options.get("output_mqtt_tls", False),
            ),
            topic=topic,
        )
    pvoutput = None
    if system := options.get("output_pvoutput_system", "").strip():
        pvoutput = PVOutputSettings(
            api_key=options.get("output_pvoutput_api_key", ""), default_system=system
        )
    influx = None
    if endpoint := options.get("output_influx_endpoint", "").strip():
        version = options.get("output_influx_version", 2)
        if version == 2 and not all(
            options.get(f"output_influx_{part}", "").strip()
            for part in ("organisation", "bucket", "token")
        ):
            raise ValueError("InfluxDB 2 needs organisation, bucket and token")
        if version == 1 and not options.get("output_influx_database", "").strip():
            raise ValueError("InfluxDB 1 needs a database")
        influx = InfluxSettings(
            endpoint=endpoint,
            version=version,
            database=options.get("output_influx_database", "grottdb"),
            organisation=options.get("output_influx_organisation", ""),
            bucket=options.get("output_influx_bucket", ""),
            username=options.get("output_influx_username", ""),
            password=options.get("output_influx_password", ""),
            token=options.get("output_influx_token", ""),
        )
    return NativeOutputSettings(
        raw_mqtt=raw_mqtt,
        pvoutput=pvoutput,
        influx=influx,
        http_endpoint=options.get("output_http_endpoint", "").strip() or None,
    )
