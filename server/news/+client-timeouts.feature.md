The native Arbiter client supports configurable API response budgets, independent
connection setup limits, and artifact download inactivity limits. Configure
plugin defaults and operation overrides with scalar or mapped plugin timeouts;
command-line timeout overrides take precedence. Progressing artifact downloads
no longer have a fixed total deadline. URL overrides retain the configured CA
and timeout settings, and the client validates its config even when a URL is
provided explicitly.
