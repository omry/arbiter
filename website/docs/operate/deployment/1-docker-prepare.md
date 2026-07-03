---
title: Prepare Docker Deployment
---

Prepare and test the deployment directory as an unprivileged operator. This
phase writes files, config, and env locally; it can start the staged container
for smoke testing, but it does not install anything under `/opt`.

## Install Reploy

Reploy is the deployment tool used by this flow. It creates the staging
directory, manages generated Docker files, runs deployment-scoped app commands,
and installs the checked staging deployment later.

Install Reploy before running Arbiter deployment commands:

```bash
curl -fsSL https://reploy.yadan.net/install.sh | sh
```

If you prefer to install through Python packaging, Reploy is also available on
PyPI:

```bash
python3 -m pip install reploy
```

The installed `reploy` command creates the staging directory and writes a
deployment-local `./arbiterctl` app control script into it.

## Create the directory

```bash
reploy stage arbiter-server
```

By default this creates `./reploy-staging`, including a deployment-local
`arbiterctl` script for controlling the staged Arbiter Docker service. `stage`
refuses to overwrite existing managed files.

For an existing staging directory, refresh the generated deployment files with:

```bash
reploy stage --update --dir reploy-staging
```

Use `--force` to replace generated files that have local edits.

<details>
<summary>Files created by stage</summary>

Most operators should use `reploy` for deployment management and the
deployment-local `./arbiterctl` script for staged app control instead of
editing generated deployment files directly.

- `conf/`: deployment config directory.
- `arbiterctl`: local app control script for this deployment.
- `.reploy/`: Reploy-managed files, including generated Compose config,
  Docker env, bundle option metadata, runtime requirements projection, state,
  manifest, vendored binary, runtime cache, and prepared bundle contents.
- `data/server/`: writable server-owned runtime state, such as the generated
  self-signed TLS certificate and private key.
- `data/plugins/`: writable server runtime state for plugins, such as
  idempotency records and temporary artifacts.

</details>

Prepared Docker directories are staged deployments. They use staging-specific
Docker names and ports so they can run next to an installed Arbiter. During
install, the copied directory is marked as the installed deployment.

## Select the bundle

The bundle is the container runtime install set: selected Arbiter packages plus
a local wheelhouse with their exact dependencies. Select the packages during
staging; Reploy builds or refreshes the installation bundle automatically when a
staging or app command needs it. Container starts then install from the
wheelhouse, isolating the deployment from PyPI package changes and eliminating
the Internet connection requirement for starting Arbiter.

### Select plugins

Choose the Arbiter service plugins for this deployment:

```bash
reploy bundle list-options          # show addable options and descriptions
reploy bundle add --name imap,smtp  # IMAP receive + SMTP send support
reploy bundle remove smtp           # remove SMTP if it is not needed
```

Use `bundle add --name arbiter-suite` or `bundle remove arbiter-suite` to add or
remove all plugins in the suite meta package. `arbiter-server` is the Reploy
blueprint; `arbiter-suite` is an Arbiter package bundle option.

### Automatic bundle preparation

Most operators do not need an explicit build step: `./arbiterctl up`,
`reploy test`, and `reploy app ...` prepare the installation bundle
automatically when the selected package set is missing or out of date.

Each automatic preparation prunes stale wheels that are no longer part of the
resolved runtime install set. When the staging directory is in an Arbiter
repository checkout, normal preparation refreshes local Arbiter wheels from
that checkout before resolving the wheelhouse.

### Upgrade selected versions

For an existing deployment, refresh selected package versions and mark the
installation bundle for refresh:

```bash
reploy bundle upgrade                         # upgrade selected packages
reploy bundle upgrade arbiter-smtp             # upgrade one package
reploy bundle upgrade arbiter-smtp==0.9.4      # select one version
```

Skip this step when you want to keep the versions already recorded in
`.reploy/requirements.txt`.

## Configure the deployment
Configure Arbiter and the accounts and policies for the enabled plugins. You
can bootstrap a new config and edit it, or copy in an existing config
directory.

Bootstrap the main Arbiter config through Reploy. These commands run inside the
deployment runtime, using the Arbiter server installed in the deployment
bundle:

```bash
reploy app bootstrap server
```

Then create IMAP and SMTP accounts named `bot` with corresponding policies:

```bash
reploy app bootstrap --plugins imap,smtp --account bot
```

Edit the generated account and policy files, then activate both accounts:

```bash
reploy app config activate --plugins imap,smtp --account bot
```

Then inspect the composed config with:

```bash
reploy app config show
```

Other service plugins follow the same pattern: bootstrap the plugin account,
edit the generated account and policy files, activate the account, and rerun
`env bootstrap` if the new config references additional environment variables.

## Create and maintain the env file

After the config exists, bootstrap or update its env file:

```bash
reploy app env bootstrap
```

Arbiter config files should reference secrets through environment variables,
for example `${oc.env:SMTP_BOT_ACCOUNT_PASSWORD}`. `env bootstrap` composes the
current config, finds those environment references, and creates or updates
`.arbiter.env` at the deployment root with placeholders for any missing values.
Existing values are preserved, so rerunning it is safe.

Run this again after adding plugins, accounts, or policies. New plugin config
can introduce new credential variables, and `env bootstrap` adds the new
placeholders without removing the values you already filled in.

Keep `docker.env` separate from `.arbiter.env`: `docker.env` controls the
Compose wrapper, while `.arbiter.env` is created by Arbiter env tooling and
belongs to the Arbiter server config.

## Start and test

After adding config and env, start the staged service and smoke test the
Arbiter server:

```bash
./arbiterctl up
reploy test
```

`up` starts or updates the staged Docker service. `reploy test` calls the
staged Arbiter health endpoint through the configured staging URL and retries
through transient startup failures.

You can also verify plugin discovery and plugin versions through the normal
Arbiter client. Use the staging URL:

```bash
arbiter arbiter.url=https://127.0.0.1:18075 info --yaml plugins
# Heads up: connected to staged Arbiter at https://127.0.0.1:18075.
# server_url: https://127.0.0.1:18075
# kind: plugins
# plugins:
# - id: imap
# - id: smtp
```

Once the staged service works locally, install it as a host service with the
[Linux install](./2-linux-install.md) runbook.
