# Development, verification, and maintenance

[Documentation index](README.md)

## Repository map

| File or directory | Responsibility |
| --- | --- |
| `vantage-publisher.py` | Live acquisition, configuration, transformations, CSV, MQTT, Signal K, HTTP, and lifecycle |
| `airlink.py` | WeatherLink request and selected field conversion |
| `collect-history.py` | Console archive extraction and optional CSV export |
| `config.json.sample` | Example operational configuration |
| `parameters.json.sample` | Example station-field allowlist |
| `tests/test_publisher.py` | Offline regression suite with external clients mocked |
| `Dockerfile`, `docker-compose.yml`, `Makefile` | Container build and launch definitions |
| `util/` | Serial bridge configuration and service example |
| `updater.sh`, `get-docker.sh` | Update and host-provisioning helpers with deployment-specific assumptions |
| `docs/` | Behavioral reference and operational procedures |
| `AGENTS.md` | Repository maintenance constraints |

## Compatibility obligations

Changes must preserve existing configuration and parameter keys, the MQTT topic equal to `uuid`, and the documented CSV path semantics unless an intentional change is explicitly authorized. New configuration settings should be optional with safe defaults. Prefer a narrowly scoped change whose effect can be explained and reversed over an unrelated structural refactor.

Document the operational failure before implementing a correction. For example, premature queue deletion is a reliability failure because successful submission to an asynchronous library is weaker evidence than protocol completion. A regression test should capture that distinction through observable retained state, rather than merely checking that a particular helper was called.

## Local validation

Run the existing suite from the repository root:

```bash
python3 -m unittest discover -s tests -v
python3 -m py_compile vantage-publisher.py airlink.py collect-history.py tests/test_publisher.py
git diff --check
```

The tests substitute external imports and client objects and use temporary SQLite/CSV storage. They do not open a real console, broker, Signal K server, or WeatherLink session. They can run without installing the network dependencies, but success does not prove compatibility with a particular dependency release.

The documented baseline contains eight regression cases:

| Case | Evidence provided |
| --- | --- |
| Publish completion | A submitted but incomplete packet remains in SQLite and is not resubmitted by the next flush |
| Failed publish | A non-success return preserves the queue record for retry |
| Rejected connection | An unsuccessful connection does not set the online flag |
| Expired queue record | A record outside the age limit is excluded from replay |
| CSV field expansion | Existing rows survive a new column and subsequent append |
| Failed CSV replacement | Injected replacement failure preserves the original file and skips the new row |
| Empty storage root | An empty storage setting does not create a directory in the working directory |
| Dry datetime handling | A datetime-valued packet is logged and the configured delay is observed |

The suite passed when the initial reliability changes were prepared. This statement is a local regression result, not a claim of exhaustive testing. Syntax compilation also cannot establish runtime behavior or dependency compatibility.

## Integration validation strategy

Use a controlled deployment to test the boundary that a change affects. MQTT reliability changes warrant broker disconnect/reconnect and process-restart exercises with independently observed messages and queue contents. Storage changes warrant a representative hourly file, schema variation, and a realistic filesystem failure. Signal K changes warrant actual server acceptance of context, paths, units, and authorization transitions.

A station emulator can make malformed packets and reconnect behavior reproducible, but no emulator is supplied here. The current offline suite does not exercise real callback scheduling, TLS, server-specific authentication behavior, serial timing, archive timezone handling, or abrupt power failure. Record these limitations when reporting validation rather than treating passing unit tests as general production certification.

## Reproducible dependency environments

`requirements.txt` specifies a Git repository for PyVantagePro and unpinned package names for the other dependencies. Rebuilding at a later date can therefore resolve different software. For a release record, capture the Git commit and installed package metadata:

```bash
git rev-parse HEAD
python3 --version
python3 -m pip freeze
```

Retain the output with the deployment record, together with an image digest when using containers. Pinning a tested dependency set is a potential release-management improvement, but the documentation does not silently change the dependency policy or certify an untested combination.

## Known implementation limits

The configuration layer lacks comprehensive range validation; parameter maps use truthiness rather than strict booleans. Signal K conversion failures preserve raw values, and JSON serialization does not enforce finite numeric values. These behaviors can produce outputs that are structurally generated but unsuitable for a strict consumer.

The latest-observation architecture can discard intermediate reads. AirLink and Signal K operations can delay the main loop. Queue and CSV initialization failures may terminate startup, and not every later filesystem error is fully isolated. Token persistence rewrites configuration in place. The HTTP server exposes its whole configured root. None of these limitations is removed merely by enabling dry mode or adding a process restart policy.

The archive collector and deployment maintenance helpers have separate constraints described in their chapters. Future work should prioritize a reproducible failure or explicit operational requirement and then add the smallest meaningful verification for that change.

## Maintaining this documentation

When changing defaults, update the [configuration reference](configuration.md) and example configuration together. Changes to timestamps, units, field selection, or envelopes require updates to the [data model](data-model.md). Changes to replay, retention, or failure handling require updates to [operations](operations.md). Keep the root README as the entry point to the detailed manual.

Verify relative links and example JSON after documentation edits. Distinguish code-derived defaults from recommended deployment values, implemented behavior from desired behavior, and tested observations from assumptions. An academic standard of explanation depends on traceable claims and explicit limits, not on presenting the software as more general or more validated than it is.
