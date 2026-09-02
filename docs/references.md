# Sources, terminology, and evidence

[Documentation index](README.md)

## Primary implementation sources

The checked-out source is the primary evidence for claims about this publisher. External protocol documentation explains interfaces but cannot establish that every integration path has been validated in this repository.

| Subject | Repository source |
| --- | --- |
| Configuration defaults and command-line precedence | [`normalize_config`, `parse_args`, and `main`](../vantage-publisher.py) |
| Observation timing and concurrency | [`USBReaderThread` and `main`](../vantage-publisher.py) |
| Queue persistence and replay | [`OfflineQueueSQLite` and `flush_offline_queue`](../vantage-publisher.py) |
| CSV schema handling | [`ensure_csv_schema` and `save_data_to_csv`](../vantage-publisher.py) |
| Signal K transformations and access management | [`convert_signalk_value`, `build_signalk_update`, and Signal K classes](../vantage-publisher.py) |
| AirLink selection and formulas | [`airlink.py`](../airlink.py) |
| Archive command behavior | [`collect-history.py`](../collect-history.py) |
| Regression evidence | [`tests/test_publisher.py`](../tests/test_publisher.py) |
| Container assumptions | [`Dockerfile`](../Dockerfile), [`docker-compose.yml`](../docker-compose.yml), [`Makefile`](../Makefile) |

## External primary references

The following materials were consulted on 3 September 2026. They are cited for interface semantics rather than as evidence of a completed integration test.

1. Eclipse Foundation. [Paho MQTT Python client reference](https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html). Describes asynchronous connection, callback APIs, and publish-result completion.
2. Signal K Project. [Full and Delta Models, specification 1.7.0](https://signalk.org/specification/1.7.0/doc/data_model.html). Provides a versioned explanation of delta updates. This citation does not assert that the publisher's custom paths or context satisfy every server's schema.
3. SQLite Project. [Write-Ahead Logging](https://www.sqlite.org/wal.html). Explains WAL behavior, synchronization considerations, and the relationship between database and journal files.

The [PyVantagePro repository](https://github.com/ccmmma/PyVantagePro) is the dependency location named by `requirements.txt`. Its remote contents were not successfully retrieved for this documentation review. Consequently, the manual reports publisher-side unit assumptions and directs operators to verify the installed dependency; it does not assert a remotely audited conversion contract.

WeatherLink field meanings, sensor selection, and air-quality index definitions likewise require verification against the actual provider response and provider documentation for the deployed product. This manual describes the extraction code rather than supplying an independent meteorological or air-quality standard.

## Terminology

| Term | Meaning in this manual |
| --- | --- |
| Observation | A dictionary decoded from a console read or returned by the archive API |
| Selected observation | A live observation after applying the parameter map |
| Packet | An observation enriched by publisher timestamps, station metadata, and optional AirLink data |
| Delta | A Signal K update containing a context, timestamp, and path/value entries |
| Queue record | A SQLite row retaining an MQTT topic, serialized packet, and publication options |
| Pending publish | A packet submitted to Paho whose result is still tracked in memory |
| Publish completion | The Paho completion condition appropriate to the chosen QoS; not subscriber application processing |
| Replay | A later attempt to publish retained queue contents |
| Retention | Age and count rules controlling how long queue records remain eligible |
| Dry mode | Live console acquisition with generated output logging and output side effects disabled, apart from configured logging |
| Provenance | The recorded origin and transformation context needed to interpret or reproduce a dataset |

## Limits of the evidence

The manual is an implementation analysis and operational reference. It is not a peer-reviewed evaluation, a measured throughput study, a sensor calibration report, or a protocol certification. Its quantitative queue example is an arithmetic planning estimate with stated assumptions. Its test descriptions concern the repository's offline regression suite. Claims requiring physical hardware, actual network services, or a specific dependency build remain deployment validation tasks.
