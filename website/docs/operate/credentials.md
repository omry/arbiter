---
title: Development Credential Profiles
---

Offline `arbiter-server authority init` creates separate owner-issuer,
server-authority and inner server-TLS credentials. They are committed with the
owner/server IDs in the private SQLite database. Status returns public identity
metadata, never private keys. Pairing can activate an enrollment; ordinary runtime
caller authentication remains a later milestone. Keep trusted-local-only operation.

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
certificate. Certificate verification alone does not enroll a client or authorize
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
The POSIX backend requires read and search access to every ancestor directory,
and write access to the direct parent when creating the private directory.
Search-only ancestors are currently unsupported, even when their ownership and
modes are safe. Permission failures preserve existing state.

Each public selector names one immutable private record. A save writes exclusive
private staging, flushes and closes the file, then publishes without replacing
an existing record. POSIX publication flushes the directory and its owning parent,
including retries with an existing directory; flush errors cannot report a
successful save. Windows uses native
write-through publication without a cross-volume copy fallback. Cancellation is
checked before mutation and publication; it cannot interrupt a kernel disk call.
An error after publication can leave a complete recoverable record, so an error
never means that no file exists. Retrying an existing selector retries the
directory flush, preserves the file, and still returns the existing-record error;
any flush failure is also returned. A process crash before publication can leave
private staging. Explicit removal discards that request's final and staging
names, including the exact two-link POSIX interruption state, and does not claim
secure deletion.

The client lifecycle library holds a native, non-waiting process lock for the
entire exchange, including underlying work that has not yet stopped after
cancellation. A second process cannot clean or start an exchange in that same
directory while it is owned. The empty private `.enrollment.lock` file remains
afterward; do not delete it while an exchange is running. Process exit releases
the native lock even after a crash.

Startup/restart cleanup removes abandoned provisional records and torn staging
before generating fresh keys. Each call reads at most 65 names and processes at
most 64. If more entries remain after removing owned files, it reports incomplete
cleanup; explicitly retry before starting enrollment. If a full batch removes
nothing, it instead reports unexpected contents: move unrelated files out of the
provisional directory before retrying. Repeating cleanup alone cannot resolve
that condition. Unknown filenames are preserved. An unrecognized
complete record or unsafe permissions stop cleanup without deleting that record.
Use a dedicated provisional directory, separate from prepared or enrolled state.

The consumer receives keys and the in-memory secret only after a successful
durable save. Its deadline is ten minutes from original client creation. On
success, failure, expiry or cancellation, the request is removed after the
consumer actually returns. Removal failures are reported, retaining recoverable
files for the next cleanup; no secure deletion is promised. Any durable prepared
result or activated identity must be saved separately before the consumer returns.

The executable Python/Go peer uses this lifecycle, reloads the saved keys before
producing public cryptographic proofs, and checks removal afterward. Native
permission, ownership, cancellation and process-crash tests exercise supported
platforms. The storage lifecycle itself does not contact a server or activate an
identity. The enrollment command below consumes it through the pairing carrier.
Losing the in-memory pairing secret does not enroll a provisional record.

### Interactive pairing command

Run `arbiter enrol url=https://HOST name=NAME` from an interactive terminal.
The initialized server operator first runs `arbiter-server enrollment open`
through local administration. The client durably saves fresh provisional keys,
then registers only the public advertisement. A closed server returns local
opening guidance; registration never opens enrollment itself.

The client displays its request selector and 43-character secret on the
controlling terminal, separately from stdout/stderr. At the server's local
approval prompt, enter that secret for the matching request and select account
grants. Return to the client and press Enter. There is no secret argument,
environment variable, file or redirected-output fallback. Do not capture the
terminal or share the secret with an untrusted server operator.

The client then has one connect-timeout budget (ten seconds by default) for
outer TLS, WebSocket upgrade, Noise, both signing-key proofs, preparation,
private persistence and the ready exchange. Operator waiting
does not consume it; the original ten-minute provisional lifetime still applies.
The dedicated bootstrap connection accepts an unverified outer certificate;
the PSK-authenticated Noise channel authenticates the signing identities.
It inherits no proxy, cookies, authorization, client certificate or redirects.
WebSocket compression/subprotocols, text, oversized or excessive control frames
are rejected. Unauthenticated failure cannot establish that a client was revoked.
For an expired, restarted, rejected or unavailable request, ask the operator and
start again. Private provisional files are removed on exit, with the existing
bounded startup cleanup handling interruptions.

The command saves verified prepared credentials, keys and public trust before
sending ready. The server commits the approved identity, credentials, grants and
exact result together. The client verifies the committed result through remembered
server trust, flushes complete private state into `enrolled/`, then removes its
prepared record. It reports `Enrollment activated and saved`. Ordinary authenticated
API access remains unavailable in this milestone.

If the final reply or private publication fails, keep the saved state and run
`arbiter enrol resume=SELECTOR`. This needs no terminal secret or new approval.
It authenticates the server and retrieves only that exact committed result. A
retry after successful promotion is also safe. Denial, inactivity or an absent
commit prevents confirmation. If the server never committed, ask the operator and
explicitly start a new pairing. Do not restart pairing merely to recover a lost
receipt: new pairing discards abandoned prepared records.

### Public enrollment request boundary

`POST /api/v1/enrollment/requests` accepts version-one provisional public
advertisements as strict UTF-8 JSON without a byte-order marker. UTF-16 and
UTF-32 bodies are rejected. Registration does not grant access, approve an agent,
or install server trust. The endpoint stays closed until local administration opens a
ten-minute window. Its closed response explains that an operator must open it;
there is no remote opening endpoint. Use the local commands below on the server
host, or through a trusted SSH terminal to that host.

Core Arbiter limits pending requests with
`arbiter.limits.max_pending_enrollments`, a positive integer that defaults to
20. Requests are memory-only and disappear on server restart/shutdown. Each
request has an immutable ten-minute deadline. Reopening the window and retrying
the same exact advertisement cannot extend it. Expired records are discarded
on the next bounded registry operation. A later registration is a new revision,
not a continuation of an expired approval context.

The public payload contains only version, selector, untrusted display name and
separate access, portable and recipient public keys. Private keys and pairing
secrets are rejected fields. The returned request digest is SHA-256 of the exact
accepted request-body bytes; clients retain those bytes for later pairing proof.
Changing a selector's payload conflicts with the existing request. A repeated
identical payload consumes no additional slot.

### Local enrollment approval

Initialize server authority first, then run these commands as the same OS user
that runs the server. `--state-dir` selects its private authority directory,
not the client state directory. The server must be running:

```bash
arbiter-server enrollment open --state-dir /path/to/server/authority
arbiter-server enrollment list --state-dir /path/to/server/authority
arbiter-server enrollment approve --selector=SELECTOR --state-dir /path/to/server/authority --grant imap/personal
arbiter-server enrollment reject --selector=SELECTOR --state-dir /path/to/server/authority
```

Opening admits requests for ten minutes and returns a bounded acknowledgement.
List shows public selectors, untrusted names, key fingerprints and remaining
request lifetime. The local control list is paged, while the command combines
all pages for complete output. Approval and rejection look up the exact
selector before sending its revision and request digest. Use the
`--selector=VALUE` form so selectors that begin with `-` remain values.
Approval displays the selected public summary and prompts for the client's
43-character pairing secret with terminal echo disabled. It refuses redirected input or an echoing
fallback. There is no secret command-line option or environment variable. Do
not paste the secret into a shell command. Use `--json` for machine-readable
public results.

Repeat `--grant PLUGIN/ACCOUNT` to select configured accounts. Omitting it grants
no accounts. Approval freezes those names, the request revision, digest and keys;
it does not create another policy or activate ordinary API access. An identical
approval is idempotent. To change an approval, reject it and start again. Approval
and rejection bind the revision selected before the prompt, so a replaced or
expired request cannot receive a stale approval.

Approved requests still count toward the configured pending-request capacity and
keep their original ten-minute deadline. Reopening does not extend it. Secrets
remain only in bounded server memory and are omitted from listings, logs, files
and backups. Expiry,
rejection and server shutdown/restart remove approval state. This does not promise
complete zeroization of every Python string or temporary copy. The interactive
client can now exchange Noise and signing-key proofs; approval alone
installs no identity, certificate or account access.

### Pairing component limits

The Go client contains the same bounded Noise initiator profile as the Python
responder. It binds the exact public advertisement bytes, selector and three
proposed public keys, authenticates the server role, and limits each frame and
the total bytes in each direction. A protocol or authentication failure closes
that exchange. The Python/Go integration tests execute both libraries with fresh
keys; they also reject a substituted request or secret. The interactive CLI uses
the bounded public carrier. After both peers prove their signing keys, the server
issues a prepared result for the exact approved request. The client checks the
server, owner and leaf certificate profiles, reserved IDs, proposed keys, recovery
public trust, expiry and grant summary. It flushes its complete keys and result to
a private `prepared/` record before signing a ready proof over that result and
the current Noise transcript. The record carries `NEVER SHARE THIS` and contains
no pairing secret.

Prepared certificates and a ready proof alone cannot authorize ordinary work.
Only the final conditional transaction activates the exact result, after rechecking
live approval, original expiry, attempt ownership and configured account names.
Committed results survive restart without a PSK; rejected or expired uncommitted
requests do not. One prepared result per request shares the existing bounded
pending capacity and original deadline. Rejection, expiry and server restart
invalidate it. Explicitly starting another enrollment discards abandoned private
prepared records in bounded batches while holding the client enrollment owner.
It preserves enrolled state. No secure deletion is promised. Do not start a new
pairing to retry an operation or infer activation from the presence of a file.

The Noise pairing component uses a 256-bit pairing secret and the approved
`Noise_NNpsk0_25519_ChaChaPoly_SHA256` profile. It binds the client/server roles,
public selector, exact advertised request and proposed keys. Anonymous failures
do not consume the five distinct admitted exchanges allowed per approval.
Authenticated replays share an existing exchange; they cannot create a second
mutable handshake. Five failed or abandoned admitted exchanges discard pending
approval and require a new request and owner approval.

The component has these operator-owned settings:

```yaml
arbiter:
  limits:
    native_transport:
      max_connections: 16
      max_handshakes: 4
      handshake_timeout_seconds: 10
      max_handshake_bytes: 262144
      max_noise_frame_bytes: 65535
      max_headers_bytes: 32768
      max_websocket_message_bytes: 65536
      max_buffered_bytes: 262144
      max_control_frames_per_second: 32
      max_control_bytes_per_second: 4096
```

Counts and byte limits must be positive integers. The handshake timeout is a
positive, finite number of seconds. The Noise frame ceiling cannot exceed
65,535 bytes, including the authentication tag. The total handshake-byte budget
applies separately to each direction and includes the initial Noise messages.
Admission has no waiting queue; cancellation retains capacity until underlying
crypto work actually finishes. These limits belong to core Arbiter.

The binary WebSocket endpoint is
`/api/v1/enrollment/requests/SELECTOR/pair`. It uses `wsproto` for standard
framing and `noiseprotocol==0.3.1` for the Noise handshake. Extensions and text
messages are rejected. Fragmented messages share one cumulative limit. The
smaller Noise/message limit applies, with additional headroom reserved within
the buffer budget. Header, framing and control bytes count toward each
direction's total exchange budget. Pings do not extend the absolute deadline.
Overload rejects before upgrade without a queue; per-request failures close
generically. Socket and crypto ownership survive cancellation until cleanup.
The initial connection has a bounded target-decision deadline. Once the pairing
target is known, its native slot covers the remaining raw headers and the
upgrade, which preserves the original deadline. Each native carrier uses a
fresh HTTP connection; an upgrade that bypasses this guard is rejected.
Ordinary HTTP body, operation and download budgets remain separate.

This endpoint completes the encrypted handshake, mutual signing-key proofs,
prepared-result delivery and ready verification before closing. Fresh Python/Go
peers exercise the public CLI/carrier and private prepared persistence.
Activation remains a subsequent delivery step. Local approval, prepared
certificates and ready verification still grant no ordinary API access.

Registration uses the existing bounded worker admission before reading body
bytes. The body limit is the smaller of the core request-body limit and 64 KiB,
with a ten-second total body-read budget. Full capacity returns a retryable
`resource_exhausted` error. None of these public requests activate an identity.

### Pairing possession proof profile

After Noise authenticates the approved PSK and exact advertisement, the server
proves possession of its distinct server TLS leaf key. It sends the public
server authority, leaf and server ID inside Noise. The Go client validates the
existing strict certificate profiles, signature chain, role, ID and validity.
It then proves both exact proposed access and portable signing keys. These
steps neither install public trust durably nor activate an enrollment.

Version-one server proof plaintext is the ASCII tag
`arbiter-pairing-server-v1` plus NUL, the canonical 36-byte server UUID, the
authority DER and leaf DER each prefixed by an unsigned big-endian 16-bit byte
length, then a 64-byte ES256 signature. Each certificate is at most 4,096 bytes;
no trailing bytes are accepted. Client proof plaintext is
`arbiter-pairing-client-v1` plus NUL, then separate 64-byte access and portable
signatures. Signatures use standard P-256 ECDSA/SHA-256 with raw `r || s` encoding.

The common context is SHA-256 of `arbiter-enrollment-possession-v1` plus NUL,
the 32-byte Noise handshake hash, SHA-256 of the exact existing prologue, and
SHA-256 of the complete server proof header before its signature. Each signer
signs SHA-256 of that context, NUL and its ASCII role: `server`, `access` or
`portable`. This binds both directions to one request, exchange, server and
certificate set. Invalid or repeated proofs close the shared exchange. Existing
frame/total-byte limits, worker admission and the absolute handshake deadline
also cover proofs. No proof can extend approval or authorize ordinary traffic.

## Native lifecycle transport

Lifecycle controls use `/api/v1/native/control` as a binary WebSocket byte
carrier. Configure reverse proxies to forward WebSocket upgrades. TLS 1.3 runs
inside this carrier with the purpose-specific `arbiter-control-v1` ALPN. The
client checks the remembered Arbiter authority and server ID before sending
client identifiers or proofs. Changing the outer proxy certificate does not
change that remembered identity. The outer upgrade sends no client certificate,
application credentials or cookies, and does not follow redirects or use proxy
settings from the environment. This exception is confined to the native carrier;
it does not alter ordinary HTTP or browser certificate checks.

Each connection permits one length-prefixed control exchange, at most 64 KiB
in each plaintext message. Smaller configured buffer budgets also reduce this
plaintext limit to the carrier frame limit, reserving space for TLS and framing.
The connect budget covers outer dial, upgrade and
inner handshake. A separate absolute ten-second control budget starts once the
inner handshake authenticates the server. Progress and pings reset neither budget. Carrier byte,
buffer and shared crypto limits also apply. The Python server and Go client
expose the RFC 9266 TLS exporter for channel-bound receipt proofs. The Go client
verifies the remembered server authority and server ID before exposing the
stream. This transport foundation supports authenticated retrieval of the exact
result for an already committed enrollment. The `enrol` command uses that
authenticated receipt to publish complete enrolled custody. Only the pairing
ready/activation transaction can activate enrollment; this transport cannot
dispatch ordinary operations. Prepared state alone remains uncommitted.

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

### Shared native transport capacity

Native carriers share the configured handshake capacity for the full handshake lifetime. Cancelling a connection releases its slot after the underlying cryptographic work finishes. Byte limits and backpressure also apply while peers are slow.

### Remembered-server lifecycle control transport

The Go client now has a fixed lifecycle-control transport over binary WebSocket
at `/api/v1/native/control`. Inner TLS requires TLS 1.3 and the exact
`arbiter-control-v1` ALPN. The client verifies the expected durable server ID
and registered server authority before exposing the stream. Its TLS exporter
binds later control proofs to that connection. The outer HTTPS certificate can
change without changing remembered Arbiter trust.

This dedicated outer carrier sends no inherited cookies, authorization headers
or client certificates, and uses neither inherited proxies nor redirects. Its
certificate and upgrade input is bounded before untrusted parsing. Reverse
proxies must forward WebSocket traffic without terminating the inner TLS.
This exception is not a general HTTP or browser trust setting.

Connection setup, including the outer dial, upgrade and inner handshake, has
one budget of at most ten seconds. A separate ten-second control deadline starts
after the inner handshake; a shorter caller deadline still applies. One stream
permits one nonempty request and reply, each at most 64 KiB decoded. Cancellation
closes the carrier, and exchanges are never automatically replayed.

Receipt reconciliation is available only for already committed results. Pairing
can now commit such a result. No arbitrary control or ordinary HTTP operation is
enabled by this transport.

## Enrollment receipt reconciliation

The native control carrier supports retrieval of an already committed enrollment
result. The client authenticates remembered server trust before sending its
enrollment ID, then proves possession of the registered access key over the fresh
TLS connection and exact prepared-result digest. No pairing secret is required.
Denial and inactivity prevent retrieval. A network failure does not prove that
enrollment failed; retain the private prepared record for a later authenticated
retry. Receipt retrieval cannot create an identity, grant access, or enter the
ordinary API. The CLI promotes the complete private result only after authenticated
confirmation, using the same private atomic custody contract as preparation.
Enrolled records are separate from provisional/prepared cleanup. Startup under the
exclusive authority instance lock recovers a validated interrupted SQLite journal;
network control readers never perform that recovery.

