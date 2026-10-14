Add the server-owned `arbiter.limits.max_artifact_count` limit, defaulting to
2,000 retained artifacts across all plugins. Creation at capacity returns a
retryable storage-exhausted error; existing downloads remain available and
consumed artifacts count until cleanup removes them. Operation discovery reports
the configured limit.
