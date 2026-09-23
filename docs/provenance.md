# Implementation and test provenance

The Python implementation in `src/ha_growatt` was written for this repository.
It uses a new asynchronous relay, immutable frame representation, bounded stream
processing, explicit scalar decoder, discovery generator and MQTT publisher.
The project does not import, vendor, rename or translate the predecessor runtime.

Compatibility evidence is treated separately from implementation source:

- Wire framing and register meaning are checked against protocol observations
  and manufacturer documentation linked in [protocol evidence](protocol.md).
- The scalar descriptors in `wire_profiles.json` record input byte positions,
  widths and signedness determined by controlled input/output experiments.
  The experiment changes one input byte at a time, then separately checks signed
  interpretation. These are observed interface facts, not copied layout source files.
- `tests/fixtures/telemetry_cases.json` contains newly generated synthetic packet
  inputs and independently recorded expected scalar outputs. All device
  identifiers in these fixtures are artificial. The tests compare the new decoder
  with those recorded results; they do not require the previous implementation.
- Discovery identity and metadata were checked against read-only broker
  observations and controlled reference publications. Field source mappings
  were identified by changing input values and observing rendered sensor states.
  New templates implement those mappings. Real device records and credentials
  are excluded from the repository.
- Command-policy fixtures record observed forwarding decisions for synthetic
  function codes and configuration requests. They contain no predecessor code.
- Family-selection fixtures record scores, selected layouts and output checks
  from controlled calls to the working version. Configuration fixtures record
  effective settings for synthetic INI files and environment overrides.
  The new selector and configuration loader were written from those observations.
- Publication fixtures capture the existing proxy's handling of announcement,
  telemetry and buffered packets, with external delivery replaced by a recorder.
- Server command and acknowledgement fixtures record loopback observations for
  protocols 2, 5 and 6. Optional output fixtures record synthetic MQTT, PVOutput,
  InfluxDB, CSV and HTTP results. No production account is used by those tests.
- Rendered sensor strings and battery-label cases are recorded from synthetic
  values. The discovery generator uses observed number-format flags and label
  mappings; the fixtures contain outputs, not predecessor templates.
- Tests for transport, register envelopes, discovery and delivery were written
  for the new implementation. Passing them does not prove untested modes or
  hardware compatibility.

The MIT licence applies to this project's implementation. Installed dependencies
retain their own licence notices. Runtime dependencies are
[Eclipse Paho MQTT](https://github.com/eclipse-paho/paho.mqtt.python),
[Python tzdata](https://github.com/python/tzdata) and
[websockets](https://github.com/python-websockets/websockets).

The app icon depicts a solar inverter and uses the
[Growatt wordmark](https://en.growatt.com/) to identify the supported hardware.
The Growatt name and wordmark belong to Growatt. HA Growatt is an independent
project.

The guided installation follows useful patterns reviewed in
[FezVrasta's configuration flow](https://github.com/FezVrasta/growatt-datalogger/blob/main/custom_components/growatt_datalogger/config_flow.py):
discover devices before offering individual profiles, preserve existing options
and check connection readiness. Hardware evidence also uses the model-specific
reporting approach in [Growatt ModbusTCP](https://github.com/0xAHA/Growatt_ModbusTCP).
These projects provide a head start on behaviour and evidence, rather than code
to import into the app's different transport and web UI. No upstream implementation
was copied for this feature. The matrix links each external report and records
its connection and qualification limits.

The direct Modbus polling profiles were written for HA Growatt from Growatt's
[Protocol II V1.24](https://www.amosplanet.org/wp-content/uploads/2023/06/Growatt-Inverter-Modbus-RTU-Protocol_II-V1_24-English.pdf)
and [PV V3.14](https://www.amosplanet.org/wp-content/uploads/2023/07/Growatt-PV-Inverter-Modbus-RS485-RTU-Protocol-V3-14.pdf)
input-register tables. They reuse this project's existing read-only Modbus TCP
scanner and do not contain code from another integration. The separation
between MIC and MIN layouts matters: the same register number can mean a
different quantity on the two devices. The direct receiver has synthetic TCP
gateway tests, but no claimed physical Modbus qualification for the owner's
ShineWiFi-X devices.
