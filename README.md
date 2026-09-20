# HA Growatt

HA Growatt sends readings from Growatt inverters to Home Assistant through MQTT.
It also forwards the datalogger connection to Growatt so the cloud service can
continue receiving data.

The project is still in development. Keep your current installation running;
there is no replacement release or installable Home Assistant app yet.

The new code is checked against the working service. Existing sensor identities,
readings and Home Assistant history must carry over to the replacement.

## What is available

- TCP framing for protocol versions 2, 5 and 6, including split and combined records.
- Payload decoding and checksum validation.
- Asynchronous cloud forwarding with connection limits, deadlines and independent
  telemetry processing.
- Eight explicit scalar decoding profiles, checked with 78 synthetic packet cases.
- Automatic family selection, checked against 1,320 recorded selection outcomes.
- The standard 32-sensor Home Assistant discovery profile, preserving established
  device and entity identifiers.
- Full discovery, including the 171-entity MOD profile and its 205-entity
  include-all option, with known retained-topic cleanup.
- The observed command allowlist and verified time-setting exceptions.
- MQTT discovery before state publication, reconnect handling and discovery
  refresh after Home Assistant announces that it is online.
- Offline packet inspection and a development bridge command.
- Loading of the supported proxy and Home Assistant settings from INI files,
  environment variables and Home Assistant app options.

See [compatibility](docs/compatibility.md) for the checks completed and the work
still needed. Further inverter families, some configuration options, additional
output modes and deployment packages are not finished.

## Development

Python 3.12 or later and [uv](https://docs.astral.sh/uv/) are required for the
development commands:

```sh
uv sync --locked
uv run --locked ruff check src tests
uv run --locked ruff format --check src tests
uv run --locked pytest -q
uv run --locked python -m build --no-isolation
```

Inspect a binary file containing complete TCP-stream frames:

```sh
uv run --locked ha-growatt inspect sample.bin
```

The inspector reports record types, sizes and register counts, without printing
device identities or packet contents. For a PCAP capture, first extract and
reassemble the TCP stream into a binary file.

To experiment with the bridge, copy [the example configuration](examples/ha-growatt.toml),
use an isolated MQTT broker and test datalogger, and run:

```sh
uv run --locked ha-growatt run --config /path/to/ha-growatt.toml
```

Set the broker password through `HA_GROWATT_MQTT_PASSWORD`. Keep credentials and
real packet captures outside this repository. The example listener binds only to
loopback; explicitly choose a test interface when using another device.

The `run` command can also read an existing `.ini` file or an app's `options.json`.
INI files retain the existing `g*` environment overrides, including `ginvtype`
and `gextvar`. This currently covers proxy mode with Home Assistant discovery,
server time and buffered publication disabled. Unsupported active features
stop startup with a configuration error. See the
[configuration limits](docs/compatibility.md#configuration) before testing a copy
of an existing configuration.

Existing installations should continue using their current working version while
the replacement is completed and verified.

## Licence

[MIT](LICENSE), copyright Herbertmt978. Dependencies retain their own licences.
See [how the code and tests were written](docs/provenance.md).
