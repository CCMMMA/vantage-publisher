# Installation and deployment

[Documentation index](README.md)

## Deployment assumptions

The supplied container configuration targets a Linux host with a local serial bridge and Docker host networking. The application's loopback address must refer to the network namespace containing that bridge. A default bridged container or a desktop virtualization environment can violate this assumption; validate connectivity in the actual runtime namespace.

The root README declares Python 3.8+, and the Dockerfile selects `python:3.8-slim`. These repository declarations are not a tested compatibility matrix for every present dependency release. Requirements are unpinned, including the Git-sourced PyVantagePro dependency. The runtime uses `mqtt.CallbackAPIVersion.VERSION2`, so an installation of the older Paho callback interface is insufficient. Record and validate the resolved environment before deploying it to an unattended station.

## Native installation

Create an isolated environment and install the repository requirements:

```bash
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements.txt
```

Git access is needed for the PyVantagePro requirement. Installation requires network access and a compatible dependency set. The archive collector and live publisher both depend on PyVantagePro; the live program also imports Requests and Paho at module import time even when their outputs are disabled.

For a new installation, copy the samples only if the destination files do not already contain station configuration:

```bash
cp -n config.json.sample config.json
cp -n parameters.json.sample parameters.json
```

Edit the identity, coordinates, serial bridge port, storage root, and selected fields. Begin with remote outputs disabled. Confirm JSON syntax without opening a station connection:

```bash
python3 -m json.tool config.json > /dev/null
python3 -m json.tool parameters.json > /dev/null
```

These commands check syntax only, not value ranges or service reachability. Then run a live dry check:

```bash
python3 vantage-publisher.py --config config.json --parameters parameters.json --dry
```

This command connects to the console and runs until interrupted. It requires station data before output examples appear. Inspect `CSV_ROW`, `MQTT_PACKET`, and `SIGNALK_UPDATE`, then stop with Ctrl+C. Enable the desired outputs for the operational run:

```bash
python3 vantage-publisher.py --config config.json --parameters parameters.json
```

## Serial bridge commissioning

The weather connection in [`util/ser2net.yaml`](../util/ser2net.yaml) maps TCP port 22222 to `/dev/ttyUSB0` at `19200n81,local`. Verify the device path and console serial settings on the target host. The file also contains unrelated example serial connections; install only the intended connections rather than assuming the full file is a minimal station configuration.

The weather connection enables `kickolduser`, so another connection can displace an active client. Avoid simultaneous console access by the live publisher, archive collector, and diagnostic tools. Device numbering can change after reconnects; select a stable device path when the host provides one.

The supplied [`util/ser2net.service`](../util/ser2net.service) references `CONFFILE` from an optional `/etc/default/ser2net` environment file. Set it to the intended YAML path if using this unit. The repository does not install or enable the unit automatically. Use the target system's ser2net manual to validate syntax against the installed version.

## Container deployment

Build the image, verify the Compose model, and start only the publisher service:

```bash
make build
docker compose config
docker compose up -d vantage-publisher
docker compose logs --tail=100 -f vantage-publisher
```

The Compose service uses an existing image rather than a `build:` directive, so the explicit build step is required. Its configuration mounts are under `/vantage-publisher`, and the host's `/storage` directory is mounted at `/storage` in the container. Create appropriate host storage permissions for the effective container user before startup.

The Dockerfile creates a `weather` account but does not select it with `USER`; the image therefore does not automatically run as that account. If deploying with an explicit non-root user, verify access to storage, configuration token persistence, and the configured log path. Compose's `LOG_FILE=/var/log/vantage.log` is inside the container and is not separately bind-mounted for durable log retention.

`make run` provides a smaller launch command with configuration mounts and host networking, but no persistent storage mount or restart policy. It is not equivalent to the Compose service for long-term storage. The Docker image copies only the live publisher and AirLink module; `collect-history.py` is not available in that image by default.

## Included maintenance helpers

The Compose file also contains a `restarter` service. Its command refers to `docker`, a fixed container name, and a scheduled time, but the service definition does not install a Docker CLI or guarantee that container name. Its shell dollar escaping is also inconsistent, and its loop exits after the scheduled attempt. Treat it as an unvalidated helper rather than a reliable daily restart facility. The commissioning command above deliberately starts only `vantage-publisher`.

[`updater.sh`](../updater.sh) stops Compose, pulls Git changes, rebuilds, and starts services using a mixture of legacy and current Compose commands. It has no explicit fail-fast or rollback mechanism. Use the controlled [upgrade procedure](operations.md#upgrade-and-rollback) for production changes. The bundled `get-docker.sh` is a host provisioning helper, not an application prerequisite that should be rerun during every update.

## Network and credential boundaries

MQTT is configured as plain TCP by this application; HTTP file browsing is also plain HTTP. Signal K can use `wss://`, and the AirLink endpoint uses HTTPS. Choose the network boundary accordingly, especially when passwords or access tokens are involved. The token is placed in a websocket query string, and the AirLink API key is placed in an HTTP query string; proxy and exception logs can consequently contain credential-bearing URLs.

Use `httpHost=127.0.0.1` when file browsing should be reachable only through a local access layer. A nonempty `httpUser` activates authentication but does not add encryption. Set `httpRoot` to the narrowest intended download directory, keeping configuration and the queue database outside it where practical. Backups containing tokens or broker credentials need the same access restrictions as the original configuration.

## Commissioning acceptance

A complete deployment check should establish that the reader connects, the expected fields have credible values and units, a CSV row appears at the documented path, and each enabled remote consumer receives the expected station identity. For MQTT, briefly interrupt broker connectivity in a controlled environment and verify both queue growth and subsequent delivery. For Signal K, validate context acceptance, path meaning, converted values, and token persistence independently.

The repository's offline tests support implementation confidence, but they do not replace these observations. Record the results and the exact deployment revision so that later changes can be compared against a known operational baseline.
