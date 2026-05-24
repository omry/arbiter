# Mail Sentry

Mail Sentry is a policy-controlled MCP mail gateway for sending mail and reading IMAP folders through explicit account policies.

## Project Status

Current implementation status:

- MCP server over stdio, SSE, or streamable HTTP via FastMCP
- account discovery with SMTP and IMAP capability metadata
- SMTP submission with configured sender identity, TLS/auth settings, text/HTML bodies, and Bcc kept out of message headers
- IMAP list/get/search/move/mark-read/delete tools scoped to configured accounts and folders
- account access profiles for SMTP send permission, IMAP read/search/move/delete gates, and IMAP flag visibility/write policy
- Docker deployment files for a standard SMTP gateway and a hardened read-only IMAP variant
- temporary OpenClaw wrapper skills for SMTP send flows

Known open gaps:

- configured SMTP rate limits, recipient-count limits, recipient allowlists/denylists, and idempotency are not enforced yet
- durable audit storage and normalized error-code responses are still design contracts, while the implementation currently surfaces Python/MCP errors
- OpenClaw wrapper skills currently cover SMTP send flows, not the IMAP tools

## Development

Run the test suite from the repo root with:

- `python -m nox -s tests`
- `python -m nox -s lint`

The `lint` session runs both `black --check` and the Mail Sentry `mypy` passes.

The design is documented in the `docs/` structure used by the MCP server template:

- [docs/overview.md](docs/overview.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/openclaw-integration/README.md](docs/openclaw-integration/README.md)
- [docs/openclaw-integration/wrapper-skill-decision.md](docs/openclaw-integration/wrapper-skill-decision.md)
- [docs/openclaw-integration/send-email-skills.md](docs/openclaw-integration/send-email-skills.md)
- [openclaw_skills/README.md](openclaw_skills/README.md)
- [docs/config.md](docs/config.md)
- [docs/policies.md](docs/policies.md)
- [docs/errors.md](docs/errors.md)
- [docs/todo.md](docs/todo.md)
- [docs/testing_backlog.md](docs/testing_backlog.md)
- [docs/tools/list_accounts.md](docs/tools/list_accounts.md)
- [docs/tools/send_email.md](docs/tools/send_email.md)
- [docs/tools/imap_extension.md](docs/tools/imap_extension.md)

## Local Streamable HTTP Run

For local Codex or VS Code integration, run Mail Sentry as a streamable HTTP MCP
server and point the client at:

```text
http://127.0.0.1:8025/mcp
```

One convenient local setup is to keep secrets in environment variables and run
with a throwaway Hydra config outside the repository:

```yaml
# /tmp/mail-sentry-local.yaml
defaults:
  - mail_sentry_app_config_schema
  - _self_

server:
  transport: streamable-http
  host: 127.0.0.1
  port: 8025
  path: /mcp

mail:
  account_access_profiles:
    bot:
      allow_smtp_send: true
      imap:
        allow_read: true
        allow_search: true
        allow_move: true
        allow_delete: false
        system_flags:
          seen: read_write
          flagged: read_only
          answered: read_only
          deleted: read_only
          draft: read_only
        user_flags: {}
  accounts:
    primary:
      description: Local bot mailbox.
      account_access_profile: bot
      smtp:
        host: ${oc.env:MAIL_SENTRY_SMTP_HOST}
        port: ${oc.env:MAIL_SENTRY_SMTP_PORT,587}
        authenticate: true
        username: ${oc.env:MAIL_SENTRY_SMTP_USERNAME}
        password: ${oc.env:MAIL_SENTRY_SMTP_PASSWORD}
        from_email: ${oc.env:MAIL_SENTRY_SMTP_FROM_EMAIL}
        from_name: ${oc.env:MAIL_SENTRY_SMTP_FROM_NAME,Mail Sentry}
        tls: ${oc.env:MAIL_SENTRY_SMTP_TLS,starttls}
        verify_peer: ${oc.env:MAIL_SENTRY_SMTP_VERIFY_PEER,true}
      imap:
        host: ${oc.env:MAIL_SENTRY_IMAP_HOST}
        port: ${oc.env:MAIL_SENTRY_IMAP_PORT,993}
        username: ${oc.env:MAIL_SENTRY_IMAP_USERNAME}
        password: ${oc.env:MAIL_SENTRY_IMAP_PASSWORD}
        tls: ${oc.env:MAIL_SENTRY_IMAP_TLS,implicit}
        verify_peer: ${oc.env:MAIL_SENTRY_IMAP_VERIFY_PEER,true}
        default_folder: INBOX
        folders:
          INBOX:
            description: Primary inbox.
          Archive:
            description: Archive folder.
```

Then run from this directory:

```bash
python -m mail_sentry --config-path /tmp --config-name mail-sentry-local
```

The IMAP tools use folder-scoped UIDs returned by `list_messages` and
`search_messages`; pass those ids back to `get_message`, `move_message`,
`mark_message_read`, or `delete_message` with the same account and folder.

## Read-Only Real Inbox Docker Run

For a hardened Docker setup that reads a single real IMAP folder with Docker
secrets and no SMTP access, see:

- [deploy/readonly-imap/README.md](deploy/readonly-imap/README.md)
