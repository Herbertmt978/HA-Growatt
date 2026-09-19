# Compatibility and completion criteria

The target is the behaviour of the existing maintained product at version 0.1.13.
This file records the current implementation boundary; it does not reduce the
requested scope of the replacement.

| Area | Current evidence | Remaining work |
| --- | --- | --- |
| TCP stream handling | Real loopback sockets exercise fragmented/coalesced frames, command filtering, corrupt checksums, backpressure, connection limits and shutdown; 3,072 record-policy comparisons plus command exceptions | Native deployment qualification and unobserved exceptional cases |
| Scalar telemetry | 78 newly generated packets cover eight explicit profiles, normal/include-all fields and additional observed lengths; live, announce and buffered record variants are exercised offline | Additional lengths, family variants, smart meters and custom layouts |
| Register reports | A saved protocol-6 capture passes framing, checksum and register-range parsing | Broader capture coverage and other envelope formats |
| Standard discovery | All 64 retained records match identity, topic, units, statistics and template behaviour; 80 recovered late-day packets from one inverter match decoded reference values | Native Home Assistant migration/restart and fresh both-device daylight checks |
| MQTT delivery | Configs precede state; QoS 1 acknowledgements, failures, reconnects and Home Assistant birth handling are tested, including the real Paho client over loopback | Native broker and Home Assistant qualification |
| Default decoding | Known classic and extended sizes can coexist through `wire_profile = "auto"` | The existing plausibility-based family selection and strict/automatic settings |
| Full discovery | Observed metadata and source mappings for eight profiles; 32/171/205 MOD counts, raw diagnostic identity and generic/MOD retained-topic reconciliation, with cleanup retry | Native Home Assistant profile-change and statistics qualification |
| Configuration | Validated development TOML with a password environment variable | Existing INI/environment/add-on option compatibility, time policy and safe migration |
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

`auto` selects only the four default profiles by protocol and verified length.
It does not yet select a family from plausibility scores. A specifically selected
family applies to the whole bridge, so mixed-family installations requiring
different explicit profiles are not yet supported.

The standard discovery templates preserve the actual AC-power, frequency and
communication-board temperature aliases used by the current installation.
Measurement sensors keep their last readings overnight. Only the freshness
timestamp has a 900-second expiry. Existing technical MQTT identifiers retain
their historical prefix to avoid creating duplicate entities or losing history.
Select `entity_profile = "all"` in the MQTT table for full discovery, and
`include_all = true` to decode and discover the additional excluded fields.
The standard profile continues to expose 32 entities for generic/MOD packets
regardless of the include-all setting. State retains the complete decoded data.

The current bridge observes live function-4 telemetry only. Other valid frames
continue through the relay, subject to the current development command filter.
With `block_commands = true`, the relay uses the observed record allowlist in
both directions. Valid protocol-5/6 time-setting requests are permitted;
destination-setting requests require `allow_destination_change = true`.
Invalid checksums cannot use these exceptions. Disabling `block_commands`
permits other records unless an explicit cloud function block is configured.
Offline decoding covers functions 3, 4 and 80; publishing announce and buffered
records requires the pending time and buffered-record policy work.

## Release acceptance

Before a replacement release, finish the outstanding functionality above and
maintain explicit compatibility cases for each supported path. Verify existing
Home Assistant identities, units, statistics, full discovery changes, startup,
reconnection and restoration. Exercise the final Docker and Home Assistant
artifacts on the required platforms. Compare both physical inverter feeds with
the current service and ShinePhone during daylight.

Keep the working service and its rollback material until the replacement passes
those checks. Archiving the previous fork must follow replacement qualification.
Production deployment remains separate from source publication.
