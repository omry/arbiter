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
- `info`, `op`, explicit small-text `artifact`, and raw `mcp` commands

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

The skill distribution uses two layers:

```text
arbiter-skill
  agent-skill-selector.yaml

arbiter-skill-linux-amd64
  SKILL.md
  agent-skill-installer.yaml
  bin/arbiter

arbiter-skill-windows-amd64
  SKILL.md
  agent-skill-installer.yaml
  bin/arbiter.exe
```

The selector artifact contains only `agent-skill-selector.yaml` and resolves to
the matching target artifact with `platform_specific`. Each target artifact
contains the Arbiter skill, a simple discoverability config for the installer,
and exactly one native client binary. The selector config lives in
`packaging/arbiter-skill/selector/agent-skill-selector.yaml`; the target
discoverability config lives in
`packaging/arbiter-skill/target/agent-skill-installer.yaml`, keeping selector
routing details such as local relative paths out of target metadata.

After building binaries, package local directories and wheels from the repo root:

```bash
tools/package_arbiter_skill --clean
```

This writes local install directories under `dist/arbiter-skill/local/` and
wheels under `dist/arbiter-skill/wheels/`.
