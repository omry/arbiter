Replaced the old client-facing transport with Arbiter's native HTTP API. The
client URL setting is now `arbiter.url`, generated configs default to
`http://127.0.0.1:8075`, Docker staging defaults to `http://127.0.0.1:18075`,
and the generated helper commands and documentation no longer expose protocol
endpoint URLs or legacy transport commands.
