# Using the app

Version 0.5.0 adds clock, operating-state, main-fault and setting-change
sensors. The support page explains packet-format changes and offers a separate
private replay capture when redacted summaries are insufficient. See the
[diagnostic guide](https://github.com/Herbertmt978/HA-Growatt/blob/main/docs/home-assistant-features.md#reading-and-setting-diagnostics-050)
for time references, supported mappings, capture limits and evidence requirements.

Open the web UI and follow **Set up HA Growatt** for broker, datalogger, fresh
readings, profile and history checks. Install and start the MQTT broker before
starting the app. The [installation guide](https://github.com/Herbertmt978/HA-Growatt/blob/main/docs/guided-setup.md)
covers these prerequisites. Search **Hardware compatibility** in the web UI or
read the [published matrix](https://github.com/Herbertmt978/HA-Growatt/blob/main/docs/hardware-matrix.md)
to check the evidence for a model and connection.

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

## Datalogger devices and model capabilities

With HA features enabled in proxy mode, each observed datalogger has its own
Home Assistant device. Existing inverter devices link through that logger;
measurement entity identifiers, topics and history stay unchanged. Connections
and heartbeats are tracked even when no inverter measurement can be decoded.

Logger diagnostics show the cloud/local/disconnected connection, last contact,
observed upload interval and reconnections since the service started. The last
ten reconnection times appear in the reconnect sensor attributes and the web
UI. The interval needs two fresh readings from the same inverter on the same
connection. Gaps over an hour and reconnections reset it; it is not a readback
of the configured upload interval. Restored readings can restore the device
link, but cannot make the logger appear connected or create a new contact time.

Add confirmed logger details in the app configuration, then restart the app:

```yaml
dataloggers:
  - serial: LOGGER0001
    model: ShineWiFi-X
    firmware: ""
```

Replace the example serial with the logger shown in the web UI. Leave unknown
firmware blank. Inverter firmware is separate and is never reused as logger
firmware. Python configurations accept a `runtime.dataloggers` mapping; INI
uses a JSON mapping in `Generic.dataloggers`, or `HA_GROWATT_DATALOGGERS`.
No firmware or model is inferred solely from a logger serial.

The exact inverter model now constrains the existing control profiles:

- MIC TL-X: output limit only, subject to successful readback; no battery controls.
- MIN TL-XH: no SPH schedules or MOD battery registers. The existing MIN battery
  controls still require the MIN TL-XH control profile, compatible decoded
  records, experimental controls enabled and successful readback. Battery
  hardware and writes remain physically unverified.
- Other or unknown model names: retain the existing profile-based behaviour,
  with the model marked unconfirmed. This is not a claim of hardware support.

Version 0.5.0 includes **Identify hardware and inspect registers** under each
inverter. Identification never changes a profile automatically. Bounded register
reads, comparisons and private exports are explained in the
[register tools guide](../../docs/register-tools.md).

Enter exact model names, such as `MIN 2500TL-XH` or `MIC 2000TL-X`, in the web UI.
The decoder remains independent: keep a working Automatic reading profile.
A MIN model name does not authorise MIN register access through a MOD record.
The support page explains disabled controls, disconnected loggers and settings
waiting for readback. Correcting a model clears cached control values and
rejects queued commands from the old selection, without clearing readings.

The support page also offers shareable packet evidence and a serial-redacted
replay from an explicitly started private capture. The first contains no
readings; the second retains numeric measurements. See the
[capture guide](../../docs/home-assistant-features.md#shareable-packet-evidence-and-private-replay)
before attaching either file to a report.
