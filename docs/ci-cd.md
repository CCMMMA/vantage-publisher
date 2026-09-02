# Continuous integration and container delivery

[Documentation index](README.md)

## Purpose and delivery boundary

The [GitHub Actions workflow](../.github/workflows/ci-cd.yml) automates regression checks, container construction, and delivery of a tested image to GitHub Container Registry (GHCR). Previously these checks depended on an operator invoking local commands; a successful source push provided no automatic evidence that the Docker runtime could import its dependencies.

The workflow implements continuous delivery of an installable container. It does not connect to production stations or restart their services. Station addresses, remote access credentials, and a rollout policy are not defined in this repository. Keeping rollout explicit also allows a station operator to coordinate archive access, verify storage, and select a maintenance window.

## Triggers and job sequence

| Event | Python checks | Container build and smoke checks | GHCR publication |
| --- | --- | --- | --- |
| Pull request targeting `main` | Yes | Yes | No |
| Push to `main` | Yes | Yes | Yes, revision tag and `latest` |
| Push of a `v*` tag | Yes | Yes | Yes, revision tag and exact version tag |
| Manual workflow dispatch | Yes | Yes | No |

The job dependency chain is `test → container → publish`. Publication is skipped unless its upstream jobs succeed and the event is an eligible push. Pull-request runs may cancel a superseded run on the same ref. Push runs are not interrupted by that cancellation setting; workflow concurrency groups runs by ref. Tags should use container-compatible names such as `v1.2.3`, without slashes or spaces.

The test matrix uses Python 3.8, 3.12, and 3.13 on `ubuntu-22.04`. It compiles the application and test sources, runs the offline regression suite, and parses both JSON sample files. These matrix jobs intentionally use the mocked-client tests without installing runtime dependencies. They establish source-level and tested-behavior compatibility for those interpreters, not compatibility of every dependency combination.

The container job then builds the existing Dockerfile with `--pull`, using its Python 3.8 base and real runtime requirements. It runs `pip check`, imports the integration dependencies, constructs a Paho callback API version 2 client, and invokes the publisher's `--help` entry point. These commands run with container networking disabled. They do not contact a console, broker, or API. Image construction itself requires network access to the base-image registry, package repositories, and the Git-sourced dependency.

## Publishing the tested artifact

For eligible pushes, the container job exports its successfully checked image with `docker save` and uploads it as a short-lived workflow artifact. The publication job downloads that artifact from the same run and loads it before applying registry tags. It does not rebuild the image. This preserves the tested image across the boundary between a read-only validation job and a publication job.

The uploaded artifact is retained for one day to limit storage usage. Re-running only the publication job after the artifact has expired will fail; rerun the entire workflow to regenerate the tested artifact. Full reruns rebuild from the source revision and can resolve newer unpinned dependencies.

The default upstream registry path is `ghcr.io/ccmmma/vantage-publisher`. The workflow derives it from the current repository name and converts it to lowercase, so a fork uses its own namespace. Images receive these tags:

| Tag | Meaning |
| --- | --- |
| `sha-<full-commit-sha>` | Image produced by a successful run for that source revision |
| `latest` | Image published by a successful `main` push |
| `v1.2.3`, for example | Image published by that exact Git version tag |

Revision tags provide traceability but are not immutable: rerunning a build for the same commit can replace a tag, particularly because dependencies are unpinned. Record the registry digest for reproducible installation and rollback. Version-tag runs do not update `latest`. A tag run validates the tagged commit independently; the workflow does not require that the commit already belong to `main`.

Images currently target `linux/amd64`, matching the hosted build runner. No ARM image or multi-platform manifest is produced. ARM station operators should continue using a locally validated native build until architecture-specific build and execution checks are added.

## Permissions and repository setup

The default workflow token has `contents: read`. Only the publication job requests `packages: write`; it logs into GHCR with the automatically supplied `GITHUB_TOKEN`. No personal token, station secret, or Docker Hub account is required by the workflow. Third-party actions are pinned to full commit hashes, and checkout does not persist Git credentials. GitHub documents this publishing approach in its [container image workflow guide](https://docs.github.com/en/actions/tutorials/publish-packages/publish-docker-images).

Repository or organization policy must permit GitHub Actions and package creation. An existing package may also need to grant the repository access. Package visibility is controlled in GitHub Packages; a successful workflow does not itself make a private package anonymously downloadable. Consult the [GHCR access documentation](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry) when configuring consumers.

Creating a workflow does not create a branch-protection rule. Repository administrators can make the matrix checks and container check required before merging. Package publication is unsuitable as a required pull-request check because it is intentionally skipped for pull requests.

The [.dockerignore](../.dockerignore) file allows only the Dockerfile, requirements, publisher, and AirLink module into the build context. Configuration, credentials, logs, local databases, editor files, and repository history are excluded. If the Dockerfile later needs another input, update this allowlist explicitly.

## Installing a delivered image

On a compatible station host with registry access, select a verified revision tag or digest. The supplied Compose service expects the local image name `vantage-publisher`; an operator can import a selected registry image under that name without changing configuration or storage mounts:

```bash
# Replace the placeholder with the full commit SHA from a successful Actions run.
revision=REPLACE_WITH_FULL_COMMIT_SHA
image="ghcr.io/ccmmma/vantage-publisher:sha-${revision}"
docker pull "$image"
docker tag "$image" vantage-publisher:latest
docker compose up -d --no-deps --force-recreate vantage-publisher
docker compose logs --tail=100 vantage-publisher
```

Authenticate to GHCR first if the package is private. For a reproducible rollout, substitute a recorded `ghcr.io/ccmmma/vantage-publisher@sha256:...` digest for the image reference. Preserve the previous image identifier and follow the [upgrade and rollback procedure](operations.md#upgrade-and-rollback). The command starts only the publisher, not the unvalidated `restarter` helper.

## Failure diagnosis and limitations

A failed Python job should be investigated through its interpreter-specific test output. A failed container build can indicate base-image availability, package repository problems, an incompatible dependency resolution, or changes in the Git dependency. A smoke-check failure means that the installed runtime failed an import, dependency consistency check, or command-line startup check; it is stronger evidence than a syntax check but still does not simulate a station session.

If validation succeeds but publication fails, inspect package permissions, repository package association, and registry responses. A failed publication does not alter a running station. Retrying after partial publication may encounter a revision tag that already exists; publication is not an atomic operation across all tags.

The workflow does not certify sensor accuracy, perform serial or network integration tests, deploy configuration, impose dependency locks, scan every dependency for vulnerabilities, or certify power-loss durability. Its evidence complements the [development validation strategy](development.md) and the [deployment acceptance procedure](deployment.md), and should be reported with those limits intact.
