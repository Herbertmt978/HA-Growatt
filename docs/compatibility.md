# Compatibility

HA Growatt implements the interfaces and outputs of the previous service,
version 0.1.13. The checks below use recorded behaviour and synthetic packets.
Real captures and installation details remain outside this repository.

| Area | Evidence |
| --- | --- |
| Proxy | Real sockets cover fragmented and combined frames, forwarding, command filtering, corrupt checksums, backpressure, connection limits and shutdown. The record policy has 3,072 comparisons plus command exceptions. |
| Decoding | 116 generated packet cases cover all 15 built-in layouts, ordinary and excluded fields, buffered records, binary meters and CSV meter logs. Custom JSON layouts and serial-relative decoding have separate checks. |
| Family selection | Recorded selections and scores cover strict and automatic selection, score thresholds, per-device mappings and the additional SPF, SPA and meter profiles. |
| Home Assistant discovery | Thirty observed profile combinations cover 2,070 discovery records, including names, icons, units, classes and source mappings. Standard discovery retains 32 entities per generic/MOD device. Full discovery and retained-topic cleanup are checked separately. |
| MQTT | Discovery precedes state. Tests cover acknowledgements, delivery failures, reconnection and Home Assistant birth messages, including a real Paho connection. Raw MQTT has separate message, topic, retention and configuration cases. |
| Optional outputs | PVOutput requests and rate limits, InfluxDB 1 database creation/writes, InfluxDB 2 writes, CSV files, HTTP payloads and the Python callback interface are exercised. HTTP tests use local servers. Slow or failing outputs cannot block forwarding or the other outputs. |
| Server | Real connections check protocol 2, 5 and 6 acknowledgements, clock responses, reconnects and register requests. Fifty-one observed API exchanges cover reads, writes, multiple registers and error responses. |
| Sniffer | TCP reassembly covers retransmission, reordering, wrapping sequence numbers and bounded connection storage. Container probes exercise actual Linux packet capture. |
| Configuration | INI settings, environment overrides, app options, time zones, buffered publication, native MQTT, optional destinations, custom layouts and extension settings are covered. |
| Python and Linux | All 2,727 tests, lint checks and package builds pass on Python 3.12, 3.13 and 3.14. The full suite also passes against the installed Linux Python 3.13 image. |
| Container platforms | amd64, arm64, arm/v7 and 386 images pass installed-package decoding, real proxy/server connections, passive health, SIGTERM shutdown and Linux capture probes. |
| Native Home Assistant | An isolated instance on HAOS-DEV accepts the old and new discovery records with the same 64 entity identities, values, units and classes. Full discovery and cleanup, MQTT reload/birth and broker restart pass. Recorder retains history and the expected statistic metadata. |
| Physical installation | Ninety daylight frames from both inverters match the previous decoder, with no mismatches. Both fresh MQTT states match. The 64 discovery records retain their identities and metadata; the freshness label becomes “Last data push”. |

## Layouts and identifiers

Built-in profiles cover classic protocols 2, 5 and 6; extended protocols 5 and 6;
SPH and SPF protocols 5 and 6; SPA, MIN, MOD and TL3 protocol 6; and the protocol-6
binary and CSV meter records. These are observed protocol layouts, not a promise
that every Growatt model or firmware has been tested on physical hardware.

The decoder uses validated offsets and widths. Truncated numeric fields are
omitted and counted; missing identities or timestamps reject the record.
Data-only custom layouts can provide other documented field positions.

Home Assistant identifiers keep their historical technical prefix. Standard
sensor aliases preserve the current installation's power, frequency and
communication-board temperature readings. Measurement sensors keep their last
readings overnight. The freshness timestamp expires after 900 seconds. Buffered
records remain excluded from Home Assistant discovery publication; the other
outputs follow the configured time and buffered-record policy.

## Deliberate differences

The rewrite retains working behaviour without reproducing these observed faults:

- The old serial-relative compatibility path raises an exception after decoding.
  HA Growatt publishes the decoded result.
- Some SPA field names begin with digits. Their templates now use bracket access
  so Home Assistant can evaluate them.
- Server information and help pages describe HA Growatt. Register operations
  retain their observed request and response formats.
- The freshness sensor's visible label omits the previous project name. Its
  identity and expiry are unchanged.

The legacy environment variable for adding an inverter to a raw MQTT topic
retains its unusual behaviour: any non-empty string enables it, including
`False`. An INI boolean works normally. Separate meter topics have no inverter
suffix. See [configuration](configuration.md) for the complete settings.

Arbitrary third-party extensions can depend on private internals of the old
program. The public callback arguments and observed configuration/layout data
are supported; each additional extension still needs its own check. CSV meter
logs do not provide the inverter fields required by PVOutput.

## Installation qualification

A replacement release still requires native Home Assistant migration and
restart checks, including entity identity, history, statistics, full-profile
changes and broker reconnection. The live service remains in place until those installation checks pass.

After promotion, verify fresh readings from both inverters, continued cloud
forwarding and the existing Home Assistant entities. Keep the previous service
and its rollback material until verification completes. Archive the old fork
only after successful replacement.
