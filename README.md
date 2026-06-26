# Arbiter

|  | Description |
| --- | --- |
| Meta packages | [![arbiter-suite](https://img.shields.io/pypi/v/arbiter-suite.svg?label=arbiter-suite)](https://pypi.org/project/arbiter-suite/) |
| Core packages | [![arbiter-server](https://img.shields.io/pypi/v/arbiter-server.svg?label=arbiter-server)](https://pypi.org/project/arbiter-server/)[![arbiter-client](https://img.shields.io/pypi/v/arbiter-client.svg?label=arbiter-client)](https://pypi.org/project/arbiter-client/)[![arbiter-skill](https://img.shields.io/pypi/v/arbiter-skill.svg?label=arbiter-skill)](https://pypi.org/project/arbiter-skill/) |
| Plugins | [![arbiter-imap](https://img.shields.io/pypi/v/arbiter-imap.svg?label=arbiter-imap)](https://pypi.org/project/arbiter-imap/)[![arbiter-smtp](https://img.shields.io/pypi/v/arbiter-smtp.svg?label=arbiter-smtp)](https://pypi.org/project/arbiter-smtp/) |
| Python | ![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13%20%7C%203.14-blue) |
| Docs and support | [![Website](https://img.shields.io/badge/website-arbiter.yadan.net-blue)](https://arbiter.yadan.net/)[![Zulip chat](https://img.shields.io/badge/chat-Zulip-2e77d0?logo=zulip)](https://hydra-framework.zulipchat.com/#narrow/stream/arbiter) |
| General | [![CI](https://github.com/omry/arbiter/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/omry/arbiter/actions/workflows/ci.yml)[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) |

Arbiter is a capability firewall between AI agents and services. Today it exposes policy-controlled access through an Arbiter server and client CLI; additional interfaces may be added later. The current service surface covers sending mail over SMTP and reading IMAP folders through explicit account policies.

## Project Status

Current implementation status:

- Arbiter server over a native HTTP API
- capability discovery with SMTP and IMAP account and operation metadata
- SMTP submission with configured sender identity, TLS/auth settings, text/HTML bodies, and Bcc kept out of message headers
- IMAP list/get/search/move/mark-read/delete tools scoped to configured accounts and folders
- `arbiter.account.<service>` and reusable `arbiter.policy.<service>` objects
  for per-service account policy
- native `arbiter` client CLI and `arbiter-server` server/operator CLI
- Reploy blueprint material for staged and installed Docker deployments

Known open gaps:

- durable audit storage is parked for post-v1, while startup/runtime logging is
  the v1 observability focus
- normalized error-code responses are still a design contract, while the
  implementation currently surfaces Python and transport errors
- the agent-facing skill integration path is intentionally not implemented in
  this repository yet

## Development

Create and use the repo-local virtualenv with:

- `python3 -m venv .venv`
- `.venv/bin/python -m pip install --upgrade pip`
- `.venv/bin/python -m pip install -r requirements-dev.txt`

The repository root is a workspace, not an Arbiter runtime package. The dev
requirements file installs `server`, `plugins/smtp`, and `plugins/imap`
editably so the `arbiter-server` command and service plugins come from this
checkout.

Install the native client separately when you need the repo-local `arbiter`
command:

- `.venv/bin/python -m pip install -e client`

Run the test suite from the repo root with:

- `.venv/bin/python -m nox -s tests`
- `.venv/bin/python -m nox -s lint`

The `lint` session runs both `black --check` and `pyrefly check`.

For focused local runs without `nox`, use the same environment directly, for example:

- `.venv/bin/python -m pytest server/tests/unit/test_config.py`
- `.venv/bin/python -m pytest server/tests/unit/test_app.py`

The Docusaurus website lives in [website/](website/):

- `cd website && npm install`
- `cd website && npm run start`
- `cd website && npm run build`

The native client implementation lives in [client/go-cli/](client/go-cli/).
Build all default release targets from the repo root with:

- `tools/build_go_client --clean`

This writes stripped Linux, macOS, and Windows binaries for `amd64` and `arm64`
under `client/go-cli/dist/`. Limit the matrix with one or more `--target
GOOS-GOARCH` arguments, for example `tools/build_go_client --target
linux-arm64`; pass `--debug` to keep debug symbols.

Package the platform-neutral agent skill with:

- `tools/build_release_dists --packages skill`

This writes the platform-neutral `arbiter-skill` wheel under `dist/`. The skill
declares `arbiter-client` as an Agent Skill Installer companion wheel; ASI lets
pip select the current platform's native client wheel during install.

The native client can also be packaged as the `arbiter-client` PyPI project:

- `tools/build_release_dists --packages client`

This builds one platform-tagged wheel per native target. Each wheel installs the
native `arbiter` executable directly as a wheel script; it does not contain a
Python wrapper.

The user-facing documentation lives in [website/docs/](website/docs/). The
root [docs/](docs/) directory is reserved for internal planning and future
design notes:

- [docs/BACKLOG.md](docs/BACKLOG.md)
- [docs/testing_backlog.md](docs/testing_backlog.md)
- [docs/release-readiness.md](docs/release-readiness.md)
- [docs/future/](docs/future/)

## Local Native HTTP Run

For local Codex or VS Code integration, run Arbiter over native HTTPS and
point the client at:

```text
https://127.0.0.1:8075
```

Arbiter does not ship a runnable service config. Bootstrap a Hydra
config, edit it, then run the server. The default config directory is
`~/.arbiter`; pass `--config-dir <dir>` before a subcommand to use a different
location. `config.local/` is ignored scratchspace for repository-local
development.
Plugin-owned object templates are created by the plugin command surface:

```bash
arbiter-server bootstrap --server
arbiter-server bootstrap --plugin smtp --account primary
```

`${oc.env:...}` reads the process environment that your shell, supervisor,
container runtime, or secret manager provides. For local runs, the root config
can name one optional dotenv-style file to load before composition.

See [website/docs/operate/configuration-model.md](website/docs/operate/configuration-model.md)
for the generated file layout and composition flow.

For local development, a shell-owned env file can be useful:

```bash
# ~/.arbiter/local.env
SMTP_PRIMARY_ACCOUNT_HOST=smtp.example.com
# SMTP_PRIMARY_ACCOUNT_PORT=587
SMTP_PRIMARY_ACCOUNT_USERNAME=agent@example.com
SMTP_PRIMARY_ACCOUNT_PASSWORD=change-me
IMAP_PRIMARY_ACCOUNT_HOST=imap.example.com
# IMAP_PRIMARY_ACCOUNT_PORT=993
IMAP_PRIMARY_ACCOUNT_USERNAME=agent@example.com
IMAP_PRIMARY_ACCOUNT_PASSWORD=change-me
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
validating a config. Once the server is running, point the client CLI at the
server URL. The client accepts Arbiter's local self-signed TLS certificates by
default; configure `arbiter.tls_ca_file` when you want certificate verification
against a specific certificate authority file:

```bash
arbiter info server arbiter.url=https://127.0.0.1:8075
arbiter plugins arbiter.url=https://127.0.0.1:8075
```

The client can also read the endpoint from a small config file:
`~/.arbiter/arbiter-client.yaml`.

```yaml
arbiter:
  url: https://127.0.0.1:8075
```

Override config values with Hydra-style `key=value` arguments after the
command, or bootstrap the client config:

```bash
arbiter bootstrap client arbiter.url=https://127.0.0.1:8075
```

IMAP operations use folder-scoped UIDs returned by `imap:list_messages` and
`imap:search_messages`; pass those ids back to `imap:get_message`,
`imap:move_message`, `imap:mark_message_read`, or `imap:delete_message` with
the same account and folder.

## License

Arbiter is distributed under the MIT License. See [LICENSE](LICENSE).
