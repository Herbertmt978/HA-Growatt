# Home Assistant features

These features are included in 0.3.0. Additional battery profiles and schedules
are experimental and disabled by default; see the hardware evidence below.

## Setup and support page

Open **HA Growatt → Open web UI** in Home Assistant's Apps settings. The page
shows whether the datalogger listener and MQTT connection are working, whether
readings have arrived, and whether restart recovery is available. If packets
arrive without usable readings, it suggests checking the inverter family rather
than reporting that everything is connected successfully.

With the Mosquitto app installed, leave the broker credentials empty and
`mqtt_auto` enabled. HA Growatt obtains the MQTT service settings from Supervisor.
Existing explicit credentials and external broker addresses are preserved.
Disable `mqtt_auto` for a manually configured broker; use `mqtt_tls` for TLS.
Automatic setup needs a running MQTT provider. If it is unavailable, the app log
explains how to start it or enter manual settings.

Each discovered inverter has a reading-profile selector and a separate control
profile. Changes are saved in the app's `inverters` option and take effect on
the next reading. Saved readings for that inverter are discarded when its
profile changes. Existing HA entities and their history are not deleted.
Avoid editing the app's configuration elsewhere while saving a profile: the
Supervisor API replaces the complete options object. The app checks for changes
before saving and refuses the save if it detects one.
The same list can be edited in the app configuration before the first packet.

**Download diagnostics** creates a JSON support file containing version, known
profiles, connection status and counters. It excludes serials, credentials,
addresses, measurement values and raw packets. The page uses authenticated HA
ingress; it does not expose another port on the household network.

Checks refresh every ten seconds while the page is visible, without replacing
unfinished profile or schedule edits. Measurement failures, incomplete fields
and announcements that are not measurement records have separate counters.
**Start support capture** collects up to 256 packet summaries over ten minutes.
Download the result before restarting the app. It describes protocols, lengths
and decode outcomes; payloads, serials and household readings are excluded even
for an unknown packet layout. This is not a raw packet recording.

Enter the exact model printed on each inverter. **Read firmware from inverter**
reads holding registers 9–14 and accepts only valid ASCII version text. If the
model does not implement those registers, enter its displayed version manually.
These details appear on the existing MQTT device. Blank fields remain unknown;
neither packet size nor a selected control profile establishes an exact model.

## Restart recovery

The app's `restore_readings` option is enabled by default. It saves the last
live reading for each inverter in its private `/data/state` directory. Broker
reconnection and Home Assistant's MQTT birth message cause discovery and saved
measurements to be sent again, even if the inverter is asleep. A cold app
restart loads the same file. The original Last data push timestamp is retained.

Saved readings do not establish a live connection or restore control values.
Connected remains off until fresh telemetry arrives; settings remain unavailable
until a current connection returns a valid setting read. Buffered historical
records never replace these snapshots. The file is bounded and replaced
atomically. If it cannot be read or written, diagnostics explain the recovery
problem while fresh telemetry continues.

Snapshots are scoped to the MQTT endpoint, account and discovery profile. A
change to those settings waits for new readings rather than replaying data into
a different installation. Password rotation alone does not discard readings.
They are included in normal app backups. Disabling recovery stops replay; it
does not erase already retained MQTT messages if `mqtt_retain` was separately
enabled.

Standalone installations can set `mqtt.state_path` in TOML or `ha_state_path`
in the Home Assistant extension options in INI. Use a private writable path on
a persistent volume. Recovery is opt-in outside the app so existing filesystem
and publication behaviour remain unchanged.

These features work in proxy mode through the existing MQTT integration.
They add entities to the existing inverter devices. Measurement names, unique
identifiers, units and history remain unchanged.

## Cloud fallback

`cloud_fallback` is enabled by default. HA Growatt switches the affected
connection to local replies if the cloud connection fails to open, disconnects,
cannot accept forwarded data, or fails to acknowledge a record within 15 seconds.
It also supplies the clock message expected after a datalogger announcement.
Packets already forwarded but still awaiting acknowledgement receive a local
reply. Other datalogger connections continue independently.

Once local replies begin, that connection stays local. This prevents competing
cloud and local acknowledgements. By default a separate connection checks the
cloud every five minutes, backing off to at most forty minutes after failures.
The check requires an echoed protocol heartbeat; an open TCP port is insufficient.
After a successful check and thirty seconds without local commands, HA Growatt
closes the local connection so the datalogger reconnects through the cloud.
No measurement is resent by the probe. The datalogger owns reconnection and any
buffered uploads; no additional buffered cloud replay is added.
ShinePhone will have a gap for records collected only locally.

Set `cloud_recovery_seconds` to change the initial interval, or zero to leave
reconnection to the datalogger. This does not disable local fallback.

Turning `cloud_fallback` off restores the previous behaviour: a cloud connection
failure ends the datalogger session. Existing cloud command filtering still
applies. The supported protocol versions remain 2, 5 and 6; fallback does not
add support for newer session-key encryption.

## Status and diagnostics

- **Connected** means a valid live reading arrived within the last 15 minutes.
  It does not switch off between a datalogger's short connections.
- **Data connection** shows `cloud`, `local` or `disconnected` for the current
  socket. This is separate from whether readings are recent.
- **Decoder profile**, **Readings received** and **Incomplete fields** describe
  the inverter's received data. These counters reset with the app.
- **Cloud fallbacks** and **Output failures** are service-wide counters.
- **Last command result** reports applied settings, rejected requests or missing
  confirmation without exposing packet contents or credentials.

The existing Last data push sensor remains in place. Measurement values are not
cleared overnight. New diagnostics become unavailable if the service stops;
controls also require a current datalogger connection and a successful setting
read. A restarted service does not make stale controls available before their
device reconnects.

## Controls

| Setting | Families | Holding register | Values |
| --- | --- | --- | --- |
| Output power limit | Classic/extended, SPH, SPA, MIN, MOD and TL3 profiles | 3 | 0–100% |
| Grid-first discharge power | SPH/SPA profiles | 1070 | 0–100% |
| Grid-first minimum battery charge | SPH/SPA profiles | 1071 | 0–100% |
| Battery-first charge power | SPH/SPA profiles | 1090 | 0–100% |
| Battery-first charge limit | SPH/SPA profiles | 1091 | 0–100% |
| Battery-first AC charging | SPH/SPA profiles | 1092 | Off/on |

The register meanings come from the manufacturer's
[Modbus protocol V1.24](https://www.photovoltaikforum.com/core/file-download/463106/),
holding-register tables on pages 9 and 32–33. The older
[V3.05 document](https://soulraven.github.io/growatt-monitor/assets/Growatt_PV_Inverter_Modbus_RS485_RTU_Protocol_V3.05.pdf)
also documents output power register 3. Register 1044 is marked read-only in
V1.24, so it is not exposed as a writable priority selector.

An unrestricted output value of 255 is displayed as 100%. Entering 100 writes
100, not 255. Battery controls affect their named operating modes; changing
them does not itself select battery-first or grid-first operation.

SPF, meters, custom layouts and unknown profiles do not receive these write
controls. The SPH/SPA battery register block is not offered on MIN/MOD profiles.
Grid codes, protection limits and arbitrary register writes are not exposed by MQTT.

## Battery schedules and additional models

Select the matching control profile, then enable `experimental_controls` in the
app configuration and restart it. SPH/SPA expose three battery-first charge
periods and three grid-first discharge periods. Each period has start/end text
entities accepting `HH:MM` and an enabled switch for HA automations. The app page
also edits the whole period at once: read it first, change it and save it.

An enabled period must have different start and end times; crossing midnight is
allowed. Times follow the inverter's clock. The complete three-register period
is read and written as a group. Saving from the app also checks that the period
has not changed since it was loaded. If a write cannot be confirmed, the control
becomes unavailable until settings are refreshed. Only confirmation reads are
retried, up to three attempts; writes are sent once.

Explicit MIN TL-XH and MOD/MID TL3-XH profiles add their documented battery
registers separately from the SPH/SPA block. Read the
[hardware evidence and limits](hardware-support.md) before selecting one.
Community reports inform these choices; protocol tests do not establish
compatibility with every firmware version. These controls stay disabled in a
normal installation unless experimental controls are enabled.

## History and Energy preview

Choose **Check existing entities** on the app page. It reads HA's entity and
device registries, current metadata, statistics inventory and Energy settings.
Exact MQTT unique IDs with matching available metadata are marked preserved,
including any entity names you changed yourself. Different-integration matches
are shown as candidates for review. Missing metadata and conflicting units or
state classes are flagged.

The preview highlights the inverter's total generated energy as a solar Energy
Dashboard candidate. Do not also select its individual PV totals for the same
production. It shows existing Energy selections and whether statistics already
exist. A suitable unit and state class do not by themselves prove the reading is
physically correct: compare the values before selecting a new source.

Nothing is renamed, removed or transferred by this check. For another
integration's identity, take a backup and follow HA's entity/history migration
procedure only after checking the proposed mapping. Unmatched sensors need a
manual comparison. Preview requires HA Core and its recorder to be available.

HA Growatt reads settings on first contact and every five minutes by default.
`settings_refresh_seconds` accepts 30–86400 seconds, or zero for manual reads only.
**Refresh
settings** requests a read immediately. No periodic task writes settings back
to the inverter. A user change is sent once, then read back. Rejection, a
different returned value or lost confirmation is shown in Last command result;
the app never retries a write automatically.

**Sync datalogger time** sends the service's local time and reports whether the
datalogger accepted it. This button checks the acknowledgement, not a subsequent
clock read. Configure the container's time zone correctly when running outside
Home Assistant.

Commands use separate sequence tracking from cloud traffic. Retained commands
received on subscription, duplicate deliveries and oversized requests are
ignored. Existing broker authentication and permissions apply to these command
topics; anyone permitted to publish to them can operate the exposed settings.

## Options

The app options are `cloud_fallback`, `ha_features` and `ha_controls`, all enabled
by default. Disable `ha_controls` to retain diagnostics without buttons or
settings; its retained control discovery records are removed when the inverter
next reports. `ha_features: false` stops the additional feature service. Remove
previously created feature entities from Home Assistant if disabling it after
use; existing measurement entities remain unchanged.

INI installations use the same keys in `[Generic]`, with environment overrides
`HA_GROWATT_CLOUD_FALLBACK`, `HA_GROWATT_HA_FEATURES` and
`HA_GROWATT_HA_CONTROLS`. TOML uses `relay.cloud_fallback`,
`runtime.ha_features` and `runtime.ha_controls`. Experimental controls use
`runtime.experimental_controls` and an inverter-to-model `runtime.control_models`
mapping in TOML; INI uses `experimental_controls` and `control_models` in
`[Generic]`. Environment overrides are `HA_GROWATT_EXPERIMENTAL_CONTROLS` and
`HA_GROWATT_CONTROL_MODELS`.

The additional topics are under `ha_growatt/`; existing telemetry topics retain
their historical names. Run one feature publisher per MQTT namespace. Server
and sniffer modes retain their existing interfaces; these MQTT controls belong
to proxy mode.

## Verification limits

Synthetic dataloggers exercise cloud loss, silent connections, local
acknowledgements, command correlation, rejected writes and read-back mismatch.
Native Home Assistant qualification checks discovery and its actual MQTT
number, switch and button services. These checks do not establish that every
listed model or firmware accepts every register. New physical combinations still need
verification. Development does not change settings on the household inverters.

## Optional native Home Assistant companion

Add this repository to HACS as an **Integration**, download HA Growatt, restart
Home Assistant and add **HA Growatt** under Devices & services. The app, MQTT
integration and companion must use the same broker. The companion needs no new
credentials and does not listen for dataloggers or create measurement sensors.

The companion raises HA Repairs for an app that stops reporting, measurements
that cannot be decoded, or an inverter that stays silent during daylight. It
uses HA's configured location and Sun integration, waits two minutes after its
own startup and, by default, thirty minutes after sunrise. The default stale
threshold is fifteen minutes. Change these under the companion's options.
Fresh readings clear feed warnings; night-time silence does not raise them.
App/broker failures are reported separately, including at night. Existing sensor
values and their original timestamps are preserved throughout.

Buffered readings never overwrite current sensors. With `buffered_events`
enabled in the app and companion, they produce `ha_growatt_buffered_record` events
containing the inverter, its local recorded time and unscaled decoded integer
fields. Automations must apply the appropriate field scaling. A bounded duplicate
filter and rejection of retained replays prevent common duplicate deliveries;
this is not an exactly-once archival feed. Disable the option to discard these
events. Raw MQTT, InfluxDB and other existing outputs retain their own policies.

### Adopt a previous entity ID

The `ha_growatt.adopt_history` action first previews a historical ID for an HA
Growatt MQTT sensor. The old entity must be removed, with its statistics retained;
disabling it does not release its ID. The action checks ownership, current data,
compatible units and cumulative versus measurement statistics.

```yaml
action: ha_growatt.adopt_history
data:
  target_entity: sensor.new_inverter_generated_energy_total
  source_entity_id: sensor.previous_solar_energy_total
response_variable: preview
```

Compare both actual readings, the measured circuit and daily versus lifetime
counters, then make a backup. To apply the rename, add `confirm: true` and
`same_measurement: true`. The operation uses HA's entity registry; it does not
delete or merge statistics. Existing references to the historical ID continue
to work. Any statistics collected under the target's previous ID stay separate.
HA may log that it cannot rename that statistic because the historical ID already
exists: the older series is intentionally preserved. Renaming back is not a clean
undo, because HA can then move the historical series with the entity.

The companion's native diagnostics download excludes device identities, readings
and network settings. Removing the companion leaves the app's measurements running.
