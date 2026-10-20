Offline authority initialization now creates separate owner-issuer, server-authority
and server-TLS credentials atomically with the private identity database. Strict
role, issuer, identity, size and validity checks reject mixed-purpose credentials;
private keys are never included in status output. Runtime caller authentication
remains a separate milestone. The development authority schema is now version 2;
older experimental state is rejected without modification.
