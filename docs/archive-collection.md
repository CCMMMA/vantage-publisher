# Historical archive collection

[Documentation index](README.md)

## Purpose and distinction from live output

[`collect-history.py`](../collect-history.py) retrieves console archive records through `VantagePro2.get_archives_as_json()`. It is an independent command-line program, not an automatic backfill stage of the live publisher. It does not read `config.json`, publish MQTT, enrich records with AirLink, or emit Signal K deltas.

Archive data follow the decoding and normalization provided by the installed PyVantagePro dependency. The collector adds no separate conversion layer and no generated live metadata. Archive sampling intervals and completeness are determined by the console archive and the dependency, not by the live publisher's `delay`.

## Interface

| Option | Default | Interpretation |
| --- | --- | --- |
| `--url` | `tcp:127.0.0.1:22222` | Complete station connection URL |
| `--timeout` | `10.0` | Connection/read timeout in seconds |
| `--start` | `2009-01-01T01:01:01` | Beginning passed to the archive API |
| `--stop` | `None` | No explicit ending passed; CLI help describes this as now |
| `--parameters` | `parameters.json` | Field selection object |
| `--output` | Unset | CSV destination; absent means console logging only |
| `--log-level` | `INFO` | Root logging level; unknown names fall back to `INFO` |
| `-h`, `--help` | — | Display usage |

Supply an explicit range for a controlled extraction rather than relying on the broad default start. The program rejects a stop before the start but does not implement more detailed interval validation.

## Date and timezone semantics

Arguments are parsed with `datetime.fromisoformat()`, with a terminal `Z` translated to `+00:00`. Both naive and offset-aware values can therefore be parsed. They are passed to the dependency without timezone normalization. Use a consistent timezone convention for start and stop and verify that convention against the installed archive API.

Mixing a naive start with an aware stop, or the reverse, can raise a Python comparison error before collection. Omitting `--start` while specifying an aware `--stop` has the same risk because the default start is naive. Parsing an ISO timestamp does not establish correct interpretation by the console archive interface. The inclusivity of range endpoints and timezone treatment belong to the dependency contract and should be tested with known archive records.

## Collection examples

Run from an environment containing PyVantagePro. Arrange a maintenance window or otherwise release the live console connection before extraction; the supplied bridge may displace an existing client when another connects.

```bash
python3 collect-history.py \
  --url tcp:127.0.0.1:22222 \
  --start 2026-09-01T00:00:00 \
  --stop 2026-09-02T00:00:00 \
  --parameters parameters.json \
  --output /tmp/station-history-20260901.csv
```

To inspect records without writing a CSV:

```bash
python3 collect-history.py \
  --start 2026-09-01T00:00:00 \
  --stop 2026-09-01T01:00:00
```

Rows are logged with a `ROW;` prefix. Console logging is a presentation of records, not a standalone JSON array. The device is closed in a `finally` block after collection attempts.

## Filtering and CSV behavior

A missing parameters file retains all fields. An existing map is an allowlist, except that an existing `Datetime` is always retained, regardless of its map value. Use native JSON booleans; nonempty strings such as `"false"` are truthy in the current loader.

The collector builds a union of all returned field names and writes one header. It creates the output parent directory as needed and opens the destination in write mode, replacing existing content. It does not append to a previous extraction or write atomically. Choose a new output filename for each extraction whose provenance must be retained.

The entire returned collection is held in memory, and filtering can create another list. Large date ranges can therefore consume substantial memory. No progress checkpoint, incremental resume, pagination policy, or overlap deduplication is implemented by this script. If no rows are returned, no CSV is created or overwritten.

## Validation of a recovered dataset

Check row count, date extent, repeated timestamps, missing intervals, selected columns, and plausible units before merging an extraction with another dataset. Preserve the exact command, dependency revision, console timezone convention, and output checksum in the extraction record. If combining live records with archives, reconcile the live processing timestamp and the archive observation timestamp explicitly rather than concatenating them as though they had identical temporal meaning.
