---
title: Release Process
---

Arbiter publishes several Python distributions from one repository:

- `server`: `arbiter-server`, the real server runtime package
- plugin keys discovered from `plugins/*/pyproject.toml`, such as `imap` for
  `arbiter-imap` and `smtp` for `arbiter-smtp`
- `meta:all`: `arbiter-suite`, a zero-code dependency bundle for all real
  packages
- `client`: `arbiter-client`, the platform-tagged native client wheel set
- `skill`: `arbiter-skill`, the platform-neutral agent skill package that
  declares `arbiter-client` as its native-client companion wheel

Meta package keys do not expand to their dependencies. Selecting `meta:all`
publishes only the `arbiter-suite` package.

The native client and skill package versions come from `arbiter-server`. They
are generated wheel-only artifacts and do not have separate towncrier release
notes. The transitional Python CLI client is repo-local and is not published.

This page describes publishing mechanics only.

## News fragments

Final server, plugin, and meta releases require package-scoped towncrier release
notes for every package that will publish. Final client and skill releases use
generated draft GitHub Release notes because they are wheel-only artifacts tied
to the server version. Dev releases such as `0.9.0.dev1` do not consume release
notes.

Add fragments under the package that changed:

```text
server/newsfragments/123.feature.md
plugins/imap/newsfragments/123.bugfix.md
plugins/smtp/newsfragments/+smtp-only-change.feature.md
meta/arbiter-suite/newsfragments/+meta-package-change.feature.md
```

Use a GitHub issue or PR number when one exists. Use the `+` orphan prefix when
there is no issue or PR.

Before a final release, preview and build the notes for each package that will
publish:

```bash
.venv/bin/python -m towncrier build --draft --config server/pyproject.toml --version 0.9.0
.venv/bin/python -m towncrier build --yes --config server/pyproject.toml --version 0.9.0

.venv/bin/python -m towncrier build --draft --config plugins/imap/pyproject.toml --version 0.9.0
.venv/bin/python -m towncrier build --yes --config plugins/imap/pyproject.toml --version 0.9.0

.venv/bin/python -m towncrier build --draft --config plugins/smtp/pyproject.toml --version 0.9.0
.venv/bin/python -m towncrier build --yes --config plugins/smtp/pyproject.toml --version 0.9.0

.venv/bin/python -m towncrier build --draft --config meta/arbiter-suite/pyproject.toml --version 0.9.0
.venv/bin/python -m towncrier build --yes --config meta/arbiter-suite/pyproject.toml --version 0.9.0
```

Commit the updated `NEWS.md` files and removed fragments before publishing.

## Prepare and publish workflow

Final releases use a two-step GitHub Actions flow and are published from a
stable release branch for the release line. For example, the `0.9` stable line
should have a dedicated branch such as `release/0.9`; security and maintenance
patches for that line are backported to the same branch and published from
there. New development can continue on `main`, while the release branch owns
the package state, release notes, draft releases, and patch releases for its
stable line.

There are two release kinds:

- `new-release-line`: move the whole compatibility line, such as
  `0.9.0.dev2` to `0.9.0`; this always prepares all package targets together.
- `regular`: publish new plugins, updates to existing plugins, or updates to
  server/client packages already prepared on the current line.

1. Run **Prepare Release** with the target `release_line`,
   `release_kind`, `publish_packages`, and target branch.
2. If version files or package release notes need changes, the workflow opens
   or updates a release preparation PR and runs the full platform integration
   suite on that prepared branch.
3. Review and merge the release preparation PR after the PR contents and
   integration run are approved.
4. Run **Prepare Release** again on the stable release branch. With the release
   files already prepared, it creates or refreshes the draft GitHub Release tags
   for the selected final packages and runs the full integration suite on that
   branch.
5. Run **Publish** with the same `release_line` and `publish_packages`. Publish
   validates the prepared draft releases, reruns lint, the full platform unit
   matrix, build/smoke checks, and the full platform integration gates from
   the same stable release branch, uploads to PyPI only after those gates pass,
   then promotes the draft GitHub Releases.

Because Arbiter can publish several packages at once, prepare and publish are
keyed by package selection rather than by a single release tag. Packages with
the same final version share a draft GitHub Release tag, such as `v0.9.0`.
Independent plugin patch releases can create separate draft release tags in the
same prepare run.

For regular releases, `publish_packages=all` means all selected packages whose
local version is newer than PyPI, or whose PyPI project does not exist yet.
Use a comma-separated package key list to release a specific target set.

Dev releases such as `0.9.0.dev1` do not require package release notes or draft
GitHub Releases. Use **Publish** directly for dev package uploads after normal
CI is green and the matching PyPI trusted publisher is ready.

## Publish planning

The publish workflow builds the selected distributions, then runs:

```bash
tools/plan_pypi_publish --prepare-output-dir
```

The planner compares local versions with PyPI and copies only packages whose
local version is newer, or whose PyPI project does not exist yet, into
`dist-publish/`. It rejects local versions that are older than PyPI.

Limit the publish set with package keys:

```bash
tools/plan_pypi_publish --packages server --prepare-output-dir
tools/plan_pypi_publish --packages server,imap --prepare-output-dir
tools/plan_pypi_publish --packages smtp --prepare-output-dir
tools/plan_pypi_publish --packages client --prepare-output-dir
tools/plan_pypi_publish --packages skill --prepare-output-dir
```

The planner discovers plugin package keys from `plugins/*/pyproject.toml` and
reads each selected package's local version independently. Use
`tools/upgrade_release_line 0.9 --check` to validate that packages remain on
the intended compatibility line.

## Initial PyPI bootstrap

GitHub publishing uses the shared `pypi` environment. PyPI must have a matching
trusted publisher for each project that will be uploaded.

For the initial bootstrap, PyPI currently allows only one pending trusted
publisher per GitHub repo/workflow/environment. Use manual workflow dispatch
with one selected package at a time, creating the matching pending publisher
before each run:

1. `server` (`arbiter-server`)
2. `imap` (`arbiter-imap`)
3. `smtp` (`arbiter-smtp`)
4. `meta:all` (`arbiter-suite`)
5. `client` (`arbiter-client`)
6. `skill` (`arbiter-skill`)

## Dev releases

Use **Publish** manual workflow dispatch for dev package releases such as
`0.9.0.dev1`:

- `release_line`: the `MAJOR.MINOR` line, such as `0.9`
- `publish_packages`: one key or a comma-separated key list
- `publish_to_pypi`: enable only when the matching PyPI trusted publisher is
  ready

Dev releases do not require release notes and do not create or update GitHub
releases.

Use `tools/bump_release_version` before publishing when package versions need
to move. The command updates package `version` fields and the matching
`arbiter-suite` exact dependency pins. It accepts the same package keys as the
release build and publish tooling: `all`, `server`, plugin keys such as `smtp`
or `imap`, `meta:all`, `client`, and `skill`.

```bash
tools/bump_release_version --bump patch
tools/bump_release_version --packages smtp --bump patch
tools/bump_release_version --bump dev
tools/bump_release_version --bump minor
tools/bump_release_version 0.9.1.dev1 --check
```

`--bump dev` increments only the dev component, such as `0.9.0.dev2` to
`0.9.0.dev3`. `--bump patch` advances to the next patch dev release, such as
`0.9.0.dev2` to `0.9.1.dev1`. `--bump minor` advances to the next minor dev
line, such as `0.9.0.dev2` to `0.10.0.dev1`; minor bumps must select all
packages because they also update plugin `SERVER_API_VERSION` declarations.

Version bumps keep the server version family together. Selecting any of
`server`, `client`, or `skill` updates the shared server/client/skill version
state and the suite pins for `arbiter-server` and `arbiter-client`. Build,
publish, and publish planning still use the package keys literally, so
`--packages skill` can publish only the skill wheel after the shared version
files have been prepared.

Selective patch and dev bumps are for independent package releases. For a
plugin-only bump, the tool updates that plugin's version and the suite pin, but
does not raise the plugin's `arbiter-server` lower bound. If the server and a
plugin are selected together, the selected plugin's server lower bound moves to
the new server version. `--check` verifies an explicit target version and does
not infer a bump.

## Final releases

For new release lines where all package versions follow the suite meta
package, run **Prepare Release** with `release_kind=new-release-line` and
`publish_packages=all`, then run **Publish** with `publish_packages=all`.

For regular final releases, use `release_kind=regular` in **Prepare Release**:

- `release_line`: the `MAJOR.MINOR` line, such as `0.9`
- `publish_packages`: `all` for all new publishable targets, or a package key
  list such as `smtp,client`
- `publish_to_pypi`: `true` in the **Publish** workflow

Prepare validates or builds package release notes only for final packages that
will publish, adds generated entries for final client and skill artifacts, then
creates draft GitHub Release tags from each published package version, such as
`v0.9.1`. Publish requires those releases to still be drafts and to point at the
commit being published. Publish also reruns the full platform integration
matrix, full platform unit matrix, and Docker deploy integration, and the PyPI
upload job depends on those jobs completing successfully.

Additional meta packages, such as a future `meta:mail`, should follow the same
non-expanding package-key model.
