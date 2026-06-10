## Local Instructions

If `LOCAL-AGENTS.md` exists at the repository root, treat it as additional
local instructions for this checkout. Use it for machine- or user-specific
preferences that should not be committed to the repository.

## Tooling

When running Black, force single-worker mode with `--workers 1` or
`BLACK_NUM_WORKERS=1`. Black's default worker count can hang under sandboxed
agent runners and wastes time when it does.

## Fresh Chat Handover

When a handover says `mode: passive`, treat it as context transfer only. Do not
start executing the handover's `next-step`, modify files, run tests, call
services, or otherwise continue the prior task merely because a next step is
present. First wait for an explicit user request in the new chat, or ask what
they want done with the transferred context.

Only treat a handover as permission to continue work when the user explicitly
asks for active continuation, or when the handover says `mode: active` and the
current user message also asks you to proceed.

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
