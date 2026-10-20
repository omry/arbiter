---
title: Development Credential Profiles
---

Offline `arbiter-server authority init` creates separate owner-issuer,
server-authority and inner server-TLS credentials. They are committed with the
owner/server IDs in the private SQLite database. Status returns public identity
metadata, never private keys. Setup is still development work: pairing and runtime
caller authentication are separate milestones. Keep trusted-local-only operation.

## Certificate profiles, version 1

All certificates use X.509 v3, P-256 keys and ECDSA/SHA-256 signatures. IDs are
canonical lowercase version-four UUIDs. The Common Name does not grant authority.
The verifier requires an exact role and identity, rather than discovering a role
from whatever a peer supplied. The DER limit is 4,096 bytes before parsing;
noncanonical/trailing DER, duplicate identity bindings and unknown critical
extensions are rejected.

| Role | Required URI SANs | Issuer and purpose |
| --- | --- | --- |
| Owner issuer | `urn:arbiter:owner:OWNER` | Self-signed CA; certificate signing only |
| Server authority | `urn:arbiter:server-authority:SERVER` | Self-signed CA; certificate signing only |
| Portable agent | `urn:arbiter:portable:AGENT` | Registered owner issuer; `clientAuth`; enrollment identity only |
| Per-server access | `urn:arbiter:access:AGENT`, `urn:arbiter:server:SERVER`, `urn:arbiter:enrollment:ENROLLMENT` | That server's authority; `clientAuth` |
| Inner server TLS | `urn:arbiter:server:SERVER` | That server's authority; `serverAuth` |

Basic Constraints, Key Usage and SAN are required critical extensions. Leaves
also have exactly their required critical EKU. CAs have path length zero and no
leaf EKU. Issuer and leaf keys must differ. Leaf validity must fit within the
issuer's validity. Signature, issuer name, issuer role and expected IDs are all
verified. A generic valid client certificate is insufficient.

Self-signed parsing does not register trust. Leaf verification requires issuer
bytes selected by the caller from its registered trust store, with the expected
issuer ID. Portable and ordinary access identities use different issuer purposes
and SAN roles. Future enrollment additionally checks live approval and grants;
these certificate functions do not grant access by themselves.

## Validity and renewal schedule

New issuers expire 3,650 days after issuance; renewal becomes due 366 days before
expiry. Portable, access and server-TLS leaves expire 365 days after issuance;
renewal becomes due 30 days before expiry. Issuance backdates `notBefore` by five
minutes. Verification adds no expiry grace and uses the inclusive X.509 validity
bounds documented by [cryptography](https://cryptography.io/en/42.0.8/x509/reference/#cryptography.x509.Certificate.not_valid_after_utc).
An issuer with insufficient validity for a full-lifetime leaf must be renewed
before issuing it. The separate 30-day access-key rotation, 24-hour grace and
180-day inactivity rules remain later authorization checks, not these lifetimes.

This slice defines due-time calculations; it does not implement renewal or
rotation. Expired peer-supplied trust anchors cannot authorize renewal. Offline
status validates the initial setup at its recorded creation time so an expired
certificate does not prevent inspection. Network verifiers must use current time.

## Storage and compatibility

The database contains the three server-held private P-256 keys as bounded,
unencrypted PKCS8 DER inside the existing private filesystem boundary. Access
and portable client private keys are not created or retained by server setup.
Certificate status output does not export private material. The setup transaction
must succeed completely before publication; failure does not replace existing
IDs or keys. Never copy the authority database into an agent workspace.

Authority schema 2 requires the complete credential set. The earlier experimental
IDs-only schema 1 is rejected without modification; there is no implicit upgrade
or reinitialization. Preserve old state rather than deleting identity records to
work around a version error.

Shared public-only verification vectors are in
`tests/fixtures/authentication/credential-profiles-v1.json`. They are synthetic
test trust, consumed by Python now and intended for Go's slice 2.1 verifier.
They must never become product trust or default credentials.
