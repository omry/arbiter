Migrate the packaged Arbiter blueprint to Reploy's current environment schema,
including the public and local network access required by IMAP and SMTP
accounts, and
update the deployment runbooks for Reploy's current staging, bundle, build,
application-command, and system-install interfaces.

Show deployment-local control-script commands in generated bootstrap and
activation hints, including the canonical `config activate` command path.

Keep the deployment root read-only for the server workload, mount runtime data
separately with write access, and grant deployment-root writes only to explicit
mutation commands. Render generated Reploy control-script hints with the
assignment-form flags accepted by Reploy.

Use Reploy's standard `appctl` deployment control script instead of declaring
an Arbiter-specific controller name.
