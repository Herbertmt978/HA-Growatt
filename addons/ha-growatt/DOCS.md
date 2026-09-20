# Using the app

Enter the MQTT host, port, username and password. With `ha_plugin` enabled,
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
