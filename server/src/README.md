# Source Layout

Use this directory for the Arbiter HTTP server implementation.

Suggested subdirectories:

- `config/`
- `tools/`
- `services/`
- `transports/`
- `policies/`

Keep HTTP handlers thin and move shared behavior into services, transports, and policies.
