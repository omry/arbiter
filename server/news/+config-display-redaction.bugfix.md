Mask `oc.env` and `secret_file` values as `<REDACTED>` when resolving config-show
output, including scalar selections and typed environment-derived ports.
Runtime resolution remains unchanged. Literal secrets in YAML remain printable.
