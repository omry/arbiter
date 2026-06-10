## Local Instructions

If `LOCAL-AGENTS.md` exists at the repository root, treat it as additional
local instructions for this checkout. Use it for machine- or user-specific
preferences that should not be committed to the repository.

## Tooling

When running Black, force single-worker mode with `--workers 1` or
`BLACK_NUM_WORKERS=1`. Black's default worker count can hang under sandboxed
agent runners and wastes time when it does.

## Release Notes

For user-facing changes, add towncrier news fragments for the affected
published package unless the change is explicitly not release-note-worthy.
Use `server/newsfragments/` for `arbiter-server`,
`plugins/imap/newsfragments/` for `arbiter-imap`,
`plugins/smtp/newsfragments/` for `arbiter-smtp`, and
`meta/arbiter-suite/newsfragments/` for the `arbiter-suite` all-in-one meta
package. `meta:all` means only the zero-code dependency bundle that installs
all real packages; it does not mean publishing server or plugin packages. Use a
GitHub issue or PR number when one exists, or the `+` orphan prefix for
untracked changes, such as `+short-description.feature.md`.
