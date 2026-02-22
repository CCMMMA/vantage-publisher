# Vantage Publisher

`vantage-publisher` reads live data from a Davis Vantage Pro2 console (via `ser2net` TCP), enriches the payload with station metadata and optional AirLink data, stores samples to CSV, and publishes messages to MQTT.

The threaded publisher (`vantage-publisher-threading.py`) is aligned with the newer PyVantagePro streaming approach used in `examples/14_stream.py` while keeping compatibility with this repository's existing `config.json` and `parameters.json` files.

## Features

- Persistent station stream with automatic reconnect
- Per-sample field filtering through `parameters.json`
- CSV persistence under `pathStorage/YYYY/MM/YYYY-MM-DD.csv`
- MQTT publishing with offline store-and-forward (SQLite queue)
- GeoJSON MQTT payloads (`Feature` with `Point` geometry)
- Optional AirLink merge (cached on interval)
- Graceful shutdown on `SIGINT`/`SIGTERM`

## Requirements

- Python 3.8+
- Access to a Vantage Pro2 console exposed as `tcp:127.0.0.1:<usbPort>` (typically through `ser2net`)
- Optional MQTT broker

Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

## Configuration

### `config.json` (current format, still supported)

```json
{
  "uuid": "it.uniparthenope.meteo.ws1",
  "name": "Centro Direzionale",
  "lon": 14.2845,
  "lat": 40.8569,
  "usbPort": 22222,
  "usbPollInterval": 1.0,
  "delay": 10,
  "timeout": 60,
  "pathStorage": "/storage/vantage-pro/",
  "mqttBroker": "broker.example.org",
  "mqttPort": 1883,
  "mqttUser": "",
  "mqttPass": "",
  "mqttQos": 1,
  "offlineMaxMessages": 200000,
  "offlineMaxAgeSec": 604800,
  "airlinkIntervalSec": 300
}
```

Optional additional keys:

- `mqttKeepalive` (default `30`)
- `mqttReconnectSleep` (default `1.0`)
- `mqttSpoolFile` (default `<pathStorage>/mqtt_offline_queue.sqlite`)

### `parameters.json`

`parameters.json` is a map of station field name to boolean:

- `true`: include field
- `false`: exclude field

If the file is missing, all fields are included.

## Running

```bash
python3 vantage-publisher-threading.py
```

## MQTT payload format

Published payloads are GeoJSON Features:

```json
{
  "type": "Feature",
  "geometry": {
    "type": "Point",
    "coordinates": [14.2845, 40.8569]
  },
  "properties": {
    "Datetime": "2026-02-22T22:15:40Z",
    "TempOut": 12.7,
    "WindSpeed": 3,
    "uuid": "it.uniparthenope.meteo.ws1",
    "name": "Centro Direzionale"
  }
}
```

Topic: `uuid` from `config.json`.

## Storage layout

- CSV samples: `<pathStorage>/<YYYY>/<MM>/<YYYY-MM-DD>.csv`
- MQTT offline queue DB: `<pathStorage>/mqtt_offline_queue.sqlite` (or `mqttSpoolFile` if configured)

## Docker

Build:

```bash
docker build -t vantage-publisher .
```

Run:

```bash
docker run --rm -it \
  -v $(pwd)/config.json:/app/config.json:ro \
  -v $(pwd)/parameters.json:/app/parameters.json:ro \
  -v /path/to/storage:/storage \
  vantage-publisher \
  python3 vantage-publisher-threading.py
```

## Notes

- MQTT is disabled automatically when `mqttBroker` or `mqttPort` is missing.
- AirLink lookup is skipped when `mqttBroker` is empty.
- The process logs to stdout; optionally set `LOG_FILE` to also write rotated file logs.

## License

Apache-2.0
