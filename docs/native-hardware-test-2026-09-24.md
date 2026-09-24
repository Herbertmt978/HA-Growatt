# Native Shine receiver hardware test — 24 September 2026

HA Growatt 0.7.0 received live uploads from the owner's MIN 2500TL-XH and
MIC 2000TL-X in a DEV Home Assistant instance. Each inverter uses a Growatt
ShineWiFi-X datalogger. The inverter firmware recorded by the existing
installation is AL1.0 for the MIN and GH1.0 for the MIC; datalogger firmware
has not been confirmed.

Both loggers remained configured to send to the production Home Assistant
address. For this test, production's app was briefly stopped and its TCP
Relay app passed those same connections to DEV. The relay did not decode or
alter the traffic. This tested the native receiver with physical logger
traffic without changing either logger's settings. It did not test a permanent
logger-to-DEV installation without the temporary relay.

DEV Home Assistant created a receiver device and two inverter devices: 73
entities in all, with 33 on each inverter. Both inverter devices showed
**Connected** and fresh power, voltage, current and energy values. Across the
session the receiver recorded 26 decoded readings, zero failed measurements,
zero incomplete fields and zero output failures. Its four packet warnings
were separate from measurement failures.

To check cloud fallback, outbound TCP from DEV to the resolved Growatt cloud
endpoint on port 5279 was blocked for two minutes. Home Assistant logged a
local-answer fallback for both datalogger sessions. Both inverter devices
remained connected and received new measurements during the block. The block
was removed automatically; both cloud sessions re-established, and a packet
check saw a reply from Growatt on each connection. No inverter setting was
written. We did not check the Growatt portal's presentation of data during
the outage.

Production's HA Growatt app was restarted, and its support page again showed
both dataloggers on cloud connections with recent uploads. The relay was
stopped and its original destination restored. This was a receiver and
forwarding qualification, not a direct Modbus or battery-control test.
