# Agent Arbiter

Agent Arbiter is a policy-controlled MCP gateway for exposing configured services to agents. The current service surface covers sending mail over SMTP and reading IMAP folders through explicit account policies.

## Project Status

Current implementation status:

- MCP server over stdio, SSE, or streamable HTTP via FastMCP
- account discovery with SMTP and IMAP capability metadata
- SMTP submission with configured sender identity, TLS/auth settings, text/HTML bodies, and Bcc kept out of message headers
- IMAP list/get/search/move/mark-read/delete tools scoped to configured accounts and folders
- `account_access_profile` as the current shared policy object for per-service SMTP and IMAP policy
- Docker deployment files for a standard SMTP gateway and a hardened read-only IMAP variant
- temporary OpenClaw wrapper skills for SMTP send flows

Known open gaps:

- SMTP idempotency config is reserved for future work; the server fails closed
  at startup if unsupported idempotency options are configured
- durable audit storage is parked for post-v1, while startup/runtime logging is
  the v1 observability focus
- normalized error-code responses are still a design contract, while the
  implementation currently surfaces Python/MCP errors
- OpenClaw wrapper skills currently cover SMTP send flows, not the IMAP tools

## Development

Create and use the repo-local virtualenv with:

- `python3 -m venv .venv`
- `.venv/bin/python -m pip install --upgrade pip`
- `.venv/bin/python -m pip install -e ".[dev]"`

Run the test suite from the repo root with:

- `.venv/bin/python -m nox -s tests`
- `.venv/bin/python -m nox -s lint`

The `lint` session runs both `black --check` and the Agent Arbiter `mypy` passes.

For focused local runs without `nox`, use the same environment directly, for example:

- `.venv/bin/python -m pytest tests/unit/test_config.py`
- `.venv/bin/python -m pytest tests/unit/test_app.py`

The design is documented in the `docs/` structure used by the MCP server template:

- [docs/overview.md](docs/overview.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/BACKLOG.md](docs/BACKLOG.md)
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

For local Codex or VS Code integration, run Agent Arbiter as a streamable HTTP MCP
server and point the client at:

```text
http://127.0.0.1:8025/mcp
```

One convenient local setup is to keep secrets in environment variables and run
with a throwaway Hydra config outside the repository:

```yaml
# /tmp/agent-arbiter-local.yaml
defaults:
  - agent_arbiter_app_config_schema
  - _self_

server:
  transport: streamable-http
  host: 127.0.0.1
  port: 8025
  path: /mcp

mail:
  account_access_profiles:
    bot:
      services:
        smtp:
          require_confirmation: false
          recipient_policy:
            allowed_domain_patterns:
              - example.com
        imap:
          allow_read: true
          allow_search: true
          allow_move: true
          allow_delete: false
          confirmation_required: []
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

etc:
  mailserver:
    smtp_host: ${oc.env:AGENT_ARBITER_SMTP_HOST}
    smtp_port: ${oc.env:AGENT_ARBITER_SMTP_PORT,587}
    imap_host: ${oc.env:AGENT_ARBITER_IMAP_HOST}
    imap_port: ${oc.env:AGENT_ARBITER_IMAP_PORT,993}

services:
  smtp:
    accounts:
      primary:
        host: ${etc.mailserver.smtp_host}
        port: ${etc.mailserver.smtp_port}
        authenticate: true
        username: ${oc.env:AGENT_ARBITER_SMTP_USERNAME}
        password: ${oc.env:AGENT_ARBITER_SMTP_PASSWORD}
        from_email: ${oc.env:AGENT_ARBITER_SMTP_FROM_EMAIL}
        from_name: ${oc.env:AGENT_ARBITER_SMTP_FROM_NAME,Agent Arbiter}
        tls: ${oc.env:AGENT_ARBITER_SMTP_TLS,starttls}
        verify_peer: ${oc.env:AGENT_ARBITER_SMTP_VERIFY_PEER,true}
  imap:
    accounts:
      primary:
        host: ${etc.mailserver.imap_host}
        port: ${etc.mailserver.imap_port}
        username: ${oc.env:AGENT_ARBITER_IMAP_USERNAME}
        password: ${oc.env:AGENT_ARBITER_IMAP_PASSWORD}
        tls: ${oc.env:AGENT_ARBITER_IMAP_TLS,implicit}
        verify_peer: ${oc.env:AGENT_ARBITER_IMAP_VERIFY_PEER,true}
        default_folder: INBOX
        folders:
          INBOX:
            description: Primary inbox.
          Archive:
            description: Archive folder.
```

Then run from this directory:

```bash
python -m agent_arbiter --config-path /tmp --config-name agent-arbiter-local
```

The IMAP tools use folder-scoped UIDs returned by `list_messages` and
`search_messages`; pass those ids back to `get_message`, `move_message`,
`mark_message_read`, or `delete_message` with the same account and folder.

## Read-Only Real Inbox Docker Run

For a hardened Docker setup that reads a single real IMAP folder with Docker
secrets and no SMTP access, see:

- [deploy/readonly-imap/README.md](deploy/readonly-imap/README.md)
