Add Go verification of Arbiter's role-separated credential profiles, with shared
Python/Go rejection vectors and a fresh Python issuer interoperability test.
This foundation does not activate enrollment or authenticated API access.
Optional version-one extension validation now matches Python for SKI, AKI,
unsupported recognized extensions, and opaque unknown noncritical values.
Both verifiers reject unused issuer and subject unique-identifier fields.
