---
title: Prepare Docker Deployment
---

Prepare and test the deployment directory as an unprivileged operator. This
phase writes files, config, and env locally; it can start the staged container
for smoke testing, but it does not install anything under `/opt`.

## Create the directory

```bash
arbiter-server deploy docker init
```

By default this creates `./arbiter-docker`, including an `arbiter-docker`
helper script for preparing and installing the Arbiter Docker container. `init`
refuses to overwrite existing managed files.

For an existing staging directory, refresh the generated deployment files with:

```bash
arbiter-server deploy docker update --force
```

<details>
<summary>Files created by init</summary>

Most operators should use the `arbiter-docker` helper and Arbiter config
commands instead of editing generated deployment files directly.

- `arbiter-docker`: local helper script for this deployment.
- `conf/`: deployment config directory.
- `requirements.txt`: exact package pins or explicit wheel paths installed
  inside the container.
- `wheels/`: prepared dependency wheelhouse.
- `docker.env`: Docker Compose/container wrapper settings.
- `compose.yaml`: generated Docker Compose service definition.
- `compose.override.yaml`: optional generated local Compose overrides.
- `.arbiter-deploy.json`: generated file manifest.

</details>

Prepared Docker directories are staged deployments. They use staging-specific
Docker names and ports so they can run next to an installed Arbiter. During
install, the copied directory is rewritten to the installed identity. For the
networking details, see [Networking](./networking.md).

## Prepare the bundle

The bundle is the selected Arbiter packages plus the prepared dependency
wheelhouse. Prepare it before configuring accounts.

### Select plugins

Choose the Arbiter service plugins for this deployment:

```bash
./arbiter-docker bundle list-plugins  # show supported plugins
./arbiter-docker bundle add imap      # IMAP support: receive email
./arbiter-docker bundle add smtp      # SMTP support: send email
./arbiter-docker bundle remove smtp   # remove SMTP if it is not needed
```

Use `bundle add arbiter-suite` or `bundle remove arbiter-suite` to add or
remove all plugins in the suite meta package.

### Build the wheelhouse

Prepare the dependency wheelhouse from the selected package set. This locks the
runtime install to the versions selected during staging and lets Docker create
or recreate the container without reaching PyPI:

```bash
./arbiter-docker bundle prepare
```

`bundle prepare` validates `requirements.txt` and builds a complete wheelhouse
from the configured package pins or wheel paths. Running it before config work
catches package resolution, Docker image, and wheel compatibility problems
early. Each run prunes stale wheels that are no longer part of the resolved
runtime install set.

### Upgrade package pins

For an existing prepared bundle, refresh package pins and rebuild the
wheelhouse:

```bash
./arbiter-docker bundle upgrade       # upgrade selected packages
./arbiter-docker bundle upgrade smtp  # upgrade one selected package
```

Skip this step when you want to keep the versions already recorded in
`requirements.txt`.

## Configure the deployment
Configure Arbiter and the accounts and policies for the enabled plugins. You
can bootstrap a new config and edit it, or copy in an existing config
directory.

From inside the `arbiter-docker` staging directory, bootstrap a config into
`./conf`, the deployment config directory:

```bash
# Bootstrap the main config.
arbiter-server --config-dir ./conf bootstrap arbiter
# wrote conf/arbiter-server.yaml
# wrote conf/arbiter/server.yaml
```

Then bootstrap one SMTP `bot` account:

```bash
arbiter-server --config-dir ./conf bootstrap plugin smtp account bot
# wrote conf/arbiter/account/smtp/bot.yaml
# wrote conf/arbiter/policy/smtp/bot_policy.yaml

# Edit the generated account and policy files, then activate the account.
arbiter-server --config-dir conf config activate account smtp bot
```

Then inspect the composed config with:

```bash
arbiter-server --config-dir conf config show
```

Other service plugins follow the same pattern: bootstrap the plugin account,
edit the generated account and policy files, activate the account, and rerun
`env bootstrap` if the new config references additional environment variables.

## Create and maintain the env file

After the config exists, bootstrap or update its env file:

```bash
arbiter-server --config-dir ./conf env bootstrap
```

Arbiter config files should reference secrets through environment variables,
for example `${oc.env:SMTP_BOT_ACCOUNT_PASSWORD}`. `env bootstrap` composes the
current config, finds those environment references, and creates or updates
`conf/.env` with placeholders for any missing values. Existing values are
preserved, so rerunning it is safe.

Run this again after adding plugins, accounts, or policies. New plugin config
can introduce new credential variables, and `env bootstrap` adds the new
placeholders without removing the values you already filled in.

Keep `docker.env` separate from `conf/.env`: `docker.env` controls the Compose
wrapper, while `conf/.env` is created by Arbiter env tooling and belongs
to the config package.

## Start and test

After adding config and env, start the staged service and smoke test the MCP
endpoint:

```bash
./arbiter-docker up
./arbiter-docker test
```

`up` prints the MCP URL for this staged directory. `test` calls `version_info`
through that URL and waits through transient startup connection failures.

You can also test through the normal Arbiter client. Use the MCP URL printed by
`up`:

```bash
arbiter mcp call version_info arbiter.mcp_url=http://127.0.0.1:18025/mcp
arbiter cap arbiter.mcp_url=http://127.0.0.1:18025/mcp
```

Once the staged service works locally, promote it to a host service with the
[Linux install](./linux-install.md) runbook.
