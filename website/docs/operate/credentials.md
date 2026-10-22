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

The native Go verifier applies the same version-one profiles. Shared positive
and negative vectors and a test using freshly issued Python credentials check
cross-language agreement. A caller supplies the expected role, IDs, registered
issuer and verification time; the verifier does not discover trust from a peer's
certificate. This development foundation does not enable client enrollment or
ordinary authenticated API traffic.

Version-one profiles support noncritical Subject Key Identifier values with one
nonempty DER OCTET STRING and Authority Key Identifier values with only one
nonempty key-identifier field. Other recognized optional extensions are
rejected. Unknown noncritical extensions remain opaque but must contain one
complete DER element with no trailing bytes or malformed constructed-child
framing. Primitive contents remain opaque; Arbiter does not interpret the
semantics of unknown extensions.
Both signature AlgorithmIdentifiers must use ECDSA with SHA-256 and absent
parameters. Negative serial numbers and unused issuer/subject unique-identifier
fields are rejected. These restrictions
apply to issuer and leaf verification; normal setup and issuance already produce
this profile.

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

Authority schema 3 requires the complete credential set and registered recovery
trust. Earlier experimental schemas 1 and 2 are rejected without modification;
there is no implicit upgrade
or reinitialization. Preserve old state rather than deleting identity records to
work around a version error.

Initialized authority now lives in a private `generations/UUID/authority.sqlite3`
directory, selected by `active-generation.json`. IDs, credentials and registered
trust are published together. A bounded publication intent lets stopped-instance
status or the next start finish an interrupted publication of those exact bytes.
It does not invent a replacement identity or repair damaged database contents.
Only reserved incomplete staging is removed; unexpected files cause an error.

Missing or damaged selectors and generations require operator investigation.
Preserve every saved generation and the recovery file. Earlier experimental flat
database layouts are rejected without conversion or deletion. A failed setup can
have published durable intent; inspect status before retrying initialization.
POSIX publication flushes files and parent directories. Windows uses same-volume
[write-through moves](https://learn.microsoft.com/en-us/windows/win32/fileio/moving-directories)
for files and generation directories; filesystem errors are reported as failures.

On Linux, macOS and Windows, `authority init` and `authority status` also work while the
server is running, when invoked by the server's OS user. Linux and macOS use a private
Unix socket with kernel peer credentials. Windows uses local-only named pipes with
restricted ACLs and checks both caller and server OS identities. Only the running server
writes its live authority state. Other users must use trusted OS tooling to run
the command as the service account; an HTTP connection cannot administer it.
Repeated initialization still refuses to replace IDs, credentials or recovery
trust. Keep the recovery file outside the server's state and agent storage.

Live control on other POSIX systems is unsupported in this slice. An unavailable
native peer-credential backend fails closed; stop the server for offline setup
or status on those systems.

For remote administration, first open a verified SSH session as the server's OS
account, then run the installed `arbiter-server authority` command on that host.
On Windows, select the same authority directory used by the service:

```console
ssh service-user@windows-host
arbiter-server authority status --state-dir "C:\Arbiter\server\authority"
```

Use the actual service account and configured path. An SSH tunnel to an HTTP
port does not grant local authority. Windows live control does not expose a
TCP listener or require forwarding a named pipe. A missing or exhausted local
endpoint reports an error; it does not bypass the instance lock.

After an interrupted control exchange, query status before retrying setup: a
missing reply does not prove the server stopped writing. A later startup removes
only a validated stale private socket or pipe locator while holding the instance
lock. Unexpected endpoint files or permissions require investigation and are preserved. If no live
endpoint exists during startup, shutdown or another offline command, the CLI
retains the authority-busy diagnostic. Unsafe endpoints still report their safety
error. On Linux and macOS, select a short local server-data path within the OS
Unix-socket path limit.

Shared public-only verification vectors are in
`tests/fixtures/authentication/credential-profiles-v1.json`. They are synthetic
test trust, consumed by Python now and intended for Go's slice 2.1 verifier.
They must never become product trust or default credentials.

## Provisional client material (development)

The Go enrollment foundation generates separate P-256 access and portable keys,
an X25519 artifact-recipient key, and an independent public 128-bit request
selector. Its pairing secret is 32 fresh random bytes, encoded as 43 unpadded
Base64url characters for the explicit operator handoff. The public selector is
22 unpadded Base64url characters and is not a password. Version framing keeps
these values separate.

The version-one private JSON format starts with a `NEVER SHARE THIS` warning.
It contains the version, selector, HTTPS server URL, display name, UTC creation
time and three separate canonical PKCS8 DER keys, encoded with padded Base64.
It has no pairing secret, approved agent ID, certificate, grant or server trust.
The decoder rejects duplicate or unknown fields, noncanonical keys, wrong key
roles, reused signing keys, and records over 8 KiB. Names are bounded to 256
UTF-8 bytes without control characters; URLs are bounded to 2 KiB and cannot
contain user information, query credentials or fragments.

Ordinary formatting redacts the material and generic JSON serialization refuses
to export it. Explicit private encoding is intended only for the later private
state layer. These protections help prevent accidental logging; they do not
prevent an agent in lightweight mode from reading its own private keys.
The public advertisement contains only its version, selector, name and the
three SPKI public keys. A live Python/Go test checks signing and recipient-key
interoperability after the private format round-trip.

The library can save, load and explicitly remove a provisional request in a
caller-selected private directory. It creates only the final directory; its
parent must already exist with safe ownership and replacement permissions.
POSIX private directories and files must belong to the caller with no group or
other permissions. Windows permits only the caller, SYSTEM and Administrators
to own private state or read/write its files. Existing unsafe permissions are
rejected, never repaired. File links and Windows reparse points are rejected.
Parent aliases are resolved once to accommodate platform paths such as macOS
`/tmp`; the final directory itself cannot be an alias.

Each public selector names one immutable private record. A save writes exclusive
private staging, flushes and closes the file, then publishes without replacing
an existing record. POSIX publication flushes the directory and its owning parent,
including retries with an existing directory; flush errors cannot report a
successful save. Windows uses native
write-through publication without a cross-volume copy fallback. Cancellation is
checked before mutation and publication; it cannot interrupt a kernel disk call.
An error after publication can leave a complete recoverable record, so an error
never means that no file exists. A process crash before publication can leave
private staging. Explicit removal discards that request's final and staging
names, including the exact two-link POSIX interruption state, and does not claim
secure deletion.

The executable Python/Go peer reloads keys from this native storage before
producing its public cryptographic proofs. Native permission, cancellation and
process-crash tests exercise supported platforms. The library does not contact
a server, expose an enrollment command or activate an identity. Automatic
startup, expiry and restart cleanup and CLI integration remain follow-on work.
Losing the in-memory pairing secret does not enroll a provisional record.

## Operator recovery custody

Setup requires `--recovery-file PATH` in an existing operator-owned private
directory outside server state. POSIX directories must be private (normally
`0700`); files use `0600`. Windows uses the same operator/SYSTEM/Administrators
ownership and inheritable ACL boundary as authority state. Permissions and
aliases are validated, never repaired. The parent retains a private
`.authority.lock` to serialize publication and explicit reuse. Do not delete a
lock while setup is running.

The version-one JSON file starts with `NEVER SHARE THIS`. It holds a separate
P-256 recovery signer and X25519 backup decryptor as base64 canonical PKCS8 DER,
the owner ID, and trusted per-server backup-origin SPKI public keys. Keys are
unencrypted within this private file. Its combined powers are intentional:
anyone who steals it can decrypt backups and authorize live identity recovery.
Keep a safe copy outside the server, agents, export jobs and synced bundles.
Loss of every copy makes future encrypted backups unrecoverable.

Server state contains only the recovery verifier and backup recipient SPKI,
plus a separate server-held backup-origin signing key. Private external recovery
keys never enter that state or status output. The X25519 public/private records
use standard DER; the future age export adapter will encode them as age
identities/recipients. This setup does not implement export, restore or live
credential recovery yet.

Initialization refuses an existing recovery file unless you select
`--reuse-recovery-file`. Explicit reuse preserves both private keys and the owner
ID and adds this server's public backup-origin verifier without removing earlier
verifiers. It does not share server identities or grant access between servers.
Reuse never regenerates existing private keys; there is no force-reset option.

Custody publishes before the complete authority database. If setup fails before
custody publication, retry normally. If it publishes the recovery file but not
the database, retain the file and retry with explicit reuse. An unused public
origin entry can remain from that failed attempt; it grants no live access.
A killed initializer can leave one bounded private staging file, named
`.FILENAME.arbiter-pending`; the next setup reconciles it under the parent lock.
If the database already exists, inspect `authority status`; never reinitialize
or delete durable identity records to bypass an error. Status needs only the
registered public trust, so the recovery file can be moved to safe external
custody after setup. Filesystem flush failures are reported as failures even if
publication already occurred; inspect both destinations before retrying.
