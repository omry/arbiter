# Internal temporary release readiness notes

This checklist is for preparing the initial Arbiter release. It is an
internal working document, not website documentation.

Publishing one or more dev wheels is useful validation, but it does not make the
release ready. Treat every required gate as evidence-based: if the check did not
run, it is not done.

## Current blockers for the initial release

- Local release rehearsal from built artifacts has not been run end to end.
- Documentation pass across the published-package install and deployment path
  has not been completed.
- Security analysis has not been completed.

## Required gates

### 1. Version and package readiness

- Choose the target release version and package keys.
- Confirm package versions are on the intended release line.
- Confirm the all-in-one meta package uses exact dependencies for the real
  packages it curates.
- Confirm plugin packages declare the correct server compatibility line.
- Run:

```bash
tools/upgrade_release_line 0.9 --check
tools/plan_pypi_publish --packages all
```

Use `--packages` when validating a fine-grained plugin, skill, or meta-package
release.

### 2. Local release rehearsal

Build all distributions into a temporary wheelhouse:

```bash
tools/build_release_dists --clean --outdir /tmp/arbiter-release/dist
```

Use `--packages server,smtp`, `--packages meta:all`, `--packages client`, or a
skill key such as `--packages skill:linux-amd64` for narrower package sets.
Add `--verbose` when build logs are needed.

Prepare the publish artifact set from the built wheelhouse:

```bash
tools/plan_pypi_publish \
  --packages all \
  --dist-dir /tmp/arbiter-release/dist \
  --output-dir /tmp/arbiter-release/dist-publish \
  --prepare-output-dir
```

Install from the built wheelhouse into a fresh virtualenv and run installed
entry points:

```bash
.venv/bin/python -m venv /tmp/arbiter-release/venv
/tmp/arbiter-release/venv/bin/python -m pip install --upgrade pip
/tmp/arbiter-release/venv/bin/python -m pip install \
  --find-links /tmp/arbiter-release/dist \
  /tmp/arbiter-release/dist/arbiter_suite-0.9.0.dev2-py3-none-any.whl
/tmp/arbiter-release/venv/bin/arbiter-server version --json
```

Also check installed CLI help, config bootstrap, plugin discovery, and any
package-specific behavior touched by the release.

### 3. Test and deployment readiness

Run the normal release checks:

```bash
.venv/bin/python -m pytest server/tests plugins/smtp/tests plugins/imap/tests
.venv/bin/python -m nox -s lint
.venv/bin/python -m nox -s compat
```

Run the Docker deploy smoke when deployment scaffolding, package installation,
or generated helper scripts changed:

```bash
.venv/bin/python -m nox -s deploy-test
```

### 4. Documentation pass

Review the public docs against the installed-package world:

- quickstart
- package installation and Docker deployment
- config bootstrap and configuration model
- CLI reference and command names
- security model and limitations
- plugin author docs
- release process

The pass should confirm that examples use current package names, console entry
points, config shape, version expectations, and security claims.

### 5. Security readiness

Complete a focused security analysis before the initial release. Cover:

- MCP boundary and caller trust assumptions
- local and Docker deployment modes
- config and environment file handling
- plugin discovery and loading
- package supply chain assumptions
- secret handling
- SMTP and IMAP operation policies
- logging and audit gaps

Turn concrete fixes into patches or backlog items. Document accepted risks and
make sure operator docs do not overstate the security model.

### 6. Release notes readiness

Dev releases do not require release notes.

For non-dev releases, build package-scoped Towncrier notes for every package
that will publish and commit the generated `NEWS.md` changes before publishing.
See `website/docs/maintain/release-process.md`.

### 7. Publishing readiness

Confirm PyPI trusted publishers exist for the selected package keys, and that
the GitHub `pypi` environment is ready.

For the initial bootstrap, publish one package at a time because PyPI pending
trusted publishers currently allow only one pending project for the same
repository, workflow, and environment.

The native client publishing key is `client`, which publishes the
`arbiter-client` platform wheel set. The transitional Python CLI client is not
published. The skill publishing set is the selector package `skill` plus the
six native target packages: `skill:linux-amd64`, `skill:linux-arm64`,
`skill:darwin-amd64`, `skill:darwin-arm64`, `skill:windows-amd64`, and
`skill:windows-arm64`.

### 8. Post-release verification

After publishing, verify a clean install from PyPI:

```bash
python -m venv /tmp/arbiter-pypi-smoke
/tmp/arbiter-pypi-smoke/bin/python -m pip install --upgrade pip
/tmp/arbiter-pypi-smoke/bin/python -m pip install arbiter-suite==0.9.0
/tmp/arbiter-pypi-smoke/bin/arbiter-server version --json
```

For prereleases, include `--pre` and the exact prerelease version.

Confirm the default meta package, selected plugin packages, and generated
deployment state behave as expected.
