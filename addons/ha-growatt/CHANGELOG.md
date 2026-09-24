# 0.8.0

The optional Home Assistant integration adds read-only serial RTU and UDP
Modbus connections, documented register profiles and an optional faster power
reading. The existing app route and MQTT entities keep their current setup.
The native Shine receiver shipped in 0.7.0 was checked with both owner-owned
inverters on DEV Home Assistant. No direct Modbus connection was available for
a physical check.

# 0.7.0

The optional Home Assistant integration can now offer supported settings and
output destinations without this app. It also has a separate read-only Modbus
TCP polling route for a suitable inverter or gateway. Existing app settings,
MQTT entities and cloud forwarding are unchanged.

# 0.6.0

The support page's private capture now includes a shareable structure report
for packet layouts that HA Growatt cannot decode. It describes packet shapes
and changes without publishing packet bytes, serials or readings. The existing
private replay remains separate.

The optional Home Assistant integration can now receive datalogger traffic
without this app or MQTT. Existing app installations continue to work as before.

# 0.5.0

The setup guide now links directly to the optional companion. Its setup form
checks for a fresh app status on Home Assistant's MQTT broker and explains what
to check if no status arrives. An old retained status does not count as a live
connection, and setup can still continue while the app is offline.

A separate command-line scanner reads bounded input and holding register ranges
from an explicitly named direct-Modbus TCP gateway. It is read-only, paced and
saves raw results as a private file. A direct-Modbus response does not prove the
same register works through a Shine datalogger. The compatibility matrix now
labels published 0xAHA hardware results as external evidence, and the hardware
report form asks owners to identify the software and connection used.

The support page now downloads shareable packet evidence without serials or
readings. A separate serial-redacted replay keeps known numeric measurements
for decoder investigations, with a clear warning that those readings remain
private. Both use the existing short-lived capture; the original private
replay is unchanged.

Added read-only hardware identification, bounded holding-register reads and
comparisons in the app and companion actions. No profiles or controls change
automatically. Register downloads are separate private reports. The README now
has a clearer installation path and distinguishes published and upcoming features.

Dataloggers now have separate Home Assistant devices linked to their inverters,
with connection state, last contact, observed upload interval and recent
reconnections. Optional logger model and firmware details are kept separate
from inverter firmware. Existing measurement entities and history are preserved.

Exact MIC TL-X and MIN TL-XH model names now restrict incompatible battery
controls. The support page explains controls waiting for readback and the
remaining experimental limits. Correcting a model cancels commands queued
under the previous selection without discarding saved readings.

The hardware matrix now records the owner's MIN 2500TL-XH, MIC 2000TL-X and two
ShineWiFi-X loggers. Logger firmware and physical battery-control support remain
unconfirmed. Battery controls are not physically qualified on this installation.

Fresh readings now have clock checks, readable operating states and supported
MIC fault descriptions. Stale overnight readings wait for new data. The support
page records packet-format changes and firmware-detail changes, with a separate,
explicitly private capture for offline replay. Existing redacted downloads remain
free of packet bodies.

Setting diagnostics distinguish confirmed cloud writes from unattributed readback
changes. They observe replies and subsequent reads without retrying writes or
changing the cloud-blocking default. Fault mappings and cloud-conflict scenarios
are tested synthetically; they are not new physical hardware qualifications.

# 0.4.0

The app now includes a setup guide for the broker, datalogger connection, first
readings, inverter profiles and Home Assistant entities. It checks the published
TCP port and waits for fresh readings from every expected inverter. Saved
readings after a restart do not complete setup, and changing a reading profile
requires a new reading and another review.

Browse the hardware catalogue in the app or the compatibility matrix in the
repository. Both distinguish checks on our installation from community reports
and protocol documentation. A hardware report form makes it easier to share
results from other installations.

Existing MQTT identities and history are preserved. Experimental battery controls stay
off by default; the matrix does not qualify additional
models or firmware for control. Update the app to use the new guide. The HACS
companion has no behaviour changes in this release.

# 0.3.2

Keep status messages running when an inverter setting disappears during a
refresh. The companion now waits until daylight and the sunrise grace period
before warning about missing status. Explicit app-offline messages still warn
at any time. Existing readings, entity identifiers and history are preserved.

# 0.3.0

Cloud forwarding recovers automatically after a successful protocol health check.
Local readings continue during an outage; command completion is respected before
asking the datalogger to reconnect.

The optional HACS companion adds daylight-aware Repairs, buffered-reading events
and previewed history adoption. It uses the existing MQTT connection and leaves
measurement identities intact.

App checks refresh automatically. Failed measurements, incomplete fields and
announcement warnings have separate counters, with a bounded redacted support
capture. Settings refresh is configurable. Exact model and firmware details can
be recorded on existing inverter devices, including a read-only firmware check.

# 0.2.1

Keep forwarding when Growatt acknowledges the connection but takes longer to
check the datalogger clock. A missing clock-setting command no longer causes
a false cloud outage. Genuine missing acknowledgements and disconnections
still switch to local fallback.

# 0.2.0

Readings recover after Home Assistant, MQTT or app restarts, including when the
inverter is asleep. Their original timestamps are kept; stale controls remain
unavailable.

The web UI adds connection checks, per-inverter profiles, redacted diagnostics
and a read-only history/Energy preview. MQTT service configuration can be
obtained automatically from Home Assistant. Existing explicit settings remain.

Cloud-failure fallback, connection diagnostics and documented controls are now
included. Experimental SPH/SPA schedules use grouped writes and read-back;
additional MIN TL-XH and MOD/MID TL3-XH controls require explicit profiles and
are disabled by default. The hardware guide records community confirmations,
known failures and the limits of our own testing.

# 0.1.1

Unitless numeric sensors keep their existing decimal strings, including State.

First release with cloud forwarding, MQTT discovery and the existing app options.
Existing sensor identifiers, units and history are retained. The app reads
Supervisor’s protected settings before running without root privileges.
