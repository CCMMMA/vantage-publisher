# Configuration reference

[Documentation index](README.md)

## Interpretation and precedence

The live publisher reads `config.json` and `parameters.json` from the working directory unless their paths are supplied on the command line. Paths inside configuration are also interpreted relative to the process working directory, not relative to the configuration file. Use absolute storage paths for service deployments where the working directory may change.

The effective output flags follow this order: a command-line boolean overrides its configuration value; otherwise the configuration value applies; otherwise the code default applies. `--dry` subsequently disables output side effects and AirLink access. Numeric conversion and other normalization happen before these overrides, so a malformed numeric setting can still stop a dry run or a run with that output disabled.

Use a JSON object with native JSON numbers and booleans. The code converts numeric fields with `int()` or `float()` but does not comprehensively validate ranges, finiteness, identity strings, or cross-field consistency. The admissible values described below are operational requirements, not claims that every invalid value produces a tailored error.

## Identity, acquisition, and storage

| Key | Expected value | Code default | Meaning |
| --- | --- | --- | --- |
| `uuid` | Nonempty string | Required | MQTT topic, CSV station directory and filename prefix, default context identity |
| `name` | String | Required | Human-readable station name in CSV and MQTT data |
| `lat` | Finite number, −90 to 90 | Required | Station latitude in degrees |
| `lon` | Finite number, −180 to 180 | Required | Station longitude in degrees |
| `storage` | Boolean | `true` | Enable CSV writes |
| `pathStorage` | Directory path | Empty | CSV root; an empty value skips CSV writes |
| `usbPort` | TCP port, 1–65535 | `22222` | Port on `127.0.0.1` used for console access |
| `usbPollInterval` | Positive seconds | `1.0` | Wait following each successful reader operation |
| `delay` | Positive seconds | `usbPollInterval` if supplied, otherwise `2.0` | Wait after main-loop work |
| `timeout` | Positive seconds | `10` | Console timeout and Signal K HTTP/websocket operation timeout |

The sample sets `delay=10` and `timeout=60`; those values are examples rather than fallback defaults. Choose a `uuid` that is safe both as a single filesystem component and as a literal MQTT publication topic. In particular, do not include path separators or MQTT wildcards. This constraint is not enforced centrally by the publisher.

## MQTT and persistent queue

| Key | Expected value | Code default | Meaning |
| --- | --- | --- | --- |
| `mqtt` | Boolean | `false` | Request MQTT output |
| `mqttBroker` | Hostname or IP address | Unset | Broker host; not a URL |
| `mqttPort` | TCP port | `1883` | Broker port |
| `mqttUser` | String | Unset | Optional username; an empty value skips authentication setup |
| `mqttPass` | String | Unset | Password supplied with a configured username |
| `mqttQos` | Integer `0`, `1`, or `2` | `1` | Delivery quality passed to Paho |
| `mqttFormat` | `flat` or `geojson` | `flat` | Packet representation; unknown values fall back to `flat` |
| `mqttKeepalive` | Integer seconds | `30` | Paho keepalive argument |
| `mqttReconnectSleep` | Number | `1.0` | Parsed for compatibility but not used by the runtime |
| `mqttSpoolFile` | File path | `<pathStorage>/mqtt_offline_queue.sqlite`, or `./mqtt_offline_queue.sqlite` without a storage root | Persistent queue database |
| `offlineMaxMessages` | Positive integer | `200000` | Retention cap in queue records |
| `offlineMaxAgeSec` | Positive integer seconds | `604800` | Maximum queued age, measured from insertion |

MQTT requires a nonempty host and a truthy port to pass the runtime's completeness check; this check is not full connection validation. Paho reconnection delay is configured in code from one to thirty seconds. Changing `mqttReconnectSleep` has no effect. Enabling MQTT can create a database even when `storage=false`, because CSV and queue persistence are separate facilities. Zero or negative retention values must not be used as a supposed unlimited setting; the pruning code gives them destructive retention semantics.

## Signal K

| Key | Expected value | Code default | Meaning |
| --- | --- | --- | --- |
| `signalk` | Boolean | `false` | Request direct websocket publishing |
| `signalkServerUrl` | `ws://` or `wss://` stream URL | Empty | Server stream endpoint; required to activate the transport |
| `signalkToken` | String | Empty | Initial token, with automatic acquisition attempted when needed |
| `signalkContext` | String | `meteo.<uuid>` | Context attached to every delta |
| `signalkPathMap` | Object mapping station keys to paths | `{}` | Overrides destination paths; does not define conversion formulas |

Server acceptance of the configured context and paths must be verified. A name accepted by this program is not automatically valid for every Signal K deployment. Access checks normally run at sixty-second intervals, with access-request submission retries at three hundred seconds; these intervals are constructor defaults, not JSON settings exposed by `main()`.

## AirLink

| Key | Expected value | Code default | Meaning |
| --- | --- | --- | --- |
| `airlinkId` | WeatherLink identifier string | Empty | Identifier interpolated into the current-conditions API path |
| `airlinkApiKey` | String | Empty | API key sent in the URL query |
| `airlinkApiSecret` | String | Empty | Secret sent in `X-Api-Secret` |
| `airlinkIntervalSec` | Positive integer seconds | `300` | Refresh interval used by the main-loop cache |

A nonempty identifier enables attempted retrieval outside dry mode. Both credentials are needed by `airlinkData`; absent credentials yield a warning and empty data. The request timeout is fixed at ten seconds in `airlink.py`, independently of `timeout`.

## HTTP file service

| Key | Expected value | Code default | Meaning |
| --- | --- | --- | --- |
| `httpEnabled` | Boolean | `false` | Start the embedded file server |
| `httpHost` | Bind address | `0.0.0.0` | Network interfaces on which the server listens |
| `httpPort` | TCP port | `8080` | HTTP listener port |
| `httpRoot` | Directory path | `pathStorage`, or `.` when storage root is empty | Entire directory tree exposed by the handler |
| `httpUser` | String | Empty | A nonempty value enables HTTP Basic authentication |
| `httpPass` | String | Empty | Password compared with the supplied username |

The server can be enabled independently of CSV generation. An explicitly configured `httpRoot` takes precedence; the program does not restrict the service to CSV extensions. See [deployment boundaries](deployment.md#network-and-credential-boundaries).

## Parameter selection

[`parameters.json.sample`](../parameters.json.sample) lists common weather variables, alarms, and auxiliary channels. Its meaning is an allowlist when the file exists: true entries are retained, while false or absent entries are omitted. If the file is missing, all console fields are retained. An empty object therefore differs substantially from a missing file: it filters out every console field, leaving no nonempty observation to advance the live reader sequence.

Use literal `true` and `false`. Both parameter loaders currently apply Python truth-value conversion; a string such as `"false"` is nonempty and therefore enables a field. The more permissive boolean parser for the main configuration flags is a different mechanism and should not be assumed to apply here.

Selection occurs before publisher metadata and AirLink values are added. It cannot suppress the generated `Datetime`, `position`, or `name`, or the subsequently merged AirLink fields. Disabling console `Datetime` prevents preservation of that field as `DatetimeWS`. Archive collection applies its own filtering rule and always retains an existing `Datetime`.

## Command-line and logging reference

| Live option | Default | Purpose |
| --- | --- | --- |
| `--config PATH` | `config.json` | Configuration file |
| `--parameters PATH` | `parameters.json` | Selection file |
| `--storage true\|false` | No override | CSV enable override |
| `--mqtt true\|false` | No override | MQTT enable override |
| `--signalk true\|false` | No override | Signal K enable override |
| `--dry` | Off | Log generated output without output transports or CSV/queue writes |
| `-h`, `--help` | — | Usage text |

Output-flag parsers also accept case-insensitive `1/0`, `yes/no`, and `on/off`. Invalid configuration flag values produce a warning and use the default; invalid command-line boolean arguments are rejected by argparse.

`LOG_LEVEL` selects the publisher logger level, defaulting to `INFO`; use a recognized logging level. `LOG_FILE` optionally adds a rotating file handler with a 10,000,000-byte threshold and five backups. The console handler remains enabled. These environment variables configure the named publisher logger; AirLink uses a separate module logger and does not automatically inherit the publisher's file handler.

Implementation basis: [`normalize_config`, `parse_args`, and parameter loading](../vantage-publisher.py), [`airlinkData`](../airlink.py), and [`load_parameters`](../collect-history.py).
