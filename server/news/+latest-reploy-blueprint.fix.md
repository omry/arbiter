Migrate the packaged Arbiter blueprint to Reploy's current environment schema,
including the public and local network access required by IMAP and SMTP
accounts, and
update the deployment runbooks for Reploy's current staging, bundle, build,
application-command, and system-install interfaces.

Show deployment-local control-script commands in generated bootstrap and
activation hints, including the canonical `config activate` command path.

Run config display, environment checks, version queries, and plugin inspection
commands with read-only deployment storage while retaining write access for
explicit mutation commands, live config checks, and the server workload.

Use Reploy's standard `appctl` deployment control script instead of declaring
an Arbiter-specific controller name.
