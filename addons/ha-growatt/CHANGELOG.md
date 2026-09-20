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
