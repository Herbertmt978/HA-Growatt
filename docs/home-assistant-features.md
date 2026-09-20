# Home Assistant features

These additions are in development and are not part of the 0.1.1 release.

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
cloud and local acknowledgements. The next datalogger connection tries the cloud
again. Devices that keep a connection open for a long time may therefore remain
local after an outage until they reconnect. No buffered cloud replay is added.
ShinePhone will have a gap for records collected only locally.

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
Charge schedules, grid codes, protection limits and arbitrary register writes
are not exposed by MQTT.

HA Growatt reads settings on first contact and every five minutes. **Refresh
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
`runtime.ha_features` and `runtime.ha_controls`.

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
