# HA Growatt

HA Growatt reads Growatt datalogger traffic and publishes inverter and meter
readings. It can forward the connection to Growatt, run as a local datalogger
server, or listen passively to traffic on a Linux network interface.

Version 0.3.0 includes Docker images and a Home Assistant app. Follow the
[migration instructions](docs/installation.md) when replacing an existing
service so its configuration and sensor history are retained.

Home Assistant discovery keeps existing device and sensor identifiers, units
and statistics settings. The standard profile exposes 32 sensors for the
generic and MOD layouts. Full discovery includes the other decoded fields.
MQTT topics retain their existing technical prefix so existing installations
can keep their entity history.

The implementation includes:

- Proxy, standalone server and passive sniffer modes.
- Protocol versions 2, 5 and 6, including split records, command filtering,
  acknowledgements and the local register API.
- The 15 built-in layouts, automatic and per-device family selection, custom
  JSON layouts and serial-relative compatibility decoding.
- Home Assistant discovery, raw MQTT, PVOutput, InfluxDB 1 and 2, CSV export,
  HTTP delivery and the existing Python extension callback interface.
- INI files, environment overrides, Home Assistant app options and a TOML
  configuration for the Home Assistant service.

See [configuration](docs/configuration.md), [installation](docs/installation.md)
and the [compatibility record](docs/compatibility.md). The compatibility record
describes the behaviour checked and the limits of physical hardware testing.

## Cloud fallback and Home Assistant controls

The app includes a web UI with connection checks, per-inverter profiles,
redacted diagnostics and a read-only history/Energy preview. It can use Home
Assistant's MQTT service automatically. Private saved readings restore the last
measurements after a quiet restart, keeping their original timestamps.

In proxy mode, HA Growatt can answer the datalogger locally when Growatt is
unreachable or stops replying. Readings continue to reach Home Assistant. That
connection stays local until a protocol health check confirms the cloud is
responding. HA Growatt then asks the datalogger to reconnect after local commands
have finished. Failed checks back off; the next connection tries Growatt again.
ShinePhone does not receive readings while the connection is local.

Additional Home Assistant entities show whether readings are arriving, whether
the current connection uses the cloud or local fallback, and decoding and
delivery diagnostics. Existing measurement sensors keep their identifiers and
their last readings overnight.

Supported inverter families also get an output power limit. SPH/SPA families
get documented battery-first charging and grid-first discharge settings. Each
setting must respond to a read before it becomes available. Writes are checked
by reading the setting back; a missing reply is not treated as success.
Refresh settings and Sync datalogger time buttons are included.

See [the feature guide](docs/home-assistant-features.md) for supported settings,
options and testing limits. These additions use the existing app and MQTT
integration. Settings refresh is configurable, including manual-only operation.

An optional **HA Growatt companion integration** adds daylight-aware repair
notices, buffered-reading events and a guarded history-adoption action. Add this
repository to HACS as an Integration, download it, restart Home Assistant and add
HA Growatt under Devices & services. Keep the app running: the companion uses
its MQTT status and creates no duplicate measurement sensors.

Experimental battery schedules and additional model controls are disabled by
default. The [hardware evidence](docs/hardware-support.md) separates manufacturer
documentation, community field results and our own installation checks.

## Run from Python

Python 3.12 or later is required. From a checkout with
[uv](https://docs.astral.sh/uv/) installed:

```sh
uv sync --locked
uv run --locked ha-growatt run --config /path/to/ha-growatt.ini
```

An existing INI file can be used directly. Keep its broker, publication and
layout settings while testing the replacement. The
[Home Assistant example](examples/ha-growatt.ini) uses cloud forwarding with
command blocking and the standard discovery profile.

Use a separate broker and synthetic device identities for development. Keep
credentials and real packet captures outside this repository.

## Development checks

Run these before publishing source:

```sh
uv sync --locked
uv run --locked ruff check src tests custom_components
uv run --locked ruff format --check src tests custom_components
uv run --locked pytest -q
uv run --locked python -m build --no-isolation
```

Run the Python checks on 3.12, 3.13 and 3.14. Packaging changes also require
container checks on amd64, arm64, arm/v7 and 386. A release requires native
Home Assistant migration and restart checks, then fresh readings from both
inverters in the installation being replaced.
The companion's real MQTT, Repairs, reload, unload and history-adoption checks
run through `tests/ha_reliability_probe.py` in a disposable Home Assistant instance.

Inspect an extracted binary TCP stream without publishing anything:

```sh
uv run --locked ha-growatt inspect sample.bin
```

The inspector reports record types, sizes and register counts without printing
device identities or packet contents. Extract and reassemble PCAP streams first.

## Disclaimer

By using HA Growatt, you accept responsibility for the security of the data you
extract. Neither HA Growatt nor Growatt can be held responsible for data breaches
stemming from the extraction of data outside of the Growatt ecosystem.

## Licence

[MIT](LICENSE), copyright Herbertmt978. Dependencies retain their own licences.
See [how the code and tests were written](docs/provenance.md).
