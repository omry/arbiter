Admit operation requests before reading their bodies, and parse JSON on the
admitted worker. The existing eight-worker default, request-body ceiling, and
retryable capacity error remain; cancellation and disconnects recover capacity
after underlying work and cleanup finish.
