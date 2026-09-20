# Compatibility

The replacement must preserve the behaviour of the current service, version
0.1.13. This page records what has been checked and what still needs work.

| Area | Current evidence | Remaining work |
| --- | --- | --- |
| TCP stream handling | Real loopback sockets exercise fragmented/coalesced frames, command filtering, corrupt checksums, backpressure, connection limits and shutdown; 3,072 record-policy comparisons plus command exceptions | Native deployment qualification and unobserved exceptional cases |
| Scalar telemetry | 78 newly generated packets cover eight explicit profiles, normal/include-all fields and additional observed lengths; live, announce and buffered record variants are exercised offline | Additional lengths, family variants, smart meters and custom layouts |
| Register reports | A saved protocol-6 capture passes framing, checksum and register-range parsing | Broader capture coverage and other envelope formats |
| Standard discovery | All 64 retained records match identity, topic, units, statistics and template behaviour; 80 recovered late-day packets from one inverter match decoded reference values | Native Home Assistant migration/restart and fresh both-device daylight checks |
| MQTT delivery | Configs precede state; QoS 1 acknowledgements, failures, reconnects and Home Assistant birth handling are tested, including the real Paho client over loopback | Native broker and Home Assistant qualification |
| Family selection | 1,320 recorded outcomes cover default, SPH, MOD, MIN and TL3 selection, strict/automatic switches and score thresholds; 78 profile scores also match | Other families, per-device family mappings and additional packet shapes |
| Full discovery | Observed metadata and source mappings for eight profiles; 32/171/205 MOD counts, raw diagnostic identity and generic/MOD retained-topic reconciliation, with cleanup retry | Native Home Assistant profile-change and statistics qualification |
| Configuration | TOML, supported proxy/HA INI settings and app options; ten recorded INI/environment cases check effective settings and override order | Other active options, time/buffered policy and native migration checks |
| Other services | Not implemented | Server mode and its API, sniffer mode, native raw MQTT, PVOutput, InfluxDB and extension compatibility |
| Packaging | Python project, development entry point and pinned Python check workflow | Hardened Docker images, Home Assistant app metadata, health checks and platform qualification |
| Retirement | The previous repository remains available | Publish the verified replacement, document migration and archive the previous fork |

## Observed decoding profiles

These are exact observed packet shapes, not claims of complete model support.
The decoder rejects an unverified size rather than producing guessed values.

| Profile | Protocol | Payload bytes | Scalar output fields |
| --- | --- | --- | --- |
| `classic-2` | 2 | 215 | 31 |
| `classic-5` | 5 | 215 | 31 |
| `classic-6` | 6 | 255 | 31 |
| `extended-6` | 6 | 575 or 829 | 31 |
| `mod-6` | 6 | 575 or 829 | 170 |
| `min-6` | 6 | 575 or 829 | 151 |
| `sph-6` | 6 | 575 or 829 | 68 |
| `tl3-6` | 6 | 575 or 829 | 38 |

With `wire_profile = "auto"`, the bridge scores the verified layouts for each
packet. The `[selection]` table accepts `family`, `strict`, `automatic` and
`minimum_score`. Defaults match the current proxy setup: `default`, `false`,
`true` and `20`. Setting `minimum_score = 0` disables that check. Explicit wire
profiles continue to use that profile alone.

Automatic selection can choose different supported families for different
devices. Per-device family mappings and the remaining SPA, SPF, meter and custom
layouts still need work; the current comparisons do not establish support for
those devices.

The standard discovery templates preserve the actual AC-power, frequency and
communication-board temperature aliases used by the current installation.
Measurement sensors keep their last readings overnight. Only the freshness
timestamp has a 900-second expiry. Existing technical MQTT identifiers retain
their historical prefix to avoid creating duplicate entities or losing history.
Select `entity_profile = "all"` in the MQTT table for full discovery, and
`include_all = true` to decode and discover the additional excluded fields.
The standard profile continues to expose 32 entities for generic/MOD packets
regardless of the include-all setting. State retains the complete decoded data.

The bridge publishes function-3 announcements and function-4 telemetry. Other
valid frames
continue through the relay, subject to the current development command filter.
With `block_commands = true`, the relay uses the observed record allowlist in
both directions. Valid protocol-5/6 time-setting requests are permitted;
destination-setting requests require `allow_destination_change = true`.
Invalid checksums cannot use these exceptions. Disabling `block_commands`
permits other records unless an explicit cloud function block is configured.
Offline decoding covers functions 3, 4 and 80. The existing Home Assistant output
skips buffered records; the bridge preserves that rule. Time and buffered-record
policies for the other output modes still need work.

## Configuration

The bridge can read the existing proxy configuration when it uses Home Assistant
discovery through `grottext.ha`, `nomqtt = True`, `time = server` and
`sendbuf = False`. INI options are read first, then the existing `g*` environment
variables override them. `gextvar` replaces the extension mapping as a whole.
The loader reads JSON objects and Python dictionary literals without executing
expressions. Broker passwords stay out of diagnostic representations.

The supported settings cover listener and upstream addresses, command blocking,
the destination-change exception, family selection, include-all fields and the
Home Assistant broker, retention and entity-profile options. The old `extvar`
password remains in that configuration; `HA_GROWATT_MQTT_PASSWORD` is used by the
new TOML format.

An app's `options.json` can supply the documented proxy and Home Assistant
settings. Reading those options does not provide an installable app. Combining
app options with additional container overrides still needs verification.

The loader stops before opening connections when an enabled feature is not yet
implemented, including native MQTT, other outputs, per-device family mappings,
inverter timestamps or buffered publication. Keep the working service in place
while those paths and the final migration are completed.

## Before release

Before a replacement release, finish the outstanding functionality above and
add compatibility cases for each supported path. Verify existing
Home Assistant identities, units, statistics, full discovery changes, startup,
reconnection and restoration. Exercise the final Docker and Home Assistant
artifacts on the required platforms. Compare both physical inverter feeds with
the current service and ShinePhone during daylight.

Keep the working service and its rollback material until the replacement passes
those checks. Archiving the previous fork must follow replacement qualification.
Production deployment remains separate from source publication.
