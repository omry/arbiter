# Arbiter

Arbiter is a capability firewall between AI agents and services. Today it exposes policy-controlled access through MCP and a client CLI; additional interfaces may be added later. The current service surface covers sending mail over SMTP and reading IMAP folders through explicit account policies.

## Project Status

Current implementation status:

- MCP server over stdio, SSE, or streamable HTTP via FastMCP
- capability discovery with SMTP and IMAP account and operation metadata
- SMTP submission with configured sender identity, TLS/auth settings, text/HTML bodies, and Bcc kept out of message headers
- IMAP list/get/search/move/mark-read/delete tools scoped to configured accounts and folders
- `arbiter.account.<service>` and reusable `arbiter.policy.<service>` objects
  for per-service account policy
- `arbiter` client CLI and `arbiter-server` server/operator CLI
- Docker deployment files for a standard SMTP gateway and a hardened read-only IMAP variant

Known open gaps:

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

The `lint` session runs both `black --check` and the Arbiter `mypy` passes.

For focused local runs without `nox`, use the same environment directly, for example:

- `.venv/bin/python -m pytest core/tests/unit/test_config.py`
- `.venv/bin/python -m pytest core/tests/unit/test_app.py`

The Docusaurus website lives in [website/](website/):

- `cd website && npm install`
- `cd website && npm run start`
- `cd website && npm run build`

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
- [docs/tools/account_summaries.md](docs/tools/account_summaries.md)
- [docs/tools/smtp_send_email.md](docs/tools/smtp_send_email.md)
- [docs/tools/imap.md](docs/tools/imap.md)

## Local Streamable HTTP Run

For local Codex or VS Code integration, run Arbiter as a streamable HTTP MCP
server and point the client at:

```text
http://127.0.0.1:8025/mcp
```

Arbiter does not ship a runnable service config. Bootstrap a Hydra
config, edit it, then run the server. The default config directory is
`~/.arbiter`; pass `--config-dir <dir>` before a subcommand to use a different
location. `config.local/` is ignored scratchspace for repository-local
development.
Plugin-owned object templates are created by the plugin command surface:

```bash
arbiter-server bootstrap arbiter
arbiter-server bootstrap plugin smtp account primary
```

`${oc.env:...}` reads the process environment that your shell, supervisor,
container runtime, or secret manager provides. For local runs, the root config
can name one optional dotenv-style file to load before composition.

See [docs/config_bootstrap.md](docs/config_bootstrap.md) for the generated file
layout and composition flow.

For local development, a shell-owned env file can be useful:

```bash
# ~/.arbiter/local.env
SMTP_PRIMARY_ACCOUNT_USERNAME=agent@example.com
SMTP_PRIMARY_ACCOUNT_PASSWORD=change-me
ARBITER_IMAP_USERNAME=agent@example.com
ARBITER_IMAP_PASSWORD=change-me
```

Point the root config at the env file:

```yaml
arbiter:
  env_file: local.env
```

Existing process environment variables take precedence over values from the env
file. Relative paths are resolved from the config directory.

Build or refresh that file from the active config:

```bash
arbiter-server env bootstrap
arbiter-server env check
```

`env bootstrap` keeps existing assignments, adds missing config references, and
groups entries under sorted `# arbiter-*` blocks plus `# miscellaneous`.
If `arbiter.env_file` is not set yet, it adds `arbiter.env_file: .env` to the
root config first.

Then run from this directory:

```bash
arbiter-server config check
arbiter-server serve
```

`config check` and `serve` require at least one configured service account.

Use `arbiter-server plugins list` to inspect installed service plugins before
validating a config. Once the server is running, use the client CLI against the
MCP endpoint:

```bash
arbiter mcp tools arbiter.mcp_url=http://127.0.0.1:8025/mcp
arbiter cap arbiter.mcp_url=http://127.0.0.1:8025/mcp
arbiter accounts list arbiter.mcp_url=http://127.0.0.1:8025/mcp
```

The client can also read the endpoint from a small config file:
`~/.arbiter/arbiter-client.yaml`.

```yaml
arbiter:
  mcp_url: http://127.0.0.1:8025/mcp
```

Override config values with Hydra-style `key=value` arguments after the
command, or bootstrap the client config:

```bash
arbiter bootstrap client arbiter.mcp_url=http://127.0.0.1:8025/mcp
```

IMAP operations use folder-scoped UIDs returned by `imap:list_messages` and
`imap:search_messages`; pass those ids back to `imap:get_message`,
`imap:move_message`, `imap:mark_message_read`, or `imap:delete_message` with
the same account and folder.

## Read-Only Real Inbox Docker Run

For a hardened Docker setup that reads a single real IMAP folder with Docker
secrets and no SMTP access, see:

- [deploy/readonly-imap/README.md](deploy/readonly-imap/README.md)

## License

Arbiter is distributed under the MIT License. See [LICENSE](LICENSE).
