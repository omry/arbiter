# Arbiter Go Client

Experimental native Arbiter client.

The goal of this subproject is a small, portable, dependency-free client binary
that agents can run without a Python environment. The Python server remains the
source of truth for Arbiter runtime behavior, plugin loading, configuration
composition, and deployment tooling.

Current scope:

- no third-party Go dependencies
- CLI shell for the native `arbiter` client
- MCP URL resolution from command-line override, environment, or client config
- minimal MCP Streamable HTTP transport
- `info`, `op`, explicit `artifact`, and raw `mcp` commands

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
