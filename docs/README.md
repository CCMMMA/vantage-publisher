# Vantage Publisher: technical and operational documentation

## Purpose and scope

Vantage Publisher connects a Davis Vantage Pro2 station to local file storage and remote information systems. Its central responsibility is to transform selected console observations into identifiable, timestamped records, then distribute those records through independently enabled CSV, MQTT, and Signal K outputs. The design separates console acquisition from the main processing loop, but it does not provide a complete archive of every acquired observation.

This documentation describes the implementation in this repository as reviewed on 3 September 2026. It distinguishes observed software behavior, deployment recommendations, and limitations that require further validation. Examples illustrate configuration and operation; they are not evidence of a successful deployment or certified protocol conformance. The executable entry point is [`vantage-publisher.py`](../vantage-publisher.py). The reference to `vantage-publisher-threading.py` in the repository instructions is historical; that filename is not present in this checkout.

## Reading paths

| Reader's objective | Relevant chapters |
| --- | --- |
| Install and commission a station | [Deployment](deployment.md), then [Configuration](configuration.md) |
| Understand timing and concurrency | [Architecture](architecture.md) |
| Interpret exported observations | [Data model](data-model.md), then [Integrations](integrations.md) |
| Maintain an unattended installation | [Operations and reliability](operations.md) |
| Recover historical console observations | [Archive collection](archive-collection.md) |
| Build and release container images | [CI/CD](ci-cd.md) |
| Modify and validate the implementation | [Development and validation](development.md) |
| Locate the basis for technical claims | [Sources and terminology](references.md) |

For a first deployment, establish console connectivity before enabling remote outputs. Then verify the meaning of a small set of measurements, the station identity, and the timestamp conventions. Add storage and publishing one at a time so that each observation in the logs can be attributed to a specific component.

## Scientific interpretation

A successful transport operation establishes that bytes moved across an interface; it does not establish that the represented measurement is accurate, calibrated, timely, or appropriate for a particular analysis. The publisher does not perform range-based quality control, sensor calibration, uncertainty estimation, or temporal aggregation. A research workflow must retain those responsibilities explicitly.

Reproducibility also depends on the installed PyVantagePro revision, because decoding and most unit conversions occur in that dependency. Record the application commit, dependency versions, configuration, parameter selection, console settings, and clock conditions with each deployment. Store credentials separately from any configuration copy intended for publication.

## Documentation conventions

Configuration tables report defaults from executable code, which sometimes differ from the example configuration. All shell examples assume the repository root as the working directory unless a different location is stated. Paths within Markdown are relative so that the documentation remains usable in a local checkout and in a repository browser.

The project is distributed under the repository's [Apache License 2.0](../LICENSE). No software citation, DOI, validation dataset, or performance benchmark is supplied by these documentation changes.
