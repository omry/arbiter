---
title: Release Process
---

Arbiter publishes several Python distributions from one repository:

- `core`: `arbiter-core`, the real core runtime package
- `imap`: `arbiter-imap`, the IMAP plugin
- `smtp`: `arbiter-smtp`, the SMTP plugin
- `meta:all`: `arbiter-suite`, a zero-code dependency bundle for all real
  packages
- `skill`: `arbiter-skill`, the agent-skill selector package
- `skill:linux-amd64`: `arbiter-skill-linux-amd64`, the Linux amd64 native
  skill target
- `skill:linux-arm64`: `arbiter-skill-linux-arm64`, the Linux arm64 native
  skill target
- `skill:darwin-amd64`: `arbiter-skill-darwin-amd64`, the macOS amd64 native
  skill target
- `skill:darwin-arm64`: `arbiter-skill-darwin-arm64`, the macOS arm64 native
  skill target
- `skill:windows-amd64`: `arbiter-skill-windows-amd64`, the Windows amd64
  native skill target
- `skill:windows-arm64`: `arbiter-skill-windows-arm64`, the Windows arm64
  native skill target

Meta package keys do not expand to their dependencies. Selecting `meta:all`
publishes only the `arbiter-suite` package.

Skill package versions come from `arbiter-core`. They are generated wheel-only
artifacts and do not have separate towncrier release notes.

This page describes publishing mechanics only.

## News fragments

Final releases require package-scoped towncrier release notes for every package
that will publish. Dev releases such as `0.9.0.dev1` do not consume release
notes.

Add fragments under the package that changed:

```text
core/newsfragments/123.feature.md
imap/newsfragments/123.bugfix.md
smtp/newsfragments/+smtp-only-change.feature.md
meta/arbiter-suite/newsfragments/+meta-package-change.feature.md
```

Use a GitHub issue or PR number when one exists. Use the `+` orphan prefix when
there is no issue or PR.

Before a final release, preview and build the notes for each package that will
publish:

```bash
.venv/bin/python -m towncrier build --draft --config core/pyproject.toml --version 0.9.0
.venv/bin/python -m towncrier build --yes --config core/pyproject.toml --version 0.9.0

.venv/bin/python -m towncrier build --draft --config imap/pyproject.toml --version 0.9.0
.venv/bin/python -m towncrier build --yes --config imap/pyproject.toml --version 0.9.0

.venv/bin/python -m towncrier build --draft --config smtp/pyproject.toml --version 0.9.0
.venv/bin/python -m towncrier build --yes --config smtp/pyproject.toml --version 0.9.0

.venv/bin/python -m towncrier build --draft --config meta/arbiter-suite/pyproject.toml --version 0.9.0
.venv/bin/python -m towncrier build --yes --config meta/arbiter-suite/pyproject.toml --version 0.9.0
```

Commit the updated `NEWS.md` files and removed fragments before publishing.

## Publish planning

The publish workflow builds all bundled distributions, then runs:

```bash
tools/plan_pypi_publish --prepare-output-dir
```

The planner compares local versions with PyPI and copies only packages whose
local version is newer, or whose PyPI project does not exist yet, into
`dist-publish/`. It rejects local versions that are older than PyPI.

Limit the publish set with package keys:

```bash
tools/plan_pypi_publish --packages core --prepare-output-dir
tools/plan_pypi_publish --packages core,imap --prepare-output-dir
tools/plan_pypi_publish --packages smtp --prepare-output-dir
tools/plan_pypi_publish --packages skill:linux-amd64 --prepare-output-dir
```

The planner reads each selected package's local version independently. Use
`tools/upgrade_release_line 0.9 --check` to validate that packages remain on
the intended compatibility line.

## Initial PyPI bootstrap

GitHub publishing uses the shared `pypi` environment. PyPI must have a matching
trusted publisher for each project that will be uploaded.

For the initial bootstrap, PyPI currently allows only one pending trusted
publisher per GitHub repo/workflow/environment. Use manual workflow dispatch
with one selected package at a time, creating the matching pending publisher
before each run:

1. `core` (`arbiter-core`)
2. `imap` (`arbiter-imap`)
3. `smtp` (`arbiter-smtp`)
4. `meta:all` (`arbiter-suite`)
5. `skill` (`arbiter-skill`)
6. `skill:linux-amd64` (`arbiter-skill-linux-amd64`)
7. `skill:linux-arm64` (`arbiter-skill-linux-arm64`)
8. `skill:darwin-amd64` (`arbiter-skill-darwin-amd64`)
9. `skill:darwin-arm64` (`arbiter-skill-darwin-arm64`)
10. `skill:windows-amd64` (`arbiter-skill-windows-amd64`)
11. `skill:windows-arm64` (`arbiter-skill-windows-arm64`)

## Dev releases

Use manual workflow dispatch for dev package releases such as `0.9.0.dev1`:

- `release_line`: the `MAJOR.MINOR` line, such as `0.9`
- `publish_packages`: one key or a comma-separated key list
- `publish_to_pypi`: enable only when the matching PyPI trusted publisher is
  ready

Dev releases do not require release notes and do not create or update GitHub
releases.

## Final releases

For coordinated releases where all package versions follow the suite meta
package, publish a GitHub release with a tag like `v0.9.0`. The release workflow
validates the matching package release notes, publishes the selected
distributions to PyPI, and then edits the GitHub release with those notes.

For fine-grained final releases, use manual workflow dispatch:

- `release_line`: the `MAJOR.MINOR` line, such as `0.9`
- `publish_packages`: the package keys to publish, such as `smtp`
- `publish_to_pypi`: `true`

The workflow validates release notes only for final packages that will publish,
publishes the selected distributions to PyPI, and then creates or updates GitHub
release tags from each published package version, such as `v0.9.1`.

Additional meta packages, such as a future `meta:mail`, should follow the same
non-expanding package-key model.
