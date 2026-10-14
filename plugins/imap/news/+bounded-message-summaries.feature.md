List and search now fetch headers, flags and MIME metadata instead of full
messages, keeping oversized messages visible without downloading attachments.
Snippets fetch at most 16 KiB of transfer-encoded text per message by default,
configurable with each account's `limits.max_preview_bytes`. Summary attachment
sizes describe the server-reported encoded part size; explicit retrieval still
provides full content and decoded attachment sizes.
