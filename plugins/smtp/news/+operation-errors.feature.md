Classify SMTP authentication, transport, policy, rate-limit, and idempotency conflicts with structured plugin errors. Report confirmed submission and failed post-send steps explicitly, including key-dependent replay guidance, while preserving Sent-copy warn/fail policy. Release idempotency reservations after connection or session-setup failures before submission, and retain reservations for ambiguous duplicate-recipient outcomes. Validate unsupported non-ASCII envelope addresses before reserving retry keys.

RCPT failures raised before SMTP DATA, including service-closing 421 replies, release retry reservations without claiming partial delivery.

Temporary SMTP authentication failures report retryable transience from their 4xx response code.

Recipient-refusal transience is yes for all temporary 4xx codes, no for all permanent 5xx codes, and unknown for mixed codes; replay safety remains separate.

Pre-DATA recipient failures include the full not-submitted recipient count alongside the explicitly refused addresses.
