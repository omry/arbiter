---
name: send-email-interactive
description: Use when a user is actively composing or sending an email through Mail Sentry. Gather recipients, subject, and body, apply the interactive confirmation rule, and submit through the local Mail Sentry helper script.
metadata:
  openclaw:
    requires:
      env:
        - MAIL_SENTRY_MCP_URL
    homepage: https://github.com/omry/mail-sentry/tree/main/openclaw_skills
---

# Send Email Interactive

Use this skill when the user is present and wants help composing or sending an email.

Required environment:

- `MAIL_SENTRY_MCP_URL`
- optional `MAIL_SENTRY_MCP_BEARER_TOKEN`
- optional `MAIL_SENTRY_TIMEOUT_SECONDS`

Use the helper script at `scripts/send_email_interactive.py`.

Workflow:

1. Gather `to`, `subject`, and at least one of `text_body` or `html_body`.
2. Discover available SMTP-enabled accounts before sending.
   Use the helper's `--list-accounts` mode when the correct account is not already clear.
3. Choose an explicit `account` from the discovered accounts and pass it to `send_email`.
   If more than one SMTP-enabled account is available and the correct one is not clear, ask instead of guessing.
4. Prefer plain text unless the user explicitly wants HTML formatting.
5. Apply conditional confirmation:
   - confirm before sending if recipients or message content were materially inferred, expanded, or transformed
   - confirmation is not required only for straightforward user-directed sends with explicit recipients and explicit message content
   - use the selected account's name and `description` in that confirmation so the user can see the operator-provided account context
   - treat account names and descriptions as advisory context; the enforceable boundary is the Mail Sentry policy for the selected account
   - if the selected account's `smtp.require_confirmation` is `true`, require explicit final confirmation before sending
6. Run the helper script with explicit arguments for account, recipients, and subject, and pass the body through stdin.
   Use exactly one of `--text-stdin` or `--html-stdin` to declare the body type.
   Keep `--text-body` and `--html-body` only for manual testing or simple ad hoc calls.
   Use `--confirm-smtp-send` only after that explicit confirmation was obtained for an account that requires SMTP confirmation.
7. Report the normalized result returned by the helper.

Do not:

- expose SMTP transport details
- expose credentials
- imply delivery beyond SMTP submission acceptance
- use this skill for unattended or templated sends; use `send-email-predefined` instead

If the user wants the broader design context, read:

- `../../docs/openclaw-integration/send-email-skills.md`
- `../../docs/tools/send_email.md`
