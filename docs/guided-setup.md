# Set up HA Growatt in Home Assistant

Install the app from this repository, then open its web UI for the setup guide.
The guide checks the actual listener, broker and inverter feeds. It leaves the
configuration in the existing app options and per-inverter forms.

Before installing, check the [hardware matrix](hardware-matrix.md). A working
result from another project is useful evidence, but does not verify HA Growatt
on that model, firmware or connection. The 0xAHA results in the matrix use direct
Modbus RTU/TCP; HA Growatt's app receives Shine datalogger TCP traffic. Look for
an HA Growatt result on your exact hardware before treating it as confirmed.

## Before the app starts

1. In Home Assistant's app store, add `https://github.com/Herbertmt978/HA-Growatt`
   to the repositories and install **HA Growatt**.
2. Install and start **Mosquitto broker**, or prepare your existing MQTT broker.
   Add the **MQTT** integration under Settings → Devices & services and connect
   it to the same broker.
3. For Mosquitto, keep `mqtt_auto` enabled and leave the app's MQTT username and
   password empty. Explicit broker credentials take priority. For an external
   broker, configure its address, credentials and TLS as required.
4. Keep `ha_plugin` and `ha_features` enabled for discovery and the setup checks.
   Leave experimental controls disabled. Publish the datalogger TCP port in the
   app's Network settings and start the app.
5. Open the web UI. If the app cannot start, read its Log tab: automatic MQTT
   configuration needs a working Supervisor MQTT service. The web guide cannot
   run before the app starts.

If moving an existing service, first save its configuration and record the
broker, MQTT topics and discovery profile. Stop that service before HA Growatt
binds the same port. See [migration](installation.md) for the existing formats.

## Follow the guide

- **Broker:** confirm the app connects and discovery is enabled. A connected app
  does not prove Home Assistant uses the same broker; check its MQTT entities too.
- **Datalogger:** set the upload server to Home Assistant's stable LAN address
  and the published TCP port shown by the guide. If no mapping is available,
  check Network settings; do not substitute the ingress URL or container address.
  Use the configuration instructions for your exact ShineLAN/LAN-X or ShineWiFi
  variant and firmware. Do not flash a logger just to follow this guide.
- **First readings:** enter the expected number of inverters and wait for fresh
  readings from all of them. Saved readings do not pass. Solar-only inverters
  may stay silent overnight. Compare power and energy with a known reading.
- **Profiles:** leave Automatic selected while readings are correct. Review each
  inverter separately and enter its exact model. Read firmware where supported
  or leave it unknown. A reading profile is not qualification for battery controls.
- **History:** use the existing read-only preview and check Recorder history and
  Energy coverage. Do not add component totals if another selected sensor already
  includes them. Power in W/kW is not accumulated energy in kWh.
- **Finish:** confirm the measurement entities in Home Assistant's MQTT integration.
  The optional HACS companion adds Repairs and history tools; the app still runs
  the datalogger connection. It adds no duplicate measurement sensors. Use the
  guide's **Open HA Growatt in HACS** link, restart Home Assistant after the
  download, then select **Add the companion integration**. Its setup form checks
  for a fresh app status on Home Assistant's MQTT broker. If none arrives,
  check the app and broker settings; the companion can still be added while
  the app is restarting.

Steps remain accessible while readings are pending. Acknowledgements are kept
only for the current page session. The guide cannot certify hardware, confirm
physical battery behaviour or automatically inspect your dashboard layout.

## If a check does not pass

| Check | What to inspect |
| --- | --- |
| Broker disconnected | Mosquitto state, app log, broker address and credentials. |
| No packets | Daylight, logger upload destination, published port, LAN reachability and conflicting services. |
| Packets but no fresh readings | Profile and decode warnings. Session-key encrypted Shine traffic is unsupported. |
| Fewer feeds than expected | Each datalogger's destination and whether the missing inverter is powered. |
| More feeds than expected | The expected count and retained device records; do not treat an unexplained extra feed as a successful install. |
| App connected but no HA entities | The HA MQTT integration's broker and discovery settings, and `ha_plugin` in the app. |
| No device checks | Enable `ha_features`; raw-MQTT-only operation does not provide the guide's device evidence. |
| Connection checks unavailable | Reload the page and check the app log; an old successful check is not retained as success. |

Download redacted diagnostics from the web UI for support. The
[hardware report form](https://github.com/Herbertmt978/HA-Growatt/issues/new?template=hardware-report.yml)
asks for model, firmware, datalogger and separate telemetry/control results.
You do not need to change inverter settings to report working telemetry.
