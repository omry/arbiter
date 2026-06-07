# Arbiter Client

Native Arbiter client binary packaged as platform-specific Python wheels.

This distribution intentionally contains no Python wrapper. Installing the
wheel places the native `arbiter` executable on the environment `PATH`.

For local development from the repository root:

```bash
tools/build_go_client --target linux-amd64
python -m pip install -e client
arbiter --version
```

Use the matching `GOOS-GOARCH` target for your platform, such as
`darwin-arm64` or `windows-amd64`. Editable installs use a small launcher that
execs the binary in `client/go-cli/dist`, so rebuilding the Go client updates
the installed `arbiter` command without reinstalling the package.
