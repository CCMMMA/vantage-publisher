# Vantage Publisher

`vantage-publisher.py` reads live data from a Davis Vantage Pro2 console, optionally stores CSV rows locally, optionally publishes MQTT packets, and optionally sends Signal K deltas via websocket.

## Features

- Continuous station stream with reconnect
- Parameter filtering via `parameters.json`
- Optional local CSV storage
- Optional MQTT publishing with offline queue
- Optional direct Signal K websocket publishing
- Dry run mode for configuration/debug checks
- Built-in HTTP server for browsing stored CSV files (optional basic auth)

## Requirements

- Python 3.8+
- Vantage Pro2 reachable as `tcp:127.0.0.1:<usbPort>` (typically through `ser2net`)

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Configuration (`config.json`)

```json
{
  "uuid": "it.uniparthenope.meteo.ws1",
  "name": "Centro Direzionale",
  "lon": 14.2845,
  "lat": 40.8569,

  "storage": true,
  "mqtt": false,
  "signalk": false,

  "usbPort": 22222,
  "usbPollInterval": 1.0,
  "delay": 10,
  "timeout": 60,

  "pathStorage": "/storage/vantage-pro/",

  "mqttBroker": "mqtt-broker.local",
  "mqttPort": 1883,
  "mqttUser": "",
  "mqttPass": "",
  "mqttQos": 1,
  "mqttFormat": "flat",

  "signalkServerUrl": "ws://signalk.local:3000/signalk/v1/stream",
  "signalkToken": "",
  "signalkContext": "meteo.it.uniparthenope.meteo.ws1",
  "signalkPathMap": {},

  "httpEnabled": false,
  "httpHost": "0.0.0.0",
  "httpPort": 8080,
  "httpUser": "",
  "httpPass": "",
  "httpRoot": "/storage/vantage-pro/",

  "offlineMaxMessages": 200000,
  "offlineMaxAgeSec": 604800,
  "airlinkIntervalSec": 300
}
```

### Key runtime booleans

- `storage`: enable/disable local CSV storage (default: `true`)
- `mqtt`: enable/disable MQTT publishing (default: `false`)
- `signalk`: enable/disable Signal K websocket publishing (default: `false`)

`mqttFormat` supports:

- `flat` (default/fallback)
- `geojson`

## Parameters file (`parameters.json`)

Boolean map of station fields:

- `true`: include field
- `false`: exclude field

If file is missing, all fields are included.

## Command line options

- `--config <path>` config file path (default `config.json`)
- `--parameters <path>` parameters file path (default `parameters.json`)
- `--signalk true|false` override config `signalk`
- `--mqtt true|false` override config `mqtt`
- `--storage true|false` override config `storage`
- `--dry` dry mode (no storage, no MQTT/Signal K/http connections; packets/rows logged only)

### Usage examples

```bash
# Use config defaults
python3 vantage-publisher.py

# Enable MQTT and storage explicitly
python3 vantage-publisher.py --mqtt true --storage true

# Enable Signal K direct websocket together with MQTT and storage
python3 vantage-publisher.py --signalk true --mqtt true --storage true

# Dry mode validation (no publish/store/connect)
python3 vantage-publisher.py --dry

# Custom config + parameters
python3 vantage-publisher.py \
  --config /etc/vantage/config.json \
  --parameters /etc/vantage/parameters.json
```

## Dry mode behavior

When `--dry` is active:

- MQTT connection/publish is disabled
- Signal K websocket connection/publish is disabled
- local CSV writes are disabled
- HTTP storage server is disabled
- generated outputs are logged:
  - `CSV_ROW;...`
  - `MQTT_PACKET;...`
  - `SIGNALK_UPDATE;...`

## HTTP server for local storage

If `httpEnabled` is `true`, the app starts an HTTP server exposing `httpRoot`.

Configuration keys:

- `httpEnabled` (`true|false`)
- `httpHost` (default `0.0.0.0`)
- `httpPort` (default `8080`)
- `httpRoot` directory to serve (default `pathStorage`)
- `httpUser` optional basic auth username
- `httpPass` optional basic auth password

Authentication behavior:

- if `httpUser` is empty, no authentication is required
- if `httpUser` is set, HTTP Basic Auth is required

## MQTT payloads

### `flat`

```json
{
  "Datetime": "2026-02-24T10:15:40Z",
  "TempOut": 12.7,
  "WindSpeed": 3,
  "position": { "latitude": 40.8569, "longitude": 14.2845 },
  "name": "Centro Direzionale"
}
```

### `geojson`

```json
{
  "type": "Feature",
  "geometry": {
    "type": "Point",
    "coordinates": [14.2845, 40.8569]
  },
  "properties": {
    "Datetime": "2026-02-24T10:15:40Z",
    "TempOut": 12.7,
    "uuid": "it.uniparthenope.meteo.ws1",
    "name": "Centro Direzionale"
  }
}
```

MQTT topic is always `uuid`.

## Signal K deltas

When Signal K is enabled (`signalk=true` or `--signalk true`), the publisher sends deltas with:

- `context`: `signalkContext` (default `meteo.<uuid>`)
- `navigation.position`: station lat/lon
- remaining fields:
  - from `signalkPathMap` if present
  - otherwise standard mappings for common weather keys
  - otherwise fallback to `environment.<field>`

## Storage layout

- CSV files: `<pathStorage>/<YYYY>/<MM>/<YYYY-MM-DD>.csv`
- MQTT offline queue DB: `<pathStorage>/mqtt_offline_queue.sqlite` (or `mqttSpoolFile`)

## License

Apache-2.0
