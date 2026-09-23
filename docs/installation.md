# Installation and migration

Use the versioned packages from the [release page](https://github.com/Herbertmt978/HA-Growatt/releases).
Keep a recoverable copy of an existing service until the replacement is receiving
fresh readings from every inverter.

## Docker

Copy `compose.yaml` and `examples/ha-growatt.ini` into a private service directory.
Use the release's image tag in Compose and configure the MQTT connection. The
container runs as user and group 10001. Its configuration must be readable by
that account; an existing service's supplementary configuration group can be
retained with Compose `group_add`.

The supplied profile has a read-only root filesystem, a small temporary
filesystem, no Linux capabilities and no privilege escalation. Only the
datalogger port is published. The configuration is mounted read-only at
`/app/config/ha-growatt.ini`. `HA_GROWATT_CONFIG` can select another mounted path.

```sh
docker compose config --quiet
docker compose up -d
docker compose ps
docker compose logs --tail 50
```

For server mode, set `mode=server`, select the listener port and publish it.
Publish port 5782 only on the intended management interface and set `api_host`
inside the container accordingly. Keep the API away from public networks.

Sniffer mode needs Linux, access to an interface carrying the datalogger's
traffic and `NET_RAW`. Use a separate capture deployment with the required
network namespace; do not grant capture privileges to an ordinary proxy.
Writable CSV or log output needs a dedicated writable mount.

## Home Assistant app

Add this repository to the app store:

```text
https://github.com/Herbertmt978/HA-Growatt
```

Install HA Growatt, enter the MQTT broker details and keep the intended host
port. The app reads Supervisor’s protected options before dropping to user
and group 10001. Network services run under that account. It needs no Home Assistant
API token, Docker access or host networking. The app supports the current
Home Assistant architectures, amd64 and aarch64. Standalone Docker packaging
also targets arm/v7 and 386.

The app remains a proxy. `ha_plugin` selects Home Assistant discovery or raw
MQTT. See [configuration](configuration.md) for the profile, timestamp and
layout options.

## Move an existing installation

Keep a recoverable copy of the current image, configuration, environment
overrides, port mapping and Compose file. Back up Home Assistant and preserve
the existing MQTT discovery records before changing the service.

Test the replacement with a separate broker and synthetic device identities.
For an existing installation, compare discovered sensor identifiers, units,
state classes and values before and after replacement. Include broker
reconnection, Home Assistant restart and any discovery-profile change that
the installation uses.

At cutover, stop the old service and start HA Growatt on the same host port.
Only one service should own the datalogger connection. Retain the effective
configuration, including environment overrides: the INI file alone may not
describe the running setup. Existing `grottext.ha` configuration selects the
new built-in publisher.

Confirm fresh data from every inverter, continued cloud forwarding and the
existing Home Assistant entities and history. A healthy container alone does
not prove those checks. Restore the saved service if any required check fails.
Archive the previous repository only after the replacement is qualified and
the production installation is verified.

## Image qualification

Build the Dockerfile from the repository root. It pins the Python base image,
runtime dependencies and wheel builder. The following probe runs against the
installed package with only test fixtures mounted:

```sh
docker run --rm --read-only --cap-drop ALL --security-opt no-new-privileges \
  --tmpfs /tmp:rw,noexec,nosuid,size=32m,mode=1777 \
  -v "$PWD/tests:/tests:ro" --entrypoint python ha-growatt:test /tests/container_probe.py
```

Run it for each supported Docker platform. Test passive capture separately on
Linux as root with only `NET_RAW`, using the probe's `--sniff` option. The probe checks
installed profile data, real proxy/server sockets, passive health and SIGTERM
shutdown. Native broker and Home Assistant migration checks are additional
release requirements. The `--supervisor` probe also checks root-owned private
options and the privilege drop. Run that harness as root with writable `/data`
and `CHOWN`, `SETUID`, `SETGID` and `KILL`; the last capability lets the parent
test signal the child after its user changes. The service itself has no
effective capabilities.
