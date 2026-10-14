Bound full IMAP message retrieval to 50 MiB by default with the account-owned
`limits.max_message_bytes` setting. Check size before fetching, use bounded
non-mutating reads, and reject oversized response literals before allocation.
Oversized reads return `policy_denied` with IMAP domain code `message_too_large`
without changing the mailbox or creating an attachment artifact.
