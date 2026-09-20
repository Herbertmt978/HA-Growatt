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
- Tests for transport, register envelopes, discovery and delivery were written
  for the new implementation. Passing them does not prove untested modes or
  hardware compatibility.

The MIT licence applies to this project's implementation. Installed dependencies
retain their own licence notices. The current runtime dependency is
[Eclipse Paho MQTT](https://github.com/eclipse-paho/paho.mqtt.python).
