# Agent Arbiter

Agent Arbiter is a policy-controlled MCP gateway for exposing configured services to agents. The current service surface covers sending mail over SMTP and reading IMAP folders through explicit account policies.

## Project Status

Current implementation status:

- MCP server over stdio, SSE, or streamable HTTP via FastMCP
- account discovery with SMTP and IMAP capability metadata
- SMTP submission with configured sender identity, TLS/auth settings, text/HTML bodies, and Bcc kept out of message headers
- IMAP list/get/search/move/mark-read/delete tools scoped to configured accounts and folders
- `arbiter.account.<service>` and reusable `arbiter.policy.<service>` objects
  for per-service account policy
- `arbiter` client CLI and `agent-arbiter` server/operator CLI
- Docker deployment files for a standard SMTP gateway and a hardened read-only IMAP variant

Known open gaps:

- SMTP idempotency config is reserved for future work; the server fails closed
  at startup if unsupported idempotency options are configured
- durable audit storage is parked for post-v1, while startup/runtime logging is
  the v1 observability focus
- normalized error-code responses are still a design contract, while the
  implementation currently surfaces Python/MCP errors
- the agent-facing skill integration path is intentionally not implemented in
  this repository yet

## Development

Create and use the repo-local virtualenv with:

- `python3 -m venv .venv`
- `.venv/bin/python -m pip install --upgrade pip`
- `.venv/bin/python -m pip install -e ".[dev]" -e core -e smtp -e imap`

Run the test suite from the repo root with:

- `.venv/bin/python -m nox -s tests`
- `.venv/bin/python -m nox -s lint`

The `lint` session runs both `black --check` and the Agent Arbiter `mypy` passes.

For focused local runs without `nox`, use the same environment directly, for example:

- `.venv/bin/python -m pytest core/tests/unit/test_config.py`
- `.venv/bin/python -m pytest core/tests/unit/test_app.py`

The design is documented in the `docs/` structure used by the MCP server template:

- [docs/overview.md](docs/overview.md)
- [docs/architecture.md](docs/architecture.md)
- [docs/BACKLOG.md](docs/BACKLOG.md)
- [docs/config.md](docs/config.md)
- [docs/config_bootstrap.md](docs/config_bootstrap.md)
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

Agent Arbiter does not ship a runnable service config. Bootstrap a local Hydra
config into an explicit config directory, edit it, then pass that directory with
`--config-dir`; `config.local/` is ignored scratchspace for this purpose.
Plugin-owned object templates are created by the plugin command surface:

```bash
agent-arbiter --config-dir "$PWD/config.local" bootstrap arbiter
agent-arbiter --config-dir "$PWD/config.local" bootstrap plugin smtp account primary
```

`${oc.env:...}` reads the process environment that your shell, supervisor,
container runtime, or secret manager provides. For local runs, the root config
can name one optional dotenv-style file to load before composition.

See [docs/config_bootstrap.md](docs/config_bootstrap.md) for the generated file
layout and composition flow.

For local development, a shell-owned env file can be useful:

```bash
# config.local/local.env
SMTP_PRIMARY_ACCOUNT_USERNAME=agent@example.com
SMTP_PRIMARY_ACCOUNT_PASSWORD=change-me
AGENT_ARBITER_IMAP_USERNAME=agent@example.com
AGENT_ARBITER_IMAP_PASSWORD=change-me
```

Point the root config at the env file:

```yaml
arbiter:
  env_file: local.env
```

Existing process environment variables take precedence over values from the env
file. Relative paths are resolved from `--config-dir`.

Build or refresh that file from the active config:

```bash
agent-arbiter --config-dir "$PWD/config.local" env bootstrap
agent-arbiter --config-dir "$PWD/config.local" env check
```

`env bootstrap` keeps existing assignments, adds missing config references, and
groups entries under sorted `# agent-arbiter-*` blocks plus `# miscellaneous`.
If `arbiter.env_file` is not set yet, it adds `arbiter.env_file: .env` to the
root config first.

Then run from this directory:

```bash
agent-arbiter --config-dir "$PWD/config.local" --config-name config config check
agent-arbiter --config-dir "$PWD/config.local" --config-name config serve
```

`config check` and `serve` require at least one configured service account.

Use `agent-arbiter --config-dir "$PWD/config.local" plugins list` to inspect
installed service plugins before validating a config. Once the server is
running, use the client CLI against the MCP endpoint:

```bash
arbiter --url http://127.0.0.1:8025/mcp tools list
arbiter --url http://127.0.0.1:8025/mcp accounts list
```

The IMAP tools use folder-scoped UIDs returned by `list_messages` and
`search_messages`; pass those ids back to `get_message`, `move_message`,
`mark_message_read`, or `delete_message` with the same account and folder.

## Read-Only Real Inbox Docker Run

For a hardened Docker setup that reads a single real IMAP folder with Docker
secrets and no SMTP access, see:

- [deploy/readonly-imap/README.md](deploy/readonly-imap/README.md)
