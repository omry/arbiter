Bound complete IMAP server responses with the configurable account setting
`limits.upstream_server.max_response_bytes` (64 MiB by default), including
startup, unsolicited data, protocol framing, and data retained between commands.
Close the connection on overflow and report `upstream_protocol_error` with
IMAP `response_too_large`. Enforce aggregate full-message literal limits and
reject duplicate matching message bodies before MIME parsing. Message and
preview limits remain independent of the upstream-server response budget.
