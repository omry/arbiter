# Arbiter Go Client

Experimental native Arbiter client.

The goal of this subproject is a small, portable client binary
that agents can run without a Python environment. The Python server remains the
source of truth for Arbiter runtime behavior, plugin loading, configuration
composition, and deployment tooling.

Current scope:

- one native executable, with build dependencies pinned in `go.mod` and `go.sum`
- CLI shell for the native `arbiter` client
- URL resolution from command-line override, environment, or client config
- native HTTP transport
- `info`, `op`, and explicit `artifact` commands
- independent connection and API response timeouts, plugin/operation response
  budgets, and artifact download inactivity limits (no total transfer deadline)

See the [client reference](../../website/docs/reference/client.md#timeouts) for
the timeout configuration and command-line override contract.

## Build

From the repository root, build the default cross-platform binary matrix with:

```bash
tools/build_go_client --clean
```

This writes stripped binaries under `client/go-cli/dist/<goos>-<goarch>/` for
Linux, macOS, and Windows on `amd64` and `arm64`. Limit the matrix with one or
more `--target GOOS-GOARCH` arguments; pass `--debug` to keep debug symbols.

For a local single-platform build from this directory:

```bash
go generate ./internal/cli
go build -buildvcs=false ./cmd/arbiter
```

The Go client version is generated from `../../server/pyproject.toml`, so it stays
aligned with the `arbiter-server` package version.

## PyPI client wheels

The native client can be published as the `arbiter-client` PyPI project. Build
its platform-tagged wheels from the repo root with:

```bash
tools/build_release_dists --packages client
```

The generated wheels contain no Python wrapper. Each wheel places the matching
native executable in the standard wheel script location:

```text
arbiter_client-<version>.data/scripts/arbiter
arbiter_client-<version>.data/scripts/arbiter.exe
```

## Test

```bash
go test ./...
```

The Linux and macOS terminal regression harness requires Python 3. Windows tests
create a private console. These tests check actual expiry, private-state cleanup
and lock reacquisition; CI requires them on all six native platform targets.
Python is a test dependency here, not a dependency of the installed client binary.

When running in a sandbox where the default Go build cache is not writable, set
`GOCACHE` to a writable directory:

```bash
GOCACHE=/tmp/arbiter-go-cache go test ./...
GOCACHE=/tmp/arbiter-go-cache go build -buildvcs=false ./cmd/arbiter
```

## Intended Distribution Shape

The native client is published as the `arbiter-client` Python package. It emits
one platform-tagged wheel per supported target, each containing:

```text
arbiter_client-<version>.data/scripts/arbiter
arbiter_client/bin/arbiter
```

The wheel script installs the normal `arbiter` executable onto `PATH`. The
stable `arbiter_client/bin/arbiter` copy is for Agent Skill Installer, which
copies it into the installed `arbiter` skill from the external companion wheel.

Package the platform-neutral skill wheel from the repo root:

```bash
tools/build_release_dists --packages skill
```

This writes the `arbiter-skill` wheel under `dist/`. The skill package declares
`arbiter-client==${package.version}` in `agent-skill-installer.yaml`, so ASI lets
pip select the correct native client wheel for the installing platform.
