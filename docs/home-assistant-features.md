# Home Assistant features

The integration can receive datalogger traffic itself, poll an explicitly
configured direct Modbus TCP connection, or act as a companion to the app.
Battery controls and schedules remain experimental and disabled by default;
see the hardware evidence below.

## Integration-only installation

From version 0.6.0, install the repository as a custom HACS integration or copy
`custom_components/ha_growatt` into Home Assistant. Home Assistant downloads
the matching receiver wheel from the release, so it needs internet access for
the first installation or upgrade. Add **HA Growatt** under Devices & services
and choose **Receive dataloggers in Home Assistant**. Select the TCP port, cloud
forwarding and a preferred inverter family. **Automatic** is the normal family
choice. Point the ShineWiFi upload server at Home Assistant's LAN address and
the chosen port; stop another service using that port first.

This route creates native measurement sensors and a Connected binary sensor
for each inverter. Its decoder, cloud forwarding, local fallback and restart
cache use the same Python library as the app. Buffered uploads fire
`ha_growatt_buffered_record` events but never replace live readings. Daylight
Repairs, redacted diagnostics and the `adopt_history` preview also work without
MQTT. A profile-family change invalidates saved readings until a new packet
arrives. The last reading keeps its original timestamp on restart; Connected
remains off until live data arrives.

For an unknown layout, call `ha_growatt.capture_evidence` with `action: start`.
After packets arrive, call it with `action: report` and use the action response
as the shareable report. Capture lasts at most ten minutes and 256 frames, and
is cleared after thirty minutes or shutdown. The report contains only packet
structure and decoding outcomes. No private replay is returned by this action.

If you are investigating a model that does not decode reliably, turn on
**Unrecognised Shine packet diagnostics** under **Configure → Readings, alerts
and controls**. This adds one diagnostic sensor to the HA Growatt receiver. It
counts packets that failed decoding or had incomplete fields and lists up to
eight packet shapes by protocol, function, payload length and known profile.
Additional shapes are counted together. The option is off by default and the
summary starts afresh when the receiver reloads. It does not include serials,
timestamps, packet bytes or measurement values. It does not claim that an
unmapped byte is an inverter register or assign it a sensor unit. Use the
shareable `capture_evidence` report for a fuller investigation; neither view
can decode session-key encrypted traffic. The same bounded summary is included
in the integration's diagnostic download while the option is enabled.

The current MIC and MIN Shine layouts decode measurements from fixed byte
offsets; they do not establish an address for every unassigned byte. Those
bytes can contain text or other private data even when the known fields decode
correctly. For this reason the integration does not expose an unassigned byte
as an unnamed numerical sensor. The structured register parser applies only
when a packet actually declares valid register ranges. A new model needs
reviewed protocol evidence before additional readings can be named and shown.

The native Shine route offers optional raw MQTT, PVOutput, InfluxDB 1 or 2 and
HTTP delivery. These outputs run in separate bounded queues and skip buffered
records. Configure them under **HA Growatt → Configure → Optional output
destinations**. Clear a destination's address or system ID to turn it off.
Passwords and tokens are kept out of diagnostic downloads; enter a new value
only when changing one. If you change a destination address, port, TLS setting
or account, enter its password or token again; the previous credential is not
sent to the new destination. Output failures appear in the receiver's diagnostic
sensors and download without interrupting readings or cloud forwarding. The
HTTP body follows the app's JSON-string format. CSV and executable Python
extensions remain in the app, where their file and process access belongs.

Supported native Shine controls are off by default. In **Configure → Readings,
alerts and controls**, enable them after checking the inverter's physical
model. Under **Choose an inverter control profile**, record that exact model
and select a profile only if it matches. A setting appears after a live
datalogger session answers a read; each write is checked with a readback. The
output-power limit uses the documented register. Battery controls and SPH/SPA
schedule times need the separate experimental switch, a matching physical
model and an appropriate decoded profile. The native receiver never offers
arbitrary register writes or grid-code settings. A model name entered in the
UI is owner-supplied information, not hardware verification.

Home Assistant's device pages show reading and connection entities plus
receiver diagnostic counters. Use **Download diagnostics** on the integration
or device page for redacted support details; use Repairs for daylight failures
and `capture_evidence` for an unfamiliar Shine packet. The app retains its
guided web support page. Changing routes creates new entity IDs; use the
history-adoption preview before releasing an old ID. Do not point one
datalogger at both receivers.

## Direct Modbus TCP

This is a separate connection to an inverter or gateway, not the ShineWiFi
upload stream. Choose **Read a direct Modbus TCP connection** during integration
setup and enter its reachable address, Modbus unit number and a stable device
identity. **Auto** uses read-only device type codes and an input-range probe
to distinguish supported MIN and MIC layouts. It recognises codes 5100, 5200
and 5201, with a matching input range; an unavailable or ambiguous code asks
for manual selection rather than guessing. You can also select the MIN TL-X/XH
V1.24 profile for a matching MIN, the MIC V3.14 profile for a matching MIC,
or the legacy V1.24 profile only when that register table matches the device.
The names describe manufacturer
register tables; the two owner-owned inverters have not been checked through
this direct connection. One minute is the default poll interval.

The separate `min-three-string-v124` profile adds PV3 voltage, current,
power and daily and lifetime energy for a three-string MIN TL-X/XH using the
V1.24 3000-series input table. It attempts a separate diagnostic block for
temperature, fault, warning and derating codes. A missing diagnostic block
does not discard the core solar reading. The published
[0xAHA model matrix](https://0xaha.github.io/Growatt_ModbusTCP/hardware/models/)
reports related hardware using its own integration; it does not verify this
HA Growatt profile or the owner's 2500 W MIN. Check the actual readings against
the inverter before selecting them in Energy.

The separately selected `tl3-three-phase-v139` profile follows the published
Protocol II V1.39 input table for devices with three PV strings and three AC
phases. It adds phase 2 and 3 voltage and current, phase apparent power in VA,
and line voltages. Apparent power is not active power, so this profile does not
create phase watt or grid-import/export sensors from those registers. It also
uses the optional fault and temperature block. Shared MOD/MID device type codes
do not select this profile automatically. Compare live values against the
inverter before using its energy totals; no HA Growatt physical Modbus result
is claimed by the published [0xAHA V1.39 register table](https://0xaha.github.io/Growatt_ModbusTCP/developer/protocol-v139/).

Connection options permit a maximum read block of 4–32 words, a 0.5–10 second
pause between requests and a 0.5–10 second reply timeout. Defaults remain 32
words, one second and three seconds. Smaller blocks and slower requests can
help limited gateways, at the cost of a longer poll. The interval stays at one
minute by default. The connection remains TCP and read-only; serial/UDP and
direct Modbus control are not included. The published
[0xAHA gateway guidance](https://0xaha.github.io/Growatt_ModbusTCP/troubleshooting/rs485-gateways/)
and [Growatt Modbus polling notes](https://github.com/jacobbjerregaard/homeassistant-growatt-modbus#data-updates)
provide other projects' hardware experience, not HA Growatt verification.

For an unfamiliar direct Modbus device, choose `investigate-raw` and a single
input or holding-register block of at most 32 words. The integration creates
one diagnostic entity per address, disabled by default. You can enable only
the addresses you need in Home Assistant. Values are raw unsigned words with
no unit or energy meaning. They are not saved for restart recovery or included
in diagnostic downloads, but enabling an entity records its state in Home
Assistant. Raw words may contain serial numbers or settings: keep their states
and screenshots private. This option sends paced reads only and offers no
writes. It does not inspect unknown Shine packet bytes; use
`ha_growatt.capture_evidence` for those packets.

Documented profiles send only input-register reads after Auto's read-only
identification probes, if selected. A complete and plausible set of core
blocks updates the sensors. A failed block, incorrect profile or
unavailable inverter leaves the last valid measurements in place and turns the Connected
sensor off. The saved reading returns after restart without marking the
gateway connected. It does not forward to ShinePhone, publish optional outputs
or offer writes. If a suitable direct gateway is not present, keep the
working Shine route. Never infer direct Modbus access solely from the presence
of a ShineWiFi-X datalogger.

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
The setup form reports whether a fresh app status arrived through Home
Assistant's MQTT connection. A retained status is not counted as a live check.
If it cannot see the app, check that both use the same broker; a temporary
outage does not prevent adding the companion.

The companion raises HA Repairs for an app that stops reporting, measurements
that cannot be decoded, or an inverter that stays silent during daylight. It
uses HA's configured location and Sun integration, waits two minutes after its
own startup and, by default, thirty minutes after sunrise. The default stale
threshold is fifteen minutes. Change these under the companion's options.
Fresh readings clear feed warnings; night-time silence does not raise them.
Missing app status also waits until daylight and the sunrise grace period.
An explicit app-offline message still raises a warning at night. Existing sensor
values and their original timestamps are preserved throughout. The app sends
its heartbeat even when no inverter is reporting.

Buffered readings never overwrite current sensors. With `buffered_events`
enabled in the app and companion, they produce `ha_growatt_buffered_record` events
containing the inverter, its local recorded time and unscaled decoded integer
fields. Automations must apply the appropriate field scaling. A bounded duplicate
filter and rejection of retained replays prevent common duplicate deliveries;
this is not an exactly-once archival feed. Disable the option to discard these
events. Raw MQTT, InfluxDB and other existing outputs retain their own policies.

### Adopt a previous entity ID

The `ha_growatt.adopt_history` action first previews a historical ID for an HA
Growatt MQTT or native sensor. The old entity must be removed, with its
statistics retained;
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

## Reading and setting diagnostics (0.5.0)

Four extra diagnostic sensors describe the operating state, main fault, reported
clock and setting changes. Existing numeric sensors, entity IDs, readings and
history are unchanged. These checks never correct a clock or retry a write.

Clock checks compare each fresh packet's wall-clock timestamp with the configured
publication time zone, or the service's local time when that setting is `local`.
The support page names that reference and shows the signed difference in seconds:
positive means the packet clock is ahead. This is not a measurement of the inverter's
internal clock register. Network or logger buffering can also produce a difference.
Within two minutes is treated as aligned; an offset close to one hour suggests a
possible time-zone or daylight-saving difference, without asserting its cause.
The service time zone can differ from Home Assistant's. Check the named reference
before using the existing manual clock button. No fresh reading for fifteen minutes
changes these sensors to waiting for fresh readings, including overnight. Restored
and buffered readings do not produce fresh clock warnings.

MIC TL-X and MIN TL-XH operating states use the documented TL-X web-status byte
when decoded with `mod-6` or `min-6`. Main fault descriptions currently cover the
MIC codes listed in the manufacturer manual. Other codes and families are explicitly
unmapped. Storage fault fields and bitfields are not substituted for an inverter's
main fault. Keep the original numeric sensors and consult the inverter display and
its model-specific manual if a fault persists. This mapping has protocol tests;
we have not deliberately induced faults on physical inverters.

The support page keeps bounded packet-format observations for this service run.
A new protocol, payload length or decoded profile is recorded after successful
measurement decoding. Failed or incomplete measurements are shown separately;
a later complete measurement reports recovery. Announcements and buffered records
do not change the baseline. A format change is evidence to investigate, not proof
that firmware changed. A manual firmware read which differs from saved details
clears cached settings and requires fresh readback. Correcting a recorded firmware
version also invalidates commands queued under the previous details. Firmware is
not polled automatically, and a firmware update that leaves the format unchanged
cannot be detected from packet shape alone.

Setting-change attribution requires an observed, forwarded cloud write, a matching
successful device reply, and a later local register read matching that cloud value
while differing from the requested local value. A mismatch alone is labelled
unconfirmed. Blocked commands, timeouts, stale reads and ambiguous reused cloud
transactions do not count as confirmed cloud changes. Single-register settings and
supported grouped schedules use the same checks. Evidence lasts for the current
connection, is bounded to 256 registers and 256 distinct cloud write requests, and
is only available for writes passing through this relay. Reconnect resets it.
An unrelated successful read does not clear another setting's conflict. The normal
cloud-write blocking default remains enabled. No automatic corrective write is sent.

### Hardware identification and register inspection

Version 0.5.0 adds explicit read-only identification and small holding-register
reads in the app and companion actions. Reports include uncertain or unavailable
fields; applying metadata remains a separate choice. See the
[register tools guide](register-tools.md) for comparisons, private exports,
administrator access and the limits of model detection.

### Shareable packet evidence and private replay

The usual diagnostics and support capture remain redacted and contain no packet
bodies. If those are insufficient, expand **Private packet capture for offline
replay**, acknowledge the private data notice and start recording. You can then
download **shareable evidence** from the same short-lived capture. It gives each
feed a generic label and records protocol, function, packet length, active
32-byte blocks, decoding result, selected built-in profile and missing fields.
Frames that fail decoding also appear in an **undecoded layouts** summary,
grouped by feed, protocol, function and packet length. It shows how often each
shape appeared, which 32-byte blocks changed between samples, a fixed reason
category, the selected profile when built in or automatic, and built-in
profiles with compatible headers. A compatible header does not mean that the
profile can decode the packet. Long block lists are limited to 64 entries with
an omitted count. The file contains no payload bytes, serial numbers, exact
times or measurement values.
This is the file to attach to a public issue. Check it before posting, especially
if you have added custom layouts or support tooling of your own.

For a decoder investigation that needs actual measurement values, download the
**serial-redacted replay**. This preserves the supported frame shape and known
numeric fields, replaces serials with generic stand-ins, gives every frame a
fixed synthetic timestamp and removes unknown bytes. Frames that fail decoding,
use a custom layout or contain log text are skipped rather than copied. The
download says how many were skipped. It still contains real numeric readings,
which may reveal generation or household activity. Review it before sharing and
get the owner's consent before posting someone else's data. Replay it offline
with the same command as the private file; the saved built-in profile is used.

The private download still contains complete packets, including serial numbers
and household readings. Keep it private; do not upload it to a public issue or
commit it to Git. The shareable evidence cannot replay those packets or reveal
the exact values behind an unknown layout. A private capture may still be needed
for a difficult decoder investigation, with the owner's informed consent.

Capture stops after ten minutes, 256 frames or 2 MiB, whichever comes first. Its
in-memory contents are deleted after thirty minutes, on request or on app shutdown.
A downloaded copy remains your responsibility. Only complete supported framed
announcement/measurement packets are included; malformed framing and unsupported
wire protocols cannot be reconstructed by this capture. It does not collect writes.

Replay on a trusted computer using the same decoder configuration:

```sh
ha-growatt replay ha-growatt-private-capture.json --config ha-growatt.toml
```

Replay does not start the relay, connect to MQTT or write to a device. Its report
contains decode outcomes and field counts, not identities or readings. Unknown
layouts cannot be automatically guaranteed safe to publish. Build a reviewed,
synthetic regression fixture before sharing an example in the repository.
