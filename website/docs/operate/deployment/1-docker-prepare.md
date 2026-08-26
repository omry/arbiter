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
curl -fsSL https://reploy.cli.dev/install.sh | sh
```

If you prefer to install through Python packaging, Reploy is also available on
PyPI:

```bash
python3 -m pip install reploy
```

The installed `reploy` command creates the staging directory and writes the
standard deployment-local `appctl` control script. The commands below use
`reploy` directly so the staging and installed workflows stay explicit.

## Create the directory

```bash
reploy stage arbiter-server
```

By default this creates `./reploy-staging`, including a deployment-local
`appctl` script for the staged Arbiter Docker service. `stage` records desired
state and generates control files; it does not build the application image.

For an existing staging directory, refresh the generated deployment files with:

```bash
reploy stage --update --dir reploy-staging
```

Use `--force` only to recover incompatible or foreign staging state, or when an
active operation must be stopped for recovery.

<details>
<summary>Deployment layout</summary>

Most operators should use `reploy` instead of editing generated deployment
files directly.

- `conf/`: operator config directory, populated by bootstrap or copied from an
  existing configuration.
- `appctl`: local lifecycle and app-command wrapper.
- `.reploy/state.json`: versioned deployment state.
- `.reploy/staged-control.json`: generated control metadata.
- `.reploy/`: Reploy-managed build locks, provider artifacts, runtime files,
  operation locks, and its deployment-local runtime binary. Files such as the
  Compose configuration are materialized when the environment is built.
- `data/server/`: server-owned runtime state created as needed, such as the
  generated self-signed TLS certificate and private key.
- `data/plugins/`: runtime state created by plugins, such as idempotency records
  and temporary artifacts.

</details>

Prepared Docker directories are staged deployments. They use staging-specific
Docker names and ports so they can run next to an installed Arbiter. Install
transfers the selected build and managed paths into a separate state-v1
installed deployment.

## Select the bundle

The bundle request overlay selects optional Arbiter components and direct
package additions. Building resolves that request, materializes a validated
application image, and records the closed provider artifact set used to
reproduce it.

### Select plugins

Choose the Arbiter service plugins for this deployment:

```bash
reploy bundle options                    # show component-qualified options
reploy bundle add application/imap,smtp  # IMAP receive + SMTP send support
reploy bundle remove application/smtp    # remove SMTP if it is not needed
reploy bundle list                       # show the current request overlay
```

Use `bundle add application/arbiter-suite` or `bundle remove
application/arbiter-suite` to add or remove all plugins in the suite meta
package. `arbiter-server` is the Reploy blueprint; `arbiter-suite` is an
Arbiter package bundle option.

### Build the environment

Build after changing the blueprint, bundle overlay, or development overrides:

```bash
reploy build
```

`reploy up` and `reploy restart` also build when needed. App, shell, test, and
inspection commands require a current build and tell you to run `reploy build`
when it is missing or stale. Use `reploy verify` for a read-only audit of the
current build.

### Change package selections

The current Reploy interface has no `bundle upgrade` command. Change an option,
blueprint requirement, or direct component-qualified package request, then
build again. For example, a third-party Python plugin can be added directly to
the application component:

```bash
reploy bundle add-package application/application/python my-arbiter-plugin==1.0.0
reploy build
```

Use `reploy bundle remove-package` with the same component and requirement to
remove a direct addition.

## Configure the deployment
Configure Arbiter and the accounts and policies for the enabled plugins. You
can bootstrap a new config and edit it, or copy in an existing config
directory.

Reploy gives bootstrap, activation, deactivation, `env bootstrap`, and
`config check` commands write access to the deployment directory. Display,
`env check`, version, and plugin-list commands mount the same directory
read-only, so they can run concurrently without mutating operator
configuration. Live config checks retain write access because plugins may
record operational test state.

Bootstrap the main Arbiter config through Reploy. These commands run inside the
deployment runtime, using the Arbiter server installed in the deployment
bundle:

```bash
reploy app bootstrap server
```

Then create IMAP and SMTP accounts named `bot` with corresponding policies:

```bash
reploy app bootstrap --plugins=imap,smtp --account=bot
```

Edit the generated account and policy files, then activate both accounts:

```bash
reploy app config activate --plugins=imap,smtp --account=bot
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

Keep `.reploy/docker.env` separate from `.arbiter.env`: the former is generated
runtime metadata, while `.arbiter.env` is created by Arbiter env tooling and
belongs to the Arbiter server config.

## Start and test

After adding config and env, start the staged service and smoke test the
Arbiter server:

```bash
reploy up
reploy test
```

`up` builds when needed, then starts or updates the staged Docker service.
`reploy test` calls the staged Arbiter health endpoint through the configured
staging URL.

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
