---
name: send-email-predefined
description: Use for unattended or preapproved Mail Sentry sends driven by deployment-owned templates or profiles. Resolve a configured template, validate allowed parameters, and submit without a final confirmation step.
metadata:
  openclaw:
    requires:
      env:
        - MAIL_SENTRY_MCP_URL
    homepage: https://github.com/omry/mail-sentry/tree/main/openclaw_skills
---

# Send Email Predefined

Use this skill for unattended or preapproved email sends.

Required environment:

- `MAIL_SENTRY_MCP_URL`
- optional `MAIL_SENTRY_MCP_BEARER_TOKEN`
- optional `MAIL_SENTRY_TIMEOUT_SECONDS`

Use the helper script at `scripts/send_email_predefined.py`.

Workflow:

1. Accept only a configured template/profile name and its allowed parameters.
2. Load the local template registry from `templates.json` next to this skill.
3. Reject the request if it tries to introduce arbitrary recipients, arbitrary bodies, or unsupported parameters.
4. Resolve the final `send_email` payload from the configured template/profile, including the fixed account from `templates.<NAME>.account`.
5. Submit immediately without a final confirmation step.
6. Report the normalized result returned by the helper.

Do not:

- degrade into freeform composition
- ask for a final confirmation step
- expose SMTP transport details
- expose credentials

If the user wants the template registry shape, read `template-operator-readme.md`.

If the user wants the broader design context, read:

- `../../docs/openclaw-integration/send-email-skills.md`
- `../../docs/tools/send_email.md`
