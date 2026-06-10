# Arbiter Client

Native Arbiter client binary packaged as platform-specific Python wheels.

This distribution intentionally contains no Python wrapper. Installing the
wheel places the native `arbiter` executable on the environment `PATH`.
The wheel also includes the executable at `arbiter_client/bin/arbiter` so
Agent Skill Installer can copy it into the installed Arbiter skill from a stable
path across platform wheels.

For local development from the repository root:

```bash
python -m pip install -e client
arbiter --version
```

The package build infers the host `GOOS-GOARCH` target and runs
`tools/build_go_client` if the matching binary is missing. Set
`ARBITER_CLIENT_TARGET` to build a specific target, such as `darwin-arm64` or
`windows-amd64`. Editable installs use a small launcher that execs the binary in
`client/go-cli/dist`, so rebuilding the Go client updates the installed
`arbiter` command without reinstalling the package.
