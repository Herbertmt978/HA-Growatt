# Working on HA Growatt

Preserve the behaviour of the current working service. Check decoding, sensor
names and identities, units, timestamps, MQTT messages and forwarding against
recorded behaviour. Keep missing compatibility work visible. Passing unit tests
alone does not make the replacement ready for an existing installation.

Write in plain British English. Keep the README, help text, comments and release
notes natural and easy to follow. Do not use a repeated problem, action, reason
and benefit format. Explain details where they are needed, without a sales pitch.

Write new implementation code. Use protocol documentation and observed inputs
and outputs to establish compatibility. Keep real captures and credentials out
of Git. Preserve existing Home Assistant identifiers and history.

Run the checks listed in the README before publishing code. Deployment and
archiving the old repository follow complete compatibility and installation
checks; a development commit is not a release.
