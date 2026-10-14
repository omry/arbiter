Native HTTP operations run in a bounded worker pool, defaulting to eight workers
configured by `arbiter.limits.max_blocking_workers`. Blocking plugin work and
response preparation keep the event loop responsive; excess operations return
a retryable `resource_exhausted` error before execution. Cancelled requests keep
their worker capacity until the underlying operation finishes.
Operation workers do not prevent the process from exiting during shutdown.
