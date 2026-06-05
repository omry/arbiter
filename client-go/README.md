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
- `info`, `op`, and raw `mcp` commands

Not implemented yet:

- release builds for all target platforms

## Build

```bash
go generate ./internal/cli
go build -buildvcs=false ./cmd/arbiter
```

The Go client version is generated from `../core/pyproject.toml`, so it stays
aligned with the `arbiter-core` package version.

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

The skill can eventually carry prebuilt binaries by platform:

```text
skills/arbiter/bin/
  linux-amd64/arbiter
  linux-arm64/arbiter
  darwin-amd64/arbiter
  darwin-arm64/arbiter
  windows-amd64/arbiter.exe
```

The skill can choose the matching binary at runtime and fall back to the Python
client when no native binary is available.
