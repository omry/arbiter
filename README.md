# Arbiter

|  | Description |
| --- | --- |
| Project | [![PyPI version](https://badge.fury.io/py/arbiter-suite.svg)](https://badge.fury.io/py/arbiter-suite)[![Downloads](https://pepy.tech/badge/arbiter-core/month)](https://pepy.tech/project/arbiter-core)![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue) |
| Packages | [![arbiter-core](https://img.shields.io/pypi/v/arbiter-core.svg?label=arbiter-core)](https://pypi.org/project/arbiter-core/)[![arbiter-smtp](https://img.shields.io/pypi/v/arbiter-smtp.svg?label=arbiter-smtp)](https://pypi.org/project/arbiter-smtp/)[![arbiter-imap](https://img.shields.io/pypi/v/arbiter-imap.svg?label=arbiter-imap)](https://pypi.org/project/arbiter-imap/) |
| Code quality | [![CI](https://github.com/omry/arbiter/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/omry/arbiter/actions/workflows/ci.yml) |
| Docs and support | [![Documentation](https://img.shields.io/badge/docs-arbiter.yadan.net-blue)](https://arbiter.yadan.net/) |
| License | [![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) |

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
- `.venv/bin/python -m pip install -r requirements-dev.txt`

The repository root is a workspace, not an Arbiter runtime package. The dev
requirements file installs `core`, `smtp`, and `imap` editably so the `arbiter`
and `arbiter-server` commands come from this checkout.

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

The user-facing documentation lives in [website/docs/](website/docs/). The
root [docs/](docs/) directory is reserved for internal planning and future
design notes:

- [docs/BACKLOG.md](docs/BACKLOG.md)
- [docs/testing_backlog.md](docs/testing_backlog.md)
- [docs/release-readiness.md](docs/release-readiness.md)
- [docs/future/](docs/future/)

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

See [website/docs/operate/configuration-model.md](website/docs/operate/configuration-model.md)
for the generated file layout and composition flow.

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
arbiter info arbiter.mcp_url=http://127.0.0.1:8025/mcp
arbiter info plugins arbiter.mcp_url=http://127.0.0.1:8025/mcp
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
