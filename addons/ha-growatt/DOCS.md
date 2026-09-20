# Using the app

With Mosquitto installed, leave credentials empty and keep `mqtt_auto` enabled
to use Home Assistant's MQTT service. Existing explicit broker settings are
preserved. For another broker, turn `mqtt_auto` off and enter its host, port,
username and password; enable `mqtt_tls` when required. With `ha_plugin` enabled,
HA Growatt publishes discovery and readings to the official MQTT integration.
Turn it off to use raw MQTT publication instead.

Keep the standard entity profile when moving an existing standard-profile
installation. The `all` profile discovers the other normally included fields.
Changing the profile does not change the full state JSON.

The defaults use server time, skip buffered readings and block configuration
commands other than the established clock-setting exception. `invtype=default`
allows the layout selector to choose a plausible family for each device.

Set the exposed TCP port to the port used by the datalogger. Stop the previous
service before binding that port. Keep its configuration and image until fresh
readings, existing entity history and Growatt cloud updates are verified.

Diagnostic logging includes packet contents. Keep it disabled during normal
operation and keep any support logs private. Normal overnight silence does not
make the service health check fail.

Open the web UI for connection checks, per-inverter profiles, a redacted
diagnostics download and a read-only history/Energy preview. Restart recovery
is enabled by default and preserves the last readings and their original
timestamps when HA, the broker or the app restarts without a fresh upload.

Experimental battery controls and schedules require an explicit matching
control profile and `experimental_controls: true`. Read the
[feature guide](https://github.com/Herbertmt978/HA-Growatt/blob/main/docs/home-assistant-features.md)
and [hardware evidence](https://github.com/Herbertmt978/HA-Growatt/blob/main/docs/hardware-support.md)
before enabling them. A profile choice does not establish firmware compatibility.

Cloud forwarding now recovers automatically after a valid cloud heartbeat and a
quiet period between commands. `cloud_recovery_seconds` defaults to 300; zero
disables probing. `settings_refresh_seconds` defaults to 300; zero makes settings
reads manual. Buffered records can produce events through the optional HA Growatt
companion integration, installed from this repository with HACS.

The web UI refreshes checks automatically, separates announcement warnings from
failed measurements and offers a bounded support capture without payloads. Enter
the inverter's exact model and use **Read firmware from inverter** where supported.
The companion adds native daylight-aware repair notices and guarded history
adoption. It uses the existing MQTT connection and does not duplicate sensors.

The app requests Supervisor access for its MQTT service settings and its own
profile configuration. Home Assistant API access is used only to read registry,
statistics and Energy information for the preview. It does not change HA history
or Energy settings. Ingress provides access through your existing HA login.
