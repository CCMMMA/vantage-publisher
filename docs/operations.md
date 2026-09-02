# Operations, reliability, and fault diagnosis

[Documentation index](README.md)

## Reliability boundaries

Reliability must be assessed at each stage of the data path. A console observation can be overwritten before main-loop selection, a CSV append can fail, a queued MQTT packet can expire, and a Signal K send can fail without replay. Consequently, the presence of a persistent MQTT queue does not make the entire acquisition system lossless.

| Component | Recovery behavior | Limit of assurance |
| --- | --- | --- |
| Console reader | Reconnects after failures | Keeps only the latest selected observation |
| CSV writer | Logs errors and continues | Failed rows are not queued for later storage |
| MQTT queue | Persists packets before publication and retains incomplete deliveries | Retention limits, storage failure, and duplicate replay remain possible |
| Signal K | Reopens failed websocket on later eligible output | No persistent delta backlog |
| AirLink | Periodically refreshes a cache | No exported measurement age or freshness guarantee |
| HTTP server | Runs separately after successful startup | A startup failure does not automatically retry the listener |

The SQLite connection uses WAL journaling, `synchronous=NORMAL`, and a ten-second connection timeout. WAL supports concurrent access through its journal, but a live database includes state beyond the main database file; recent transactions can depend on the WAL. With this synchronization policy, application-process recovery and power-loss durability are different guarantees. See [SQLite's WAL documentation](https://www.sqlite.org/wal.html).

## Queue retention and capacity

The queue stores `id`, insertion timestamp `ts`, topic, serialized payload, QoS, and retain flag. The insertion timestamp is host Unix time in whole seconds, not the measurement timestamp inside the payload. Pruning runs after insertion and before a replay batch: it removes records older than `offlineMaxAgeSec` and then removes the oldest IDs if the count exceeds `offlineMaxMessages`.

Retention is therefore event-driven rather than a background timer. Already submitted in-memory Paho messages are not explicitly canceled when their SQLite records are pruned. Retention limits describe the persistent backlog, not a guaranteed broker-side expiry policy.

For approximate planning, let `M` be the maximum record count and `r` the rate of processed packets per second. The count-based retention horizon is:

```text
Hcount ≈ M / r seconds
Heffective ≈ min(Hcount, offlineMaxAgeSec)
```

At one packet per ten seconds, 200,000 records represent about 23.1 days by count, while the default seven-day age limit is more restrictive. Actual packet rate depends on main-loop work, station availability, and selection; record count is not a disk-byte quota. Measure representative serialized payload sizes and database growth on the deployment filesystem.

The main loop advances a batch of up to 200 records and tracks at most 200 pending results through that path. Draining a large backlog can span many cycles. The bound avoids an unbounded replay loop but does not establish a throughput guarantee. Slow acknowledgment, synchronous integrations, or stalled pending results can delay later traffic; the publisher has no separate application-level acknowledgment deadline.

## Meaning of delivery

A completed MQTT publish is not proof of subscriber processing. A crash after broker completion but before SQLite deletion can lead to replay of the same packet. Consumers should preserve enough source information to recognize duplicates where appropriate, without assuming that timestamp alone is unique. CSV and MQTT records also have no shared transaction identifier.

A `Cycle OK` log means the main loop reached that log statement. It does not certify success of all outputs: several output failures are caught and logged earlier. Likewise, a low queue count can result from pruning as well as delivery. Diagnose health using packet freshness, destination observations, queue behavior, and error logs together.

## Routine inspection

For Compose deployments:

```bash
docker compose ps vantage-publisher
docker compose logs --tail=200 vantage-publisher
```

Examine the latest expected hourly CSV file and compare its timestamp with the host clock and console clock. Check storage headroom and permissions, and inspect the receiving broker or Signal K application independently. The program exposes no structured health endpoint, metrics endpoint, or automatic alerting service.

The following read-only SQLite inspection requires the `sqlite3` command-line tool and a valid existing database path. It reports retained queue size and insertion-time range; it does not change or acknowledge messages:

```bash
sqlite3 -readonly /storage/vantage-pro/mqtt_offline_queue.sqlite \
  "SELECT COUNT(*) AS queued, datetime(MIN(ts), 'unixepoch') AS oldest_utc, datetime(MAX(ts), 'unixepoch') AS newest_utc FROM queue;"
```

## Troubleshooting by symptom

| Observation | Interpretation to investigate | Useful next action |
| --- | --- | --- |
| `USB connect error` | Bridge unavailable, wrong namespace or port | Check ser2net listener and configured `usbPort` |
| `USB stream read error` | Serial settings, device access, competing console client, or malformed response | Inspect bridge logs and isolate a single console client |
| `No USB data available` | No new usable reader result | Check connection logs and whether parameter filtering removes every field |
| Missing `DatetimeWS` | Console timestamp absent or filtered out | Enable `Datetime` in the parameter map and inspect source data |
| CSV absent | Storage disabled, empty root, permissions, or no selected observation | Check effective output flags, path, and write errors |
| `CSV schema update error` | Temporary rewrite or replacement failed | Preserve the original file and check directory write permissions and free space |
| MQTT runtime disabled | Missing broker/port or client startup failure | Inspect startup warning and dependency installation |
| MQTT connected but backlog persists | Completion delays, limited cycle throughput, or stalled pending results | Compare broker logs, queue count, and completion logs |
| MQTT duplicates | Delivery completed before local deletion, or transport retry | Apply downstream deduplication appropriate to the measurement identity |
| Signal K waiting for token | Approval absent, endpoint mismatch, or validation failure | Review access-request logs and server approval state |
| Signal K values rejected or implausible | Context/path rejection, units, or raw conversion fallback | Compare a generated delta against server expectations |
| AirLink fields disappear | Empty refresh result, missing credentials, or unexpected response structure | Inspect AirLink logs and provider access independently |
| HTTP authentication ineffective | Empty `httpUser` disables authentication | Inspect the effective configuration and listener routing |
| `ModuleNotFoundError` during dry run | Dependencies are imported before feature flags take effect | Install the full requirements in the active environment |

Repeated authorization requests do not necessarily indicate a console problem. Signal K authentication and station acquisition are separate state machines. Conversely, a healthy websocket does not establish that fresh console data is arriving.

## Backup and recovery

Preserve configuration, parameter selection, retained CSV files, and the MQTT queue when migrating a station. Record the source revision and installed dependency versions alongside the backup. Treat credential-bearing configuration as sensitive operational material.

For SQLite, use a database-aware backup mechanism or stop the publisher cleanly before taking a consistent filesystem backup. Do not copy only the main `.sqlite` file from an actively writing WAL database and assume it contains every retained message. Keep the original backup until a restore has been checked.

Before a restore, stop the destination publisher so that two processes do not share the same spool or hourly CSV files. Restore ownership and permissions, verify the station identity, and start one instance. Queue contents preserve their original topic and serialized payload: changing `uuid` does not rewrite previously queued messages. Expect possible replay duplicates after recovery.

## Upgrade and rollback

A controlled upgrade begins by recording the current Git revision, image identity where applicable, dependency versions, and configuration. Retain the current runnable image or environment and back up mutable state. Review changes to payloads, default values, storage handling, and dependency requirements before stopping the service.

Validate the candidate in a separate environment using offline tests and a suitable station integration check. Then stop the operational instance, replace the code or image, and start only the intended publisher service. Confirm freshness and destination behavior before considering the deployment complete.

If validation fails, stop the candidate before restoring the previous executable environment. Preserve the current queue and observations unless a verified storage incompatibility requires restoring a backup; reverting them unnecessarily can lose new records or repeat older ones. This repository does not provide an automated migration or rollback framework.

## Incident evidence

An actionable incident report should identify the application revision, dependency environment, sanitized configuration, exact timestamps and timezone, relevant logs, enabled outputs, and observed destination behavior. Include whether the console was being accessed by another process and whether the host clock or network changed. Do not include live API secrets, tokens, or unredacted credential-bearing URLs.
