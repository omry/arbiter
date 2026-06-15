# Internal temporary release readiness notes

This checklist is for preparing the initial Arbiter release. It is an
internal working document, not website documentation.

Publishing one or more dev wheels is useful validation, but it does not make the
release ready. Treat every required gate as evidence-based: if the check did not
run, it is not done.

## Current blockers for the initial release

- Local release rehearsal from built artifacts has not been run end to end.
- Documentation pass across the published-package install, client and skill
  installation, and deployment path has not been completed.
- Security analysis has not been completed.
- Cleanup of the earlier platform-specific skill package attempt has not been
  planned or executed.

## Verified release evidence

- `0.9.1.dev2` was published from GitHub Actions run
  `27397058544` on `main`.
- The publish workflow used separate PyPI jobs for each publish key. The
  `arbiter-suite`, `arbiter-server`, `arbiter-imap`, `arbiter-smtp`,
  `arbiter-skill`, and `arbiter-client` publish jobs all completed
  successfully.
- PyPI version-specific JSON endpoints confirmed the expected `0.9.1.dev2`
  files:
  - `arbiter-server`, `arbiter-imap`, `arbiter-smtp`, and `arbiter-suite`:
    wheel plus sdist.
  - `arbiter-skill`: platform-neutral wheel.
  - `arbiter-client`: six platform wheels for macOS arm64, macOS x86_64,
    Linux aarch64, Linux x86_64, Windows amd64, and Windows arm64.
- The project-level PyPI JSON endpoint can lag immediately after upload. Prefer
  release-specific endpoints such as
  `https://pypi.org/pypi/arbiter-server/0.9.1.dev2/json` for post-publish
  verification evidence.
- Agent Skill Installer installed `arbiter-skill==0.9.1.dev2` from PyPI into
  standalone target `/tmp/arbiter-asi-pypi-5lz5Q9` using directory scope for
  Codex. ASI resolved the external `arbiter-client==0.9.1.dev2` companion
  wheel from PyPI, copied `arbiter_client/bin/arbiter` to
  `.codex/skills/arbiter/bin/arbiter`, wrote the Codex `AGENTS.md` hook, and
  the copied binary reported `arbiter 0.9.1.dev2`.

## Required gates

### 1. Version and package readiness

Status: complete.

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

Use `--packages server,smtp`, `--packages meta:all`, `--packages client`, or
`--packages skill` for narrower package sets.
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
VERSION=0.9.1
.venv/bin/python -m venv /tmp/arbiter-release/venv
/tmp/arbiter-release/venv/bin/python -m pip install --upgrade pip
/tmp/arbiter-release/venv/bin/python -m pip install \
  --find-links /tmp/arbiter-release/dist \
  "/tmp/arbiter-release/dist/arbiter_suite-${VERSION}-py3-none-any.whl"
/tmp/arbiter-release/venv/bin/arbiter-server version --json
```

Also check installed CLI help, config bootstrap, plugin discovery, and any
package-specific behavior touched by the release.

For the skill package, test the Agent Skill Installer path from the built
wheelhouse before publishing. Install `arbiter-skill` into a fresh temporary
target directory through ASI, then verify that the installed skill exposes the
expected `SKILL.md`, client entry point, and any installer-discoverable
metadata.

### 3. Test and deployment readiness

Run the normal release checks:

```bash
.venv/bin/python -m nox -s tests
.venv/bin/python -m nox -s lint
.venv/bin/python -m nox -s compat
```

Run the Docker deployment test before deploying, and when deployment
scaffolding, package installation, generated helper scripts, or the current
platform native client changed:

```bash
.venv/bin/python -m nox -s deploy-test
```

The release and publish workflows also run `server/tests/integration` across
the six supported platform runners. That suite builds the current-platform
`arbiter-client` wheel, installs it into a temporary Python environment, checks
`arbiter --version`, and uses the installed command against a local Arbiter
server. The Docker deployment test remains the pre-deploy gate for generated
deployment scaffolding and the current-platform native client in the Docker
flow.

### 4. Documentation pass

Review the public docs against the installed-package world:

- quickstart
- client and skill installation
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

The publish workflow publishes each package key in its own PyPI job. This keeps
server, plugin, suite, skill, and client uploads isolated, so one package upload
failure does not hide which artifact set failed. Keep `fail-fast: false` on the
publish matrix.

The native client publishing key is `client`, which publishes the
`arbiter-client` platform wheel set. The skill publishing key is `skill`, which
publishes the platform-neutral `arbiter-skill` package. The skill declares
`arbiter-client` as an ASI companion wheel, so ASI should resolve the
platform-specific native client through `arbiter-client` during skill install.

Before the final initial release, publish and validate the intended skill
package surface: publish `client`, publish `skill`, then verify ASI installs
`arbiter-skill` and copies the platform-selected `arbiter-client` executable
into the installed skill.

Inventory any old platform-specific skill package projects or artifacts from
the previous packaging attempt, including `arbiter-skill-{platform}` packages.
Decide whether each one should be left as a historical dev artifact, yanked,
deprecated in project metadata, or superseded by the single `arbiter-skill`
package, and record the cleanup action taken.

### 8. Post-release verification

After publishing, verify a clean install from PyPI:

```bash
VERSION=0.9.1
python -m venv /tmp/arbiter-pypi-smoke
/tmp/arbiter-pypi-smoke/bin/python -m pip install --upgrade pip
/tmp/arbiter-pypi-smoke/bin/python -m pip install "arbiter-suite==${VERSION}"
/tmp/arbiter-pypi-smoke/bin/arbiter-server version --json
```

For prereleases, include `--pre` and the exact prerelease version.

Confirm the default meta package, selected plugin packages, and generated
deployment state behave as expected.
