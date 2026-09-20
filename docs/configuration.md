# Configuration

Cloud fallback and additional Home Assistant status/control options are described
in the [feature guide](home-assistant-features.md). They are available in proxy
mode and preserve the existing telemetry interfaces.

`ha-growatt run --config PATH` reads an INI file, a Home Assistant `options.json`
file or a TOML file. INI settings cover all service and output modes. The TOML
format covers the Home Assistant service; use INI for the optional destinations.

## Existing INI files

Keep an existing configuration for migration. The existing `g*` environment
variables override INI values. For example, `ginvtype=default` overrides
`invtype=sph` in the file. `gextvar` replaces the whole extension mapping.
Both JSON objects and ordinary Python dictionary literals are accepted for
mappings; expressions are never executed.

| Section | Settings |
| --- | --- |
| `Generic` | `mode`, `ip`, `port`, `blockcmd`, `noipf`, `time`, `sendbuf`, `timezone`, `invtype`, `invtypemap`, `layout_strict`, `layout_auto_family`, `layout_min_score`, `includeall`, `minrecl`, `verbose`, `diagnostic_logging`, `compat`, `inverterid`, `valueoffset`, `decrypt` |
| `Growatt` | Upstream `ip` and `port` |
| `MQTT` | `nomqtt`, `ip`, `port`, `topic`, `auth`, `user`, `password`, `retain`, `inverterintopic`, `mtopic`, `mtopicname` |
| `PVOutput` | `pvoutput`, `apikey`, `systemid`, `pvinverters`, numbered `systemid`/`inverterid` mappings, `pvuplimit`, `pvtemp`, `pvdisv1` |
| `influx` | `influx`, `influx2`, `ip`, `port`, `dbname`, `user`, `password`, `token`, `org`, `bucket` |
| `extension` | `extension`, `extname`, `extvar` |

The new `Generic` options `layouts_directory`, `sniff_interface`, `api_host`
and `api_port` select local layout files, a capture interface and the server's
HTTP listener. The corresponding overrides are `HA_GROWATT_LAYOUTS`,
`HA_GROWATT_INTERFACE`, `HA_GROWATT_API_HOST` and `HA_GROWATT_API_PORT`.

`time=auto` uses a valid inverter timestamp and falls back to the service clock.
`time=server` uses the service clock for live records. Buffered records retain
their recorded timestamps and are omitted if that timestamp is invalid or
`sendbuf=False`. Home Assistant discovery continues to skip buffered readings.
`timezone` controls the conversion of timestamps for InfluxDB; it accepts
`local` or an IANA name such as `Europe/London`.

Set `invtypemap` to a mapping such as `{"INVERT0001":"sph"}` to choose a family
for a particular inverter. Custom `t*.json` layouts can sit beside the INI file
or in `layouts_directory`. They retain the existing byte-offset, text, signed
number, CSV-column, divisor and include/exclude conventions. A `recwl.txt` file
beside the INI file, or in the working directory, replaces the default record
allowlist. Put one four-digit hexadecimal record type on each line.

## Home Assistant

Use `extension=True`, `extname=grottext.ha` and the existing `ha_mqtt_*` options
inside `extvar`. This selects the new built-in Home Assistant publisher; the
former extension module is not needed. `grott_ha` remains an accepted alias.
`nomqtt=True` disables the separate raw publisher.

`ha_entity_profile=v0_1_9_standard` keeps the established standard profile.
`ha_entity_profile=all` exposes all normally included fields. Add
`includeall=True` to include the layout's normally excluded fields too.
Discovery changes leave the complete state JSON intact.

The app uses the same options as the previous app. With `ha_plugin=True`, it
publishes discovery and state. With `ha_plugin=False`, it publishes raw MQTT.
The app is for proxy mode; use the standalone container for server or sniffer
mode. Passwords in INI and app options stay out of diagnostic representations.
TOML reads its broker password from `HA_GROWATT_MQTT_PASSWORD`.

## Raw MQTT and other outputs

Raw MQTT defaults to `energy/growatt`. `inverterintopic=True` appends the device
identity. A separate meter topic, selected with `mtopic=True`, uses `mtopicname`
without that suffix. The JSON contains `device`, `time`, `buffered` and `values`.

For compatibility, a non-empty legacy `gmqttinverterintopic` environment value
enables the suffix, including the text `False`. To disable it, remove that
override and use `inverterintopic=False` in the INI file.

PVOutput supports one system or numbered per-inverter systems, upload intervals,
optional temperature and omission of the daily-energy value. Binary meter
records produce separate energy and power submissions. An InfluxDB 1 database
is created if it does not exist; InfluxDB 2 uses the configured bucket, organisation
and token. Each output has its own bounded queue. An unavailable destination
does not stop forwarding or the other outputs.

For CSV files, use `extname=grotcsv` (or `ha_growatt.csv`) and an `extvar` object
containing `outpath`. Optional `csvheader` supplies comma-separated field names.
Files use `YEAR-minute/YYYYMMDD.csv`; numeric values use the selected layout's
divisors. Mount that directory writable when using a read-only container.

For HTTP delivery, use `extname=grottext` (or `ha_growatt.http`) and `extvar`
with `url`, or `ip` and `port`. The body preserves the existing extension's
format: a JSON string containing the telemetry JSON. Requests have a deadline
and do not follow redirects.

Other installed Python modules can provide `grottext(conf, data, jsonmsg)`.
`data` is the decrypted hexadecimal record and `jsonmsg` is the telemetry JSON.
The context includes `extvar`, the current `layout` and its field divisors,
plus the common transport and publication settings. The module runs in a
separate process that retains its state between calls. An unresponsive callback
is stopped after its delivery deadline. Install any extension dependencies
alongside HA Growatt and put the module on Python's import path.

## Standalone server

```sh
ha-growatt server --config ha-growatt.ini
```

This command listens for dataloggers on port 5781 and serves the register API
on loopback port 5782. Use `--port`, `--api-host` and `--api-port` to change them.
Alternatively set `mode=server` and the desired `Generic.port` for `run`.

`GET /datalogger` and `GET /inverter` list connected devices. The established
register URLs are retained:

```text
GET /inverter?inverter=INVERT0001&command=register&register=31&format=dec
GET /datalogger?datalogger=LOGGER0001&command=register&register=31
GET /inverter?command=regall
PUT /inverter?inverter=INVERT0001&command=register&register=31&value=123
PUT /inverter?inverter=INVERT0001&command=multiregister&startregister=10&endregister=12&value=000100020003
PUT /datalogger?datalogger=LOGGER0001&command=datetime
```

Inverter values accept `format=dec`, `hex` or `text`. Multi-register writes use
four hexadecimal digits per register. Register access is local device control;
only expose the unauthenticated API on a trusted management interface.

## Sniffer and diagnostics

Set `mode=sniff` and optionally `sniff_interface=eth0`. Linux packet sockets
need `CAP_NET_RAW`, and the interface must receive the datalogger's traffic.
Capture is passive: HA Growatt sends no device or cloud packets in this mode.
`Growatt.ip` and `Growatt.port` select the traffic to decode.

The familiar `-c`, `-m`, `-i`, `-o`, `-v`, `-nm`, `-p`, `-b`, `-n` and `-t`
flags remain available before a subcommand. `-o PATH` appends console output to
a file. Verbose logs describe decoding; trace logs identify received records.
`--diagnostic-logging` includes packet bytes and can contain device information.
Keep those logs private.

The passive health check reads a heartbeat file and checks its process. It
does not connect to the datalogger or cloud and stays healthy during normal
overnight silence. MQTT and output failures appear in logs separately.
