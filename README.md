# HA Growatt

Local Growatt telemetry for Home Assistant, developed by **Herbertmt978** and
licensed under the **MIT licence**.

**Development work in progress. This is not yet a replacement for an existing
installation. No stable release or installable Home Assistant app has been published.**

HA Growatt receives datalogger traffic, forwards it to the configured Growatt
endpoint, and sends telemetry to Home Assistant through MQTT. The implementation
is being written independently, with compatibility checked against saved
telemetry and synthetic input/output experiments.

## Implemented

- TCP framing for protocol versions 2, 5 and 6, including split and combined records.
- Payload decoding and checksum validation.
- Asynchronous cloud forwarding with connection limits, deadlines and independent
  telemetry processing.
- Eight explicit scalar decoding profiles, checked with 78 synthetic packet cases.
- The standard 32-sensor Home Assistant discovery profile, preserving established
  device and entity identifiers.
- Full discovery, including the 171-entity MOD profile and its 205-entity
  include-all option, with known retained-topic cleanup.
- The observed command allowlist and verified time-setting exceptions.
- MQTT discovery before state publication, reconnect handling and discovery
  refresh after Home Assistant announces that it is online.
- Offline packet inspection and a development bridge command.

The complete feature comparison and remaining work are in
[compatibility](docs/compatibility.md). In particular, automatic family selection,
configuration migration, additional output modes
and deployment packaging remain unfinished. Passing the current tests does not
establish complete compatibility or hardware readiness.

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

The inspector reports record types, sizes and register counts. It does not print
device identities or packet contents. A PCAP file must first have its TCP stream
reassembled; the inspector does not accept PCAP containers.

To experiment with the bridge, copy [the example configuration](examples/ha-growatt.toml),
use an isolated MQTT broker and test datalogger, and run:

```sh
uv run --locked ha-growatt run --config /path/to/ha-growatt.toml
```

Set the broker password through `HA_GROWATT_MQTT_PASSWORD`. Keep credentials and
real packet captures outside this repository. The example listener binds only to
loopback; explicitly choose a test interface when using another device.

Existing installations should continue using their current working version while
the replacement is completed and verified.

## Licence

[MIT](LICENSE). Dependencies retain their own licences. This project contains no
bundled predecessor runtime and does not load one when it runs. See
[implementation provenance](docs/provenance.md) for the distinction between
source implementation and behavioural compatibility evidence.
