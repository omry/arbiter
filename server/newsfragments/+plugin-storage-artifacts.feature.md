Added server-managed plugin data directories and one-time artifact delivery
support for plugin-owned binary files. Docker deployments now create and mount
`data/plugins` as the writable plugin data root, and artifact URLs are based on
the configured public server base URL instead of the internal bind address.
