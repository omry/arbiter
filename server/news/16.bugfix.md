Limit creation of individual artifacts to 50 MiB by default through the named
core setting `arbiter.limits.max_artifact_bytes`. Reject oversized artifacts
before hashing or disk writes with a safe `policy_denied` response, and publish
the configured ceiling in operation discovery. Plugin-owned limits remain
independent of core limits.
