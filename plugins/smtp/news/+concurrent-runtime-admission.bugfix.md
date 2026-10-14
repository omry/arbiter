SMTP rate-limit admission and retry-store initialization are synchronized so
concurrent operation calls cannot consume the same remaining rate slot or
initialize duplicate retry stores.
Concurrent retries of the same completed SMTP submission also serialize recovery
of a failed sent copy, avoiding duplicate copies within one runtime.
