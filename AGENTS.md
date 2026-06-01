## Local Instructions

If `LOCAL-AGENTS.md` exists at the repository root, treat it as additional
local instructions for this checkout. Use it for machine- or user-specific
preferences that should not be committed to the repository.

## Release Notes

For user-facing changes, add towncrier news fragments for the affected
published package unless the change is explicitly not release-note-worthy.
Use `core/newsfragments/` for `agent-arbiter-core`, `imap/newsfragments/` for
`agent-arbiter-imap`, `smtp/newsfragments/` for `agent-arbiter-smtp`, and root
`newsfragments/` for the `agent-arbiter` all-in-one meta package. `meta:all`
means only the zero-code dependency bundle that installs all real packages; it
does not mean publishing core or plugin packages. Use a GitHub issue or PR
number when one exists, or the `+` orphan prefix for untracked changes, such as
`+short-description.feature.md`.
