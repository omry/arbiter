# OpenClaw Integration

## Purpose

Document the interim integration path for using Agent Arbiter from an OpenClaw installation before OpenClaw has native MCP support.

## Current gap

OpenClaw is the intended consumer for this server, but it does not currently support MCP as a first-class integration surface.

That means Agent Arbiter needs a temporary compatibility layer if OpenClaw should use it before native MCP support is available.

## Selected interim direction

The selected path is:

`OpenClaw wrapper skills -> Agent Arbiter over HTTP`

The OpenClaw skill runtime should speak a minimal Agent Arbiter-specific MCP subset over HTTP.

This is intentionally a narrow shim rather than a generic MCP client layer, and it avoids introducing a separate temporary Agent Arbiter business API that would also need to be retired later.

The shim should stay Agent Arbiter-specific, but its internal shape should allow additional Agent Arbiter tools to be added without redesigning the entire OpenClaw integration around SMTP-only assumptions.

## Planned skill split

The temporary OpenClaw integration should use two separate wrapper skill surfaces:

- `send_email_interactive` for user-in-the-loop sending with conditional confirmation
- `send_email_predefined` for unattended sending from preapproved templates or profiles without confirmation

This split keeps attended and unattended behavior separate instead of relying on one mixed skill to infer the correct safety mode.

Those send skills are currently the only OpenClaw-facing use of the shim. Agent Arbiter now exposes IMAP tools, but the temporary OpenClaw wrapper skills do not cover them yet.

## Temporary status

The protocol shim is temporary:

- the skill-local MCP-over-HTTP shim is temporary

Once OpenClaw supports native MCP, OpenClaw should call Agent Arbiter directly and the temporary shim should be retired.

The two skill modes may still remain useful after native MCP support exists:

- `send_email_interactive`
- `send_email_predefined`

In that later state, they would become thinner OpenClaw wrappers over native MCP rather than wrappers over the temporary shim.

## Related decision

See [wrapper-skill-decision.md](wrapper-skill-decision.md) for the decision record, motivation, and consequences.

See [send-email-skills.md](send-email-skills.md) for the concrete temporary skill design for `send_email_interactive` and `send_email_predefined`.

See [../../openclaw_skills/README.md](../../openclaw_skills/README.md) for automatic and manual skill installation into the OpenClaw container.
