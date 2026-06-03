Print the MCP URL after Docker deployment `up` and `restart` commands, add a
Docker helper `test` command for MCP `version_info` smoke checks, and give
staged Docker directories a staging-specific host port and Docker identifiers.
Staged `up` now rewrites `ARBITER_DOCKER_SUBNET` to an unused candidate when
the configured subnet overlaps an existing Docker network. The helper `test`
command now waits quietly through transient connection failures while the server
starts and prints a single status line.
