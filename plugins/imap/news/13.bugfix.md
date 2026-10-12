Reject control characters in IMAP search text and folder names before issuing
commands, and validate message UIDs and flag atoms at the protocol boundary.
Preserve safe quoting and literal message bodies while preventing caller input
from injecting additional IMAP commands or bypassing operation policies.
