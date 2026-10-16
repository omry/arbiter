# Prerelease Security Assessment and Remediation History

Consolidated: 2026-10-16

Evidence availability: this report retains the essential scope, findings,
commands, results and limitations for each round. The latest independent DBB
[probe and captured results](security-evidence/README.md) are retained in the
repository. References below to `temp/`, `/tmp/`, or per-user AWD state identify
original checkout-local evidence; those raw artifacts are not included in a
fresh clone. Their hashes record provenance, not a claim that they are publicly
retrievable. Historical results describe the stated revisions, not a fresh test
run on every later revision.

**Current state: actual DBB independently confirms the demonstrated R1 gaps are
fixed on merged main; R2/R3 remain resolved in the assessed scope.** The DBB
agent, using configured alias `gpt-daybreak-blue-latest`, reviewed
`19fb583c032abc6d013dbe6aa3f2342ff5a6e83f` and found no concrete security finding
in its bounded R1 and adjacent R2/R3 review. Its independent probes passed 14
checks per Python 3.10/3.13/3.14; targeted IMAP tests passed 101 per version;
real-loopback tests passed nine each on 3.10 and 3.14; 180 HTTP/artifact tests
passed. This agrees with the separate Sol assessment in Findings round 4.

DBB previously independently reassessed the R2/R3 merge at
`a5f8e23d5237d43eed6b8f7aa2ff549bf29bf7ba` in Findings round 3. DB-01 and DB-02
are fixed on the round 2 assessed revision. These focused findings do not grant
release-wide security signoff or a general security guarantee.

Under the owner's 2026-10-16 decision, configured IMAP servers remain trusted
while email content remains untrusted. The R1 byte-budget fix does not change
that trust boundary, establish general hostile-server support or set a process
memory ceiling. Authentication and mutual enrollment remain separate work.
Remote/shared deployment remains unsupported. No new risk acceptance is made.

This file preserves historical findings, dispositions and per-assessment limits.
Statements in earlier rounds refer to their stated revisions and delivery state.
Findings round 5 records the actual DBB merged R1 assessment; Findings round 4
records the separate Sol assessment. Remediation round 3 records the earlier
implementation and approval evidence. Model assessments
are not external third-party audits. Historical source line references belong
to the revision stated for each round.

- [Findings round 1](#findings-round-1)
- [Remediation round 1](#remediation-round-1)
- [Findings round 2](#findings-round-2)
- [Remediation round 2](#remediation-round-2)
- [Findings round 3](#findings-round-3)
- [Remediation round 3](#remediation-round-3--r1-upstream-response-bounds)
- [Findings round 4](#findings-round-4)
- [Findings round 5](#findings-round-5)

## Findings round 1

Historical findings at the revision below; subsequent dispositions are recorded
in Remediation round 1. The original assessment treated upstream protocol peers
as untrusted; the owner's later trusted-IMAP-server decision is recorded in
[Remediation round 2](#remediation-round-2).

Date: 2026-10-13  
Revision: `8d20f37b924234bb3693b9c57e5e71090d1cb928`

### Decision

Security signoff is **not granted** for the prerelease. Three findings should be
fixed before publishing: IMAP command injection, credential disclosure through
account/policy discovery, and missing resource bounds at the HTTP and mail
content boundaries.

The current documentation is accurate that caller authentication is absent and
that loopback clients must be trusted. It overstates credential protection in
practice because discovery can disclose plugin credentials whose field names
are not recognized by the server's redaction blacklist.

### Scope and threat model

Reviewed surfaces:

- native HTTPS server and Go client, including local self-signed TLS;
- local and Reploy/Docker deployment configuration, env files, permissions,
  storage, public diagnostics, and logs;
- plugin discovery/loading and package/release supply-chain assumptions;
- SMTP and IMAP policy enforcement, protocol clients, replay behavior, and
  partial outcomes;
- artifact creation and one-time delivery.

Protected assets are upstream credentials, mailbox/message contents, outbound
mail authority, policy/configuration integrity, idempotency records, artifacts,
TLS private keys, and server availability.

The assessed attacker is an untrusted or prompt-injected caller that can reach
Arbiter but does not control its deployment files; hostile email and upstream
protocol responses are also untrusted. The deployment operator, host
administrator, installed Python packages, and installed Arbiter plugins are
currently fully trusted. No active legacy public transport was found in server,
plugin, or native-client code.

Trust boundaries are:

1. caller to unauthenticated native HTTPS;
2. deployment config/env and runtime storage to the server process;
3. same-process plugin code to server secrets, filesystem, and network;
4. SMTP/IMAP clients to upstream services and hostile mailbox content;
5. server artifact storage to nonce-bearing HTTPS delivery;
6. source and CI workflows to built and published packages.

### Prioritized findings

#### DB-01 — Critical — IMAP arguments allow command injection

**Release blocker. Confirmed with a disposable wire-capture fixture.**

Caller-controlled search text and folder names are stripped but not rejected
for CR/LF. The IMAP client escapes quotes and backslashes only, then passes the
result to Python's `imaplib`, which concatenates command arguments and appends
CRLF. A crafted value produced a distinct, syntactically complete tagged IMAP
command line in the captured wire bytes.

Locations:

- [`plugins/imap/src/arbiter_imap/client.py`](../plugins/imap/src/arbiter_imap/client.py#L124)
  passes caller search text to the wire;
- [`plugins/imap/src/arbiter_imap/client.py`](../plugins/imap/src/arbiter_imap/client.py#L317)
  passes folder names to `SELECT`;
- [`plugins/imap/src/arbiter_imap/client.py`](../plugins/imap/src/arbiter_imap/client.py#L546)
  quotes without rejecting protocol control characters;
- [`plugins/imap/src/arbiter_imap/__init__.py`](../plugins/imap/src/arbiter_imap/__init__.py#L1037)
  strips surrounding whitespace but accepts embedded controls.

Prerequisites are a reachable IMAP operation and a configured account. Search
injection needs an allowed search operation; the generated default policy
allows search. Impact can include arbitrary additional IMAP commands under the
configured account, bypassing Arbiter's operation and folder policy.

Recommended fix slice: reject CR, LF, NUL, and other disallowed IMAP controls in
one shared encoder before any command is issued; prefer protocol literals where
supported; add wire-level tests for search text and every mailbox-bearing
operation. Keep this separate from other IMAP cleanup.

#### DB-02 — High — discovery redaction can disclose credentials

**Release blocker. Confirmed with a disposable OmegaConf fixture.**

The account and policy endpoints serialize almost the entire config, redact
fields by substring-matching their key names, and then resolve remaining
OmegaConf interpolations. A fixture field named `access` containing an
environment interpolation was returned as the resolved secret, while a field
named `password` was redacted. Third-party plugins can choose arbitrary schema
field names, so the server cannot enforce its documented promise that callers
never receive service credentials.

Locations:

- [`server/src/arbiter_server/main.py`](../server/src/arbiter_server/main.py#L2008)
  defines the key-name blacklist;
- [`server/src/arbiter_server/main.py`](../server/src/arbiter_server/main.py#L2052)
  redacts first and resolves the remaining values afterward;
- [`server/src/arbiter_server/main.py`](../server/src/arbiter_server/main.py#L2157)
  exposes policy rules and account config through discovery;
- [`website/docs/operate/security.md`](../website/docs/operate/security.md#L25)
  promises that agents do not receive credentials.

Prerequisites are any plugin credential or sensitive value under a key not
recognized by the blacklist and any caller that can reach discovery. Impact is
credential disclosure and direct upstream access outside Arbiter policy.

Recommended fix slice: make discovery output safe by construction. Return only
core public fields such as description, guidance, policy name, and explicit
plugin-owned public projections; never serialize arbitrary account/policy
config. Add fixtures using unknown credential names, nested collections,
`oc.env`, and `secret_file`. This needs a truth fix, not documentation that
narrows the promise.

#### DB-03 — High — unbounded inputs and mail materialization permit exhaustion

**Release blocker for minimum bounds. Confirmed at 2 MiB; upper bounds are absent
by code inspection.**

The HTTP route parses the complete JSON body without a configured size limit and
runs synchronous plugin work directly on the ASGI event loop. IMAP list/search
fetch complete RFC822 messages, parse full MIME trees, and decode attachment
payloads. Attachment delivery then holds the decoded bytes and writes them to
artifact storage without a size cap. A reachable caller can submit oversized
operation bodies; a hostile sender can place very large mail in an otherwise
allowed mailbox. Either path can consume memory, disk, CPU, or block health and
other requests.

Locations:

- [`server/src/arbiter_server/main.py`](../server/src/arbiter_server/main.py#L2197)
  reads JSON without a body limit;
- [`server/src/arbiter_server/main.py`](../server/src/arbiter_server/main.py#L2367)
  invokes synchronous operations in the async handler;
- [`plugins/imap/src/arbiter_imap/client.py`](../plugins/imap/src/arbiter_imap/client.py#L96)
  fetches and parses every selected full message;
- [`plugins/imap/src/arbiter_imap/client.py`](../plugins/imap/src/arbiter_imap/client.py#L395)
  requests `RFC822` without a size guard;
- [`plugins/imap/src/arbiter_imap/client.py`](../plugins/imap/src/arbiter_imap/client.py#L631)
  decodes attachment payloads to compute size/content;
- [`server/src/arbiter_server/artifacts.py`](../server/src/arbiter_server/artifacts.py#L122)
  writes arbitrary `bytes` without a configured maximum.

Recommended fix slices:

1. Add a server request-body ceiling with deterministic `413` responses and
   tests for missing, false, and oversized `Content-Length` values.
2. Add server-owned artifact byte/count/quota limits before writing data.
3. Add IMAP message/attachment limits and use metadata or bounded/partial fetch
   where possible; list/search should not fetch full attachments.
4. Move blocking operations off the event loop or enforce a bounded worker
   model so health and unrelated calls remain responsive.

#### DB-04 — Medium — generated policies grant broad ambient authority

**Needs an explicit product decision before release; not treated as a hidden
implementation default.**

The default IMAP bootstrap exposes every present and future folder and allows
read/search. The SMTP bootstrap rate-limits sends but leaves recipient allowlists
empty, which means unrestricted destinations. Operators are told to edit the
templates, but activating them unchanged gives a prompt-injected caller broad
mail authority.

Locations:

- [`plugins/imap/src/arbiter_imap/__init__.py`](../plugins/imap/src/arbiter_imap/__init__.py#L1456)
  selects `default-open` unless another variant is requested;
- [`plugins/smtp/src/arbiter_smtp/__init__.py`](../plugins/smtp/src/arbiter_smtp/__init__.py#L1268)
  generates unrestricted recipient lists.

Recommended disposition: decide whether bootstrap should fail closed by
default. If broad access is intentionally retained for bot-owned accounts,
require a conspicuous activation-time warning and document the blast radius,
including access to future folders. This is a policy/default decision, not a
mechanical patch.

#### DB-05 — Medium — HTTPS does not establish caller or local server identity

**Known limitation; acceptable only for an explicitly local, trusted-client
prerelease.**

There is no caller authentication or per-caller authorization. The default Go
client also disables certificate verification for loopback HTTPS when no CA file
is configured. A local process can invoke every configured operation; a process
that impersonates a stopped/restarting local server can capture operation
content or forge results. Binding and published Docker ports default to
loopback, which limits but does not remove the local trust assumption.

Locations:

- [`website/docs/operate/security.md`](../website/docs/operate/security.md#L5)
  accurately states the missing authentication boundary;
- [`client/go-cli/internal/cli/cli.go`](../client/go-cli/internal/cli/cli.go#L75)
  selects insecure TLS for loopback;
- [`server/src/arbiter_server/reploy/arbiter.blueprint.yaml`](../server/src/arbiter_server/reploy/arbiter.blueprint.yaml#L228)
  binds the container to all interfaces and publishes to host loopback.

Recommended fix slices: fail closed when binding/publishing beyond loopback
unless an explicit unsafe-local override is present; make the startup summary
warn about the unauthenticated boundary; design certificate pinning or local
trust enrollment separately from future caller identity. Remote, shared, or
hosted deployments are out of scope until authentication and identity exist.

#### DB-06 — Medium — every installed plugin is fully trusted executable code

**Known architectural limitation; documentation and supply-chain hardening are
needed before encouraging third-party plugins.**

The server imports and instantiates every installed `arbiter.services` entry
point during config registration, including plugins that are not active. Plugins
run in the server process and can read other plugin storage, process secrets,
and the network; `PluginStorage` is a convention, not an isolation boundary.

Locations:

- [`server/src/arbiter_server/config.py`](../server/src/arbiter_server/config.py#L204)
  loads every installed entry point for config registration;
- [`server/src/arbiter_server/plugins/__init__.py`](../server/src/arbiter_server/plugins/__init__.py#L18)
  loads and instantiates discovery entry points;
- [`.github/workflows/ci.yml`](../.github/workflows/ci.yml#L23) and the release
  workflows use mutable action tags and install ranged build tooling.

Recommended fix slices: document that installed plugins are arbitrary trusted
code and that activation does not create isolation; avoid installing unreviewed
plugins; pin security-sensitive GitHub Actions by commit and constrain release
build dependencies. Process isolation remains a larger post-v1 design and
should not be claimed by scoped storage.

#### DB-07 — Low — artifact bearer nonces can enter access logs

**Hardening item. Code-inspected against the installed Uvicorn implementation.**

Artifact capability URLs put the nonce in the query string. The installed
Uvicorn access logger formats the path with its query string, and Arbiter does
not disable or redact access logging. Operators are trusted, and successful GET
consumes the token, but centralized or broadly readable logs can expose a live
nonce after metadata/HEAD requests.

Location: [`server/src/arbiter_server/main.py`](../server/src/arbiter_server/main.py#L2552)
starts Uvicorn without an artifact-query redaction policy.

Recommended fix slice: suppress/redact artifact query strings in access logs or
move the bearer capability to a header. Document that `config show --resolve`
and `--value` can intentionally print resolved secrets and must remain an
operator-only command.

### Release blockers and accepted-risk candidates

Before prerelease:

- fix DB-01 and add wire-level regression tests;
- fix DB-02 with allowlisted public discovery projections;
- land minimum request, message, and artifact bounds from DB-03;
- update the security/plugin docs so they describe the resulting behavior.

Candidates for explicit, time-bounded acceptance in a local-only prerelease are
DB-04 through DB-07. Acceptance must state that all reachable callers and all
installed plugins are trusted, remote/shared deployment is unsupported,
loopback self-signed TLS is not server authentication, and resource isolation
between plugins does not exist.

### Positive controls observed

- HTTPS is mandatory; generated TLS keys and runtime/plugin directories receive
  restrictive modes, and runtime config/env permissions are checked.
- Docker publishes the default service on host loopback and does not mount the
  Docker socket into the container.
- SMTP recipient/rate policy is checked before submission; idempotency records
  preserve ambiguous outcomes and Sent-copy replay semantics.
- IMAP mutation policy is explicit and defaults deny for move, delete, append,
  and flag writes.
- Public operation errors are structured; unexpected exception text and locals
  are not returned or logged.
- Artifact IDs/nonces use cryptographic randomness, nonce comparison is
  constant-time, files are private, and content GET is one-time.

### Verification and limitations

Executed locally without live services:

- disposable fixtures confirmed DB-01, DB-02, acceptance of a 2 MiB operation
  body, and full materialization of a 2 MiB attachment;
- `172` focused server, artifact, IMAP, and SMTP unit tests passed;
- the assessment began from a clean Sapling checkout matching `remote/main` at
  the revision above; the unrelated `backlog-direct-pip-skill` shelf was not
  touched.

Not performed: live SMTP/IMAP testing, deployment or reverse-proxy penetration
testing, dependency vulnerability scanning, package provenance verification,
Windows ACL testing, browser-origin testing, fuzzing, or a fresh full-suite run.
The Nox lint session could not initialize because its isolated environment
needed dependency access to PyPI, which was unavailable in the sandbox.
The managed-environment status API was unavailable, so external network and
credential readiness were treated as unverified and no external services were
called.

## Remediation round 1

Delivered by assessed merged revision
`16b5e232c03f14d55db85d2122f84815dd1c42e4` (verified 2026-10-15).
The dispositions below are retrospective results from round 2, not claims
that all original findings were closed. Implementation details and executed
checks in this section come from the primary reassessment; the DBB follow-up's
independent checks and restrictions are recorded separately in Findings round 2.

| Finding | Classification at reassessment | Result |
| --- | --- | --- |
| DB-01, Critical: IMAP command injection | **Fixed** | Truth fix: shared control-character validation and quoting prevent caller text from introducing command framing. |
| DB-02, High: discovery credential disclosure | **Fixed** | Truth fix: arbitrary account/policy config is no longer serialized; absent projection hooks expose empty objects, and nested raw config is rejected. |
| DB-03, High: resource exhaustion | **Partially fixed; release blocker** | Truth fixes cover per-request body size, retained artifacts, ordinary single-message reads, previews, and blocking operation admission. Three gaps remain below, including the DBB follow-up's aggregate body-admission finding. |
| DB-04, Medium: broad bootstrap authority | **Accepted limitation** | Broad folder read/search and unrestricted SMTP destinations remain. Activation warnings and docs are representation/awareness mitigations, not reductions in authority. |
| DB-05, High: caller/server identity | **Deferred; trusted-local limitation accepted** | Authentication and local server identity remain absent. Startup/exposure warnings are implemented; they do not enforce a network boundary. |
| DB-06, Medium: plugin and supply-chain trust | **Partially fixed; active-code trust accepted** | Truth fixes avoid unnecessary inactive entry-point execution and constrain release tooling/actions. Active or explicitly loaded plugins still have process authority. |
| DB-07, Low: artifact nonces in access logs | **Fixed in the standard launcher** | Truth fix: artifact queries are removed before native access-log emission, including encoded routes. External hosts/proxies require their own suppression. Display resolver masking is also implemented within its agreed scope. |

These classifications record the assessment before the 2026-10-16 scope
decision. DB-03-R1's later acceptance is recorded in Remediation round 2;
DB-03 remains a release blocker because R2 and R3 are unresolved.

### DB-01: command framing

[`wire.py:6`](../plugins/imap/src/arbiter_imap/wire.py#L6) rejects ASCII controls
`0x00` through `0x1f` and `0x7f` before escaping quotes/backslashes.
[`client.py:159`](../plugins/imap/src/arbiter_imap/client.py#L159) validates folder
and search arguments before establishing the session; other mailbox-bearing
operations use the same validator/encoder.

Executed: all **178** wire regressions, including the original CRLF injection,
every mailbox-bearing operation, valid escaped strings, and APPEND literals.
An independent **66-case** probe covered every rejected ASCII control in both
folder and search text and observed zero command bytes. Valid message-body
newlines remain permissible inside APPEND literals.

Boundary: this fixes caller argument framing. It does not make upstream IMAP
responses trusted or establish caller authentication.

### DB-02: discovery projections

[`services.py:699`](../server/src/arbiter_server/services.py#L699) defaults missing
projection hooks to `{}` and recursively rejects OmegaConf/dataclass values
through [`services.py:23`](../server/src/arbiter_server/services.py#L23).
The native account/policy responses use those hooks at
[`main.py:2126`](../server/src/arbiter_server/main.py#L2126).
The bundled [IMAP projection](../plugins/imap/src/arbiter_imap/discovery.py#L18)
and [SMTP projection](../plugins/smtp/src/arbiter_smtp/__init__.py#L1333) select
public fields rather than copying arbitrary configuration.

Executed: discovery endpoint tests with unknown credential names, nested
collections, raw nested OmegaConf/dataclass projections, and private resolvers;
all **10** bundled projection tests passed. An independent fixture placed
`oc.env` and `secret_file` under unknown nested keys and queried plugin,
account, and policy discovery. Both resolver call counts stayed zero, and the
private fields did not appear.

Boundary: deliberately public descriptions, guidance, folder names, and other
selected fields remain public, including interpolations placed there by the
operator. Trusted plugin code can deliberately return secrets; projection
validation is not an isolation boundary or a general secret detector.

### DB-03: implemented resource controls

The [core defaults](../server/src/arbiter_server/limits.py#L5) are 1 MiB per
operation request, 50 MiB per artifact, 1 GiB retained artifact bytes, 2,000
retained artifacts, and eight blocking workers.

[`main.py:2172`](../server/src/arbiter_server/main.py#L2172) checks declared length
and counts actual streamed bytes before JSON parsing. Tests cover absent,
understated, malformed, enormous, and oversized lengths; encoded-byte
boundaries; and rejection before reading further chunks or invoking plugins.
The original oversized-body path now returns HTTP 413.

[`artifacts.py:207`](../server/src/arbiter_server/artifacts.py#L207) rejects
oversized payloads before hashing/writing. Admission includes metadata and
retained count under a [cross-process root lock](../server/src/arbiter_server/artifacts.py#L448).
All **55** artifact tests passed, covering exact byte boundaries, cross-plugin
and restart accounting, count/byte races across store instances/processes,
consumed-artifact retention, expiry, crash orphans, temporary-file cleanup,
failed creation, failed access updates, and atomic one-time claims. These are
retained-storage controls, not a filesystem quota on every transient write or
a cap on other plugin data.

The [blocking pool](../server/src/arbiter_server/blocking.py#L24) admits without
an unbounded work queue and retains a slot until underlying work ends after
request cancellation. Its six direct tests and native route tests passed:
ninth-operation rejection, event-loop responsiveness during ordinary plugin
work, cancellation, failed thread startup, operation errors, and shutdown.
The cap limits concurrent work; it cannot cancel a running Python thread or
guarantee recovery from eight hung upstream operations.

[IMAP full reads](../plugins/imap/src/arbiter_imap/client.py#L584) request size
metadata and bounded `BODY.PEEK[]` content, check returned octets, and reject a
single excessive guarded literal before its body is read. Size metadata may be
an estimate; a bounded fetch still occurs to establish the actual size. The
whole-message limit defaults to 50 MiB, including encoded attachment content.

[List/search summaries](../plugins/imap/src/arbiter_imap/client.py#L398) request
ENVELOPE/FLAGS/BODYSTRUCTURE, then share a 16 KiB text-preview budget across
plain/HTML candidates without fetching attachment bodies. The **41** IMAP
limit tests and **32** summary tests passed. Coverage includes hostile size
metadata, UID trailers/ambiguity, oversized literals, transfer-encoded partial
previews, nested attached messages, inline text attachment exclusion, malformed
MIME metadata, and RFC2231 filenames. Metadata has a separate **50 MiB**
transport budget; the 16 KiB value limits preview text, not all metadata.

### DB-04 and DB-05: retained policy and identity limitations

[IMAP activation guidance](../plugins/imap/src/arbiter_imap/__init__.py#L1527)
states that default-open covers every current and future folder and explains
default-closed. [SMTP guidance](../plugins/smtp/src/arbiter_smtp/__init__.py#L1369)
states that destinations are unrestricted. The
[activation path](../server/src/arbiter_server/main.py#L3872) emits this guidance;
the full suite exercised bootstrap variants and activation without resolving
credentials. These satisfy the approved warning/documentation disposition,
while prompt injection can still exercise all granted mail authority.

[`log_startup_summary`](../server/src/arbiter_server/main.py#L1237) warns for
every unauthenticated startup and additionally for nonliteral-loopback binds.
Tests cover loopback IPs, wildcard/network IPs, and hostnames. It does not refuse
startup, require an override, or verify Docker publication. Docker network peers
and forwarded clients must also be trusted.

The [Go client](../client/go-cli/internal/cli/cli.go#L87) still selects insecure
TLS for loopback HTTPS without a CA file. Local impersonation can capture
operation content or forge responses when the genuine server is unavailable.
This and caller authentication remain deferred to a separate identity/enrollment
effort requiring its own authorization; no protocol is selected here. Warning text and
[security documentation](../website/docs/operate/security.md#L3) accurately
describe the boundary. No stronger network or identity protection is claimed.

### DB-06: selective imports and release tooling

[`plugin_selection.py:31`](../server/src/arbiter_server/plugin_selection.py#L31)
inspects raw defaults/overrides before full composition.
[Config registration](../server/src/arbiter_server/config.py#L329) and
[runtime discovery](../server/src/arbiter_server/plugins/__init__.py#L27)
filter entry points before `.load()`. All **30** selection tests passed,
including poison inactive entry points, cold schema selection/deletion,
optional/default overrides, search paths, inline and interpolated maps,
diagnostics, and targeted bootstrap. Active/explicit plugin code remains fully
trusted; registration itself can execute it. Plugin storage does not isolate
secrets, filesystem access, or the network.

All external workflow action references passed the full-SHA/version-comment
check. Release/build jobs install the
[hashed lock](../.github/requirements-release.txt#L1) using
`--require-hashes --only-binary=:all:` and the publishing build uses
[`--no-isolation`](../.github/workflows/publish.yml#L338).
The three workflow supply-chain regressions passed. An independent offline
pip dry run rejected a synthetic local wheel with a mismatched locked hash
before installation. This checks hash enforcement, not the legitimacy or
vulnerability status of the committed pins. General CI/developer dependencies
remain ranged; the lock claim is limited to the approved release/build tooling.

### DB-07: logs and operator config output

[`_ArtifactAccessLogFilter`](../server/src/arbiter_server/main.py#L2598), installed
by the standard launcher, removes the whole artifact query before log handlers
or formatters see it. All **10** log tests passed, including disposable HTTPS
metadata/HEAD/GET requests, failures, percent-encoded routes, mounted/encoded
path variants, and nonce inspection in the raw log records. Nonce validation
and one-time delivery still work. External ASGI hosts, proxies, traces, and
debug/error logs need independent suppression; Arbiter cannot retract those
records.

[`config_for_display`](../server/src/arbiter_server/config.py#L213) masks
`oc.env` and `secret_file` using display-local context before scalar selection
at [`main.py:3172`](../server/src/arbiter_server/main.py#L3172).
All **34** display tests passed, including typed values, aliases, nested
expressions, cached resolvers, missing inputs, error recovery, and other runtime
threads. An independent scalar/alias fixture masked both synthetic secret
sources while runtime resolution still returned their actual values.

All environment-derived display values, including nonsensitive host/port
values, mask. Literal YAML secrets and unresolved interpolation references
remain printable by design, so output stays operator-only. This is a truth fix
for the agreed resolver-derived output, not a general config-secret guarantee.

## Findings round 2

Date: 2026-10-15  
Assessed merged main: `16b5e232c03f14d55db85d2122f84815dd1c42e4`  
Baseline revision: `8d20f37b924234bb3693b9c57e5e71090d1cb928`

### Decision at assessment

The following is the historical assessment decision under its original
hostile-upstream threat model. The subsequent owner-approved disposition of
DB-03-R1 is recorded in [Remediation round 2](#remediation-round-2).

**Local-only prerelease security signoff is withheld.** DB-01 and DB-02 are
fixed on the assessed revision. DB-03 has substantial working controls, but
hostile IMAP responses can still bypass resource admission, artifact HTTP
handlers can still block the ASGI event loop, and concurrent request bodies
can consume memory before worker admission. DB-03-R1 and DB-03-R2 are residuals
of the original release blocker; DB-03-R3 is an additional finding from the
DBB follow-up in this round. All three remain open; no new risk acceptance is
inferred.

The owner-approved broad mail policies, trusted local caller model, active
plugin trust, and deferred authentication/server identity remain explicit
limitations. Their documentation does not eliminate their underlying risks.
Remote/shared deployment remains unsupported.

This assessment independently inspected merged source and executed synthetic
reproductions. PR review approvals and pre-merge CI were context only. No
product fixes, live-account operations, deployment, publishing, merge, or
release were performed.

### Assessment provenance

The primary reassessment independently inspected merged source and executed
synthetic reproductions, identifying DB-03-R1 and DB-03-R2. The DBB follow-up
reports against the same assessed revision, independently reproduced both,
and established DB-03-R3. Its findings are incorporated below without claiming
that this consolidation reran them. The later DBB activation in this chat was
read-only orientation and did not produce those assessment results.

The DBB follow-up identifies its assessor as the Daybreak Blue model under the
stated runtime routing. Neither assessment constitutes an external audit.
DB-05 was rated Medium by the baseline and DBB under the trusted-local scope;
the primary reassessment's disposition table retained a High label. No rating
implies that remote/shared deployment is supported.

### Revision and evidence integrity

`git ls-remote https://github.com/omry/arbiter.git refs/heads/main` returned the
revision above; Sapling's checkout and `main` matched it. No checkout update was
needed. At entry, the only pending file was the untracked baseline report.
At that time, the baseline report was unchanged with SHA256
`40739dac4578429c89af2f2212f3453cc81110a7bb6c47665261b4902650603b`.

The approved dispositions in `temp/security-remediation-state.json`, especially
`disposition_round` and `remediation_delivery`, were read alongside actual
source. Older fields describing deferred implementation were not treated as
current delivery status. No additional risk acceptance was inferred.

Local reproduction source, JUnit evidence, and an execution summary are retained
under `temp/security-reassessment/`. These are checkout-local evidence, not
published package contents. The report below also records the triggering
conditions and relevant source so its conclusions do not depend on historical
PR approvals.

That hash identifies the historical standalone baseline, not this consolidated
file. Its findings are preserved in Findings round 1 above.

### DB-03-R1 — High — hostile IMAP responses escape aggregate admission

**Current disposition (2026-10-16): accepted limitation; hostile-server
hardening deferred.** The owner trusts configured IMAP servers while treating
email content as untrusted. The reproduced protocol-peer attacks below fall
outside that supported trust boundary. The gap remains unfixed; High records
the original assessment severity under hostile-server prerequisites, not a
remaining prerelease blocker under the approved model. The evidence is retained
for future hardening or a change in trust assumptions.

Prerequisites: an invoked IMAP operation and a malicious/compromised upstream
server, or an attacker able to alter that protocol connection. Sending an
ordinary email through a conforming upstream alone does not establish this
prerequisite. Impact: allocation controlled by upstream response volume can
exceed the configured message cap and exhaust process memory; eight workers
bound concurrency, not bytes within one response.

Two independent transcripts exercised the real stdlib IMAP command/response
parser with an in-memory transport, for **both** `BoundedIMAP4` and
`BoundedIMAP4SSL`. TLS/socket setup was bypassed; protocol parsing and Arbiter's
read guards were not mocked.

1. With `max_message_bytes=16`, the size-query response included an unsolicited
   64-byte BODY literal for UID 99 followed by valid size metadata for requested
   UID 42. Arbiter read all 64 bytes, then fetched and returned the requested
   16-byte body successfully. The size query at
   [`client.py:592`](../plugins/imap/src/arbiter_imap/client.py#L592) has no guard;
   [`transport.py:105`](../plugins/imap/src/arbiter_imap/transport.py#L105) enforces
   the literal cap only while a guard is active. FLAGS and SEARCH also execute
   outside the summary metadata guard.
2. A valid size response was followed by eight 16-byte BODY literals in one
   full-content FETCH. All **128 literal bytes** were read and retained by
   imaplib before Arbiter returned the first 16-byte body. The full-fetch call
   at [`client.py:613`](../plugins/imap/src/arbiter_imap/client.py#L613) enters
   `message_literal_guard` without an explicit maximum. Consequently
   [`transport.py:62`](../plugins/imap/src/arbiter_imap/transport.py#L62) never
   initializes its remaining-byte counter: each literal is checked separately,
   with no aggregate decrement. Response framing outside metadata mode also
   lacks an aggregate budget.

The small fixtures demonstrate bypass without an OOM stress test. The source
has no aggregate bound on the number of such responses; whole-process memory
exhaustion at default limits is an inference from that unbounded accumulation,
not an executed destructive experiment. Duplicate bodies were accepted rather
than rejected after parsing; post-parse validation alone would still be too
late to prevent their allocation.

Deferred hardening recommendation: enforce finite aggregate protocol byte/literal budgets
before allocation for every command phase, including unsolicited responses and
non-body queries. Initialize/decrement the full-fetch budget, account for
framing and repeated literals, reject ambiguous full-body responses, and close
the session on overflow. Add transcript regressions for both transport classes
and verify rejection before the excessive read. Preserve the intended support
for bounded summaries of oversized messages.

### DB-03-R2 — Medium — artifact handlers bypass event-loop protection

Prerequisites: any reachable caller can send an artifact request with a
nonempty nonce while artifact storage is busy. A valid bearer nonce is not
required. Ordinary concurrent artifact creation/cleanup can hold the root
lock; the attacker need not control deployment files.

[`main.py:2412`](../server/src/arbiter_server/main.py#L2412) and
[`main.py:2433`](../server/src/arbiter_server/main.py#L2433) directly call
`inspect`/`open_once` from async handlers. Those methods take the
[blocking storage lock](../server/src/arbiter_server/artifacts.py#L298), purge
expired entries, read metadata, and write access bookkeeping before returning.
They do not enter the operation worker pool. The asynchronous content generator
also performs [synchronous file reads](../server/src/arbiter_server/main.py#L2454).

Executed: a separate thread held the real artifact root lock for 250 ms after
the request began. Metadata GET, content HEAD, and content GET were each run
on the same loop as a scheduled health request. All six valid/invalid-nonce
cases delayed health until approximately **251 ms**; artifact responses were
200 for valid nonces and 404 for invalid ones. The scheduled 10 ms coroutine
could not resume while the handler waited in `flock`.

The finite lock hold models storage contention; no filesystem saturation or
production-scale latency was measured. It proves the scheduling gap directly.
Impact is server-wide stalls during contention/slow storage and a repeatable
availability amplification path through repeated artifact requests. This
remains part of DB-03's original event-loop concern, not a new policy risk.

Focused remediation: move artifact lookup, cleanup, bookkeeping, lock waits,
and streaming file I/O off the event loop under bounded admission. Keep health
responsive during real concurrent creation/purge and invalid-nonce traffic;
test cancellation and capacity recovery without weakening one-time claims.

### DB-03-R3 — Medium — concurrent request bodies bypass worker admission

Source: the DBB follow-up in this round; see its [verification record](#dbb-follow-up-verification).
The evidence below is reported by that assessment and was not rerun for this
documentation update.

Prerequisites: a reachable caller able to maintain many concurrent operation
POST requests. Under the supported trusted-local model this is a local process;
remote/shared exposure would increase exploitability and remains unsupported.

The operation body is streamed and parsed before `operation_pool.run` acquires
a worker slot ([`main.py:2380`](../server/src/arbiter_server/main.py#L2380)).
The standard launcher supplies no concurrency limit to `uvicorn.run`
([`main.py:2620`](../server/src/arbiter_server/main.py#L2620)); the DBB report
records `limit_concurrency=None` as the installed Uvicorn 0.54.0 default.
The 1 MiB body ceiling is per request, while the eight-worker cap does not
bound concurrent readers before operation admission.

Reported reproduction: 24 synthetic request streams each yielded a valid
sub-limit first chunk and paused. All 24 private bytearrays were held
simultaneously, totaling **6,290,400 bytes**, before worker admission. After
release, all requests parsed successfully. This verifies application admission
order with synthetic streams; it did not use real sockets, establish a
production connection ceiling, or execute a destructive OOM test.

Impact: memory and connection usage scale with caller-controlled concurrency
before the worker gate. Slow request bodies can occupy HTTP work before plugin
capacity is considered; the exhaustion threshold depends on deployment and
runtime overhead.

Focused remediation: acquire bounded request/body admission before reading the
stream, release capacity on rejection, disconnect, parse failure, and completion,
and return a deterministic retryable response when full. Add server concurrency
limits as defense in depth. Verify concurrent absent/understated lengths, slow
and cancelled bodies, capacity recovery, and exact byte boundaries while
preserving the per-request ceiling. This remains open for the main chat.

### Primary reassessment verification and limits

Executed against the assessed checkout on Linux, Python 3.13.12:

| Check | Observed result |
| --- | --- |
| Full Python suite through the existing clean Nox test environment | **1,137 passed, 2 skipped**, 45.09 seconds |
| Focused security regressions across the 11 modules discussed above | **488 passed**, 4.17 seconds; JUnit retained locally |
| Independent synthetic control and bypass probes | **79 passed**, 1.97 seconds; resource-gap tests assert vulnerable behavior |
| Nox lint with Black single-worker mode and Pyrefly | Passed; 89 files unchanged, zero type errors, five existing suppressions |
| Go 1.26.4 `go test -count=1 ./...` | Passed without cached test results |
| Website `npm run build` | Passed |

Full-suite command:

```sh
env -u PYTHONPATH BLACK_NUM_WORKERS=1 .venv/bin/nox \
  --envdir /tmp/arbiter-error-contract-nox -r --no-install -s tests
```

Independent-probe command:

```sh
env -u PYTHONPATH /tmp/arbiter-error-contract-nox/tests/bin/pytest -q -s \
  temp/security-reassessment/test_security_reassessment.py \
  --junitxml=temp/security-reassessment/independent-probes.xml
```

The first sandboxed suite could not create local sockets and was superseded
and terminated. An unrestricted run using the developer `.venv` had 1,135
passes, two skips, and two failures: its installed extra `github` entry point
broke an exact plugin-list assertion, and a sent-copy concurrency test timed
out. The concurrency test passed alone and in the clean full run; no root
cause was established for that transient timeout. No existing test/product changes
were made to obtain the clean result. The two clean-run skips require real
Windows ACL checks.

No live mail accounts, deployment/reverse-proxy penetration, browser-origin
security, fuzzing, dependency vulnerability scan, action/package provenance
verification, Windows execution, or destructive OOM/disk exhaustion was tested.
The fixtures do not establish a global process-memory ceiling, MIME
depth/part/CPU bounds, HTTP connection/body admission across concurrent
requests, or operational recovery from permanently hung upstream services.
The subsequent DBB report establishes the synthetic concurrent-body admission
gap recorded as DB-03-R3; real-socket connection limits remain unverified.
Finite raw message bytes can still expand during MIME parsing/serialization.
These boundaries remain unverified; no additional risk acceptance or general
security guarantee is implied by passing tests.

### DBB follow-up verification

The following records the DBB follow-up's own checks and limitations.

Fresh completed checks:

- 251 IMAP wire/limit/summary tests passed;
- 77 discovery, plugin-selection, config-display, and release supply-chain tests
  passed;
- 55 artifact size/quota/count/cleanup/concurrency tests passed;
- 8 artifact access-log filter unit cases passed;
- 79 inspected prior synthetic probes were replayed and passed;
- 4 additional request-body/admission probes passed; and
- 2 targeted Go TLS-selection tests passed.

JUnit and synthetic-only evidence are under
`temp/dbb-security-reassessment/`. The prior parent's 1,137-pass run is context,
not evidence claimed by this assessment.

The full Python run was stopped after restricted socket failures and an
event-loop hang. The full Go run likewise failed when `httptest` could not open
loopback listeners. Sandbox settings were not loosened. A minimal
`asyncio.to_thread` completion also hung, preventing fresh blocking-pool runtime
verification. These are environment limitations, not passing results or product
failures.

Not tested: live IMAP/SMTP accounts, real TLS interception, deployment or
reverse-proxy penetration, browser-origin behavior, dependency vulnerability or
provenance scanning, Windows ACL/runtime behavior, fuzzing, destructive
OOM/disk exhaustion, production slow-storage behavior, or recovery from eight
permanently hung upstream calls. MIME nesting/part-count/CPU amplification and
the peak expansion of a bounded 50 MiB raw message during parsing/decoding were
not exhaustively characterized. Passing focused tests do not establish a global
process-memory, connection, transient-disk, or CPU ceiling.

## Remediation round 2

**Two approved PRs delivered; merge and release reassessment pending.** R2 and
R3 are truth fixes in the approved PR heads, not claims about unchanged main.
The stack starts from `16b5e232c03f14d55db85d2122f84815dd1c42e4`:

- R2: [PR #29](https://github.com/omry/arbiter/pull/29),
  `5fb68aee6b1a34c3f9e4092ca468cb4bbc1475ba`, based on main.
- R3: [PR #30](https://github.com/omry/arbiter/pull/30),
  `2270243865eb065c8ab03fcf52bf7f1b07fae061`, based on PR #29.

Each slice received local review and a remote PR review cycle. The native
stack is #31 and contains exactly these two PRs. No merge, deployment,
live-account operation, DBB delegation, new security scan or release occurred.
Security signoff remains withheld.

### Owner-approved IMAP trust boundary — 2026-10-16

The owner explicitly decided: **configured IMAP servers are trusted; email
content is not.** The supported prerelease does not promise containment of a
malicious or compromised IMAP protocol peer. That peer can control responses
and service behavior beyond the specific byte-budget bypasses demonstrated in
R1, so closing those bypasses alone would not establish hostile-server support.

Disposition: **DB-03-R1 is an accepted limitation, with hostile-server hardening
deferred.** Its demonstrated behavior and evidence remain valid and unfixed.
This changes the release disposition and report representation; it is not a
truth fix. Revisit R1 if configured providers cease to be trusted or support
for hostile protocol peers is added.

Untrusted message content remains in scope, including message bodies, headers,
attachments, and MIME structure. Trusting the provider does not authorize
trusting sender-controlled content or removing existing size, preview, parsing,
or artifact protections. No new claim about comprehensive MIME safety or global
resource bounds follows from this decision. The dispositions of R2, R3, and the
other accepted limitations are unchanged; local-only signoff is still withheld.

### DB-03-R2 — bounded artifact work off the event loop

**Truth fix in approved PR #29.** Artifact metadata GET and content HEAD run
storage locking, cleanup, inspection and access bookkeeping in the existing
blocking pool. Content GET reserves one pool slot for lookup, the one-time
claim, file open/read/close and streaming backpressure. It sends at most one
64 KiB chunk at a time, waiting for the prior send before reading another.
It does not buffer the complete artifact or reacquire capacity per chunk.

The new `server/src/arbiter_server/http_work.py` bridge keeps asynchronous ASGI
receive/send on the loop and synchronous resource ownership on the worker.
Cancellation, disconnect and shutdown stop pending transport waits. A running
file read or storage-lock wait still owns its slot until it finishes; an open
file closes on that worker before capacity returns. Repeated shutdown does not
interrupt already-running transport cleanup. A post-start streaming error
cannot send a second response header block.

HEAD and metadata remain non-consuming; GET retains atomic one-time claims.
Disconnecting after a GET claim does not restore the capability. Real storage
lock contention with valid and invalid nonces no longer prevents health from
responding. Synthetic slow reads and blocked sends verify file closure,
backpressure, bounded admission, cancellation and capacity recovery.

### DB-03-R3 — admission before request-body reads

**Truth fix in approved PR #30.** An operation acquires an existing worker
slot before its first body read. Body collection keeps the existing actual-byte
ceiling (default 1 MiB); JSON parsing, invocation and response preparation run
on that same admitted worker. Excess callers receive the existing HTTP 503
`resource_exhausted`, `transient: yes`, `replay: safe` response without invoking
ASGI receive or parsing JSON. There is no unbounded waiting queue.

Declared Content-Length is checked before worker admission without reading
body bytes. Invalid or oversized declarations retain HTTP 400/413 even when
the worker pool is full; actual body collection and parsing remain admitted.
Malformed JSON and invalid request shapes retain HTTP 400, oversized actual
bodies retain HTTP 413, and plugin argument validation retains HTTP 422. Disconnects,
cancellation, malformed/oversized bodies, normal completion and shutdown return
capacity after the underlying work or transport cleanup ends. The bridge
cancels the actual loop task rather than prematurely cancelling its cross-thread
Future, so transport cleanup completes before its worker exits.

`arbiter.limits.max_blocking_workers` remains eight by default, now shared by
body readers, plugin/response work and artifact I/O/streams. No setting, default,
operation timeout, or overload classification was added. Health and discovery
remain outside this pool. Slow clients can occupy its slots until they finish
or disconnect; blocking workers cannot forcibly cancel hung Python operations.

### Launcher concurrency assessment and remaining boundaries

Uvicorn's global concurrency ceiling was considered as defense in depth. It
also counts connection/keepalive traffic and would change rejection behavior
for health/discovery. This hotfix therefore does not silently introduce a new
launcher-wide default. Application admission is enforced even under an external
ASGI launcher; deployment/proxy connection and transport-buffer limits remain a
separate operator concern. The application shutdown hook cancels bridge waits;
launcher graceful-shutdown deadlines remain unchanged. No global process-memory,
connection, CPU or transient storage ceiling is established by these fixes.

The IMAP trust decision above is unchanged: R1 remains accepted and unfixed;
sender-controlled email/MIME content remains untrusted. Authentication/server
identity, broad bootstrap authority, active-plugin process trust, external-log
suppression and literal-secret config-output limitations remain as previously
recorded. Remote/shared deployment is still unsupported.

### Initial combined-patch verification (historical)

**Working-tree hotfixes verified; release reassessment pending.** R2 and R3
are truth fixes in the current patch, not claims about the unchanged merged
revision. This delivery starts from
`16b5e232c03f14d55db85d2122f84815dd1c42e4`. No commit, push, PR, merge, deployment,
live-account operation, DBB delegation, new security scan, or release occurred.
The executing agent reviewed the combined patch; no independent review or
security signoff is claimed for this delivery.

The patch-only endpoint and results in this subsection predate the separate
PR delivery below. They describe that earlier working tree, not current PR
status.

All final checks below cover the same source/test/doc bytes; their SHA256
manifest, commands, runtimes, log hashes and reused successful check evidence
are in `temp/resource-hotfixes/delivery-result-final.json`.

| Check | Observed result |
| --- | --- |
| Full Python suite in a fresh Nox environment, Python 3.13.12 | **1,179 passed, 2 skipped** |
| Focused R2/R3 and lifecycle regressions in the final full suite | **42 passed**, including 17 real-socket cases |
| Python 3.10.20 targeted native HTTP/artifact/log/pool checks | **202 passed** |
| Python 3.14.2 targeted native HTTP/artifact/log/pool checks | **202 passed** |
| Complete repository Black/Pyrefly targets from noxfile.py | Passed with Black single-worker mode; zero type errors |
| Go `go test -count=1 ./...` | Passed |
| Website `npm run build` | Passed |

The resource regressions cover real root-lock contention for valid/invalid
nonces, metadata/HEAD/GET, slow reads, blocked sends, cancellation, shutdown,
file closure, shared body-reader/artifact capacity and one-time delivery.
Body tests cover concurrent slow readers, rejection before receive, missing and
understated lengths, actual byte boundaries, malformed JSON, disconnects and
cleanup. Real socket tests exercise chunked/length-delimited requests, overload
and oversized-header rejection before `100 Continue`, recovery and health
while artifact storage is contended. Synthetic understated-length tests check
ASGI byte accounting; conforming real HTTP framing uses actual Content-Length
or chunked transfer rather than treating pipelined bytes as one body.

Full-suite command:

```sh
env -u PYTHONPATH BLACK_NUM_WORKERS=1 .venv/bin/nox \
  --envdir /tmp/arbiter-resource-hotfix-nox -s tests
```

Compatibility commands use the same fresh Nox directory with `-s compat-3.10`
or `-s compat-3.14`, targeting `test_http_resource_admission.py`,
`test_native_http_protocol.py`, `test_artifacts.py`,
`test_artifact_access_logs.py`, and `test_blocking_pool.py` under
`server/tests/unit/`. Fresh environments use the declared OmegaConf
`2.4.0.dev11` and Hydra `1.4.0.dev4`.

Earlier results are retained rather than rewritten. An initial combined run
exposed global Uvicorn logging state leaked by the new socket fixture; the
fixture now isolates/restores that state, and the existing access-log tests
pass together with the new tests. Reused Nox environments then proved stale
(OmegaConf 2.3.1/Hydra 1.3.3 and missing build dependencies; the Python 3.14
child processes also lacked an editable server install). Their failed runs
are superseded by fresh environment runs; no product assertions were weakened
to obtain a pass. Successful unchanged lint, Go and website checks were reused.

The two skips require real Windows ACL checks. Remote CI and non-Linux runtime
matrices were not run; there is no PR. No live mail accounts, penetration tests,
fuzzing, new vulnerability/provenance scans, destructive exhaustion, production
slow-storage benchmarks, or recovery from permanently hung calls were tested.
Historical assessment evidence and per-assessment limitations remain intact.

### Two-PR delivery and current approval evidence

Each PR has a clean local review and completed remote review cycle. All **13
required CI checks pass on each exact head**: Python lint; Python 3.10, 3.11,
3.12 and 3.14; Linux, macOS and Windows on x64 and arm64; Website; and Gitar.
The current approval readback is `temp/two-pr-delivery/two-approved-prs.json`.

| Slice | Full Python suite | Added resource cases | Targeted Python 3.10 / 3.14 |
| --- | --- | --- | --- |
| R2, PR #29 | **1,157 passed, 2 skipped** | **20** | **180 / 180 passed** |
| R3, PR #30, including R2 | **1,182 passed, 2 skipped** | **25** | **205 / 205 passed** |

There are **45 new resource cases**, including **18 real-socket cases**, across
the two slices. Black (single worker), Pyrefly, Go tests and the website build
also pass. The skips require real Windows ACL checks; CI platform build jobs
do not establish that those skipped ACL tests ran.

Gitar identified incorrect 400/413 precedence when the pool was full. Three new
regressions failed against the original R3 implementation and pass on its
corrected head. This is a truth fix: declared invalid/oversized lengths are
rejected before admission, with no body read. The correction did not change
R2 or add a setting/default. Evidence:
`temp/two-pr-delivery/r3-header-regression-evidence.json`.

One full Python run on the corrected R3 head had an existing SMTP concurrency
test exceed its three-second wait: 1 failed, 1,181 passed, 2 skipped. The focused
test and full-suite retry passed on unchanged code. The cause was not proven;
no SMTP fix or weakened test is claimed. The failed run and successful retry
remain in `temp/two-pr-delivery/r3-corrected-published*/`. Successful unchanged
lint, types, Go, website and compatibility results were reused only after
source, dependency, command and log hashes were verified.

R2 approval was refreshed after the generated stack footer changed a canonical
body boundary newline. Its code, diff, base and scope did not change. R3 then
received a fresh review request **after that current parent approval**:

- R2 clean review: [6200044138](https://github.com/omry/arbiter/pull/29#issuecomment-6200044138).
- R2 current approval: 2026-10-15T20:51:29Z.
- R3 fresh review request: [6202049762](https://github.com/omry/arbiter/pull/30#issuecomment-6202049762),
  2026-10-15T20:53:13Z.
- R3 clean review: [6202085467](https://github.com/omry/arbiter/pull/30#issuecomment-6202085467).
- R3 current approval: 2026-10-15T20:57:09Z.

Exact head, base, canonical metadata, review requests/results, CI and approval
receipts are retained under `/home/omry/.local/state/awd/swe-review-authority/runs/run_7mytvrnsq624zkt20c2z9mqgjn`. Full R2 local evidence is
`temp/two-pr-delivery/r2-footer-refresh/result.json`; final R3 evidence is
`temp/two-pr-delivery/r3-corrected-published-final/result.json`.

The installed AWD helper did not recognize a newer Codex clean-review receipt
wrapper. A narrow compatibility patch was tested and used from an isolated
pinned source checkout; this delivery did not deploy global AWD. The patch is
`temp/two-pr-delivery/awd-receipt-compatibility.patch`. The related helper/native
checks passed (513 tests); no full AWD-suite pass is claimed. Historical failed
workflow attempts remain recorded and were not relabeled as successful.

### Remaining release action

Merge the two approved PRs, then independently reassess the corrected merged
revision under the owner-approved trust model before granting local-only
security signoff. These truth fixes and review approvals do not themselves
grant security signoff or accept any new risk. R1 remains accepted and unfixed.
Authentication and bidirectional enrollment remain separate work.

## Findings round 3

Date: 2026-10-16  
Assessor: DBB subagent, independent of the implementing agent  
Assessed merged main: `a5f8e23d5237d43eed6b8f7aa2ff549bf29bf7ba`

This round supersedes the pre-merge status statements in Remediation round 2;
those statements and their evidence remain as historical records. PR #29
merged as `c85663b74a79bb6bc7e44393dc03d005eb49ee64`; PR #30 merged as the
assessed main revision. DBB fetched the merged revision and verified an empty
tracked diff against approved PR #30 head
`2270243865eb065c8ab03fcf52bf7f1b07fae061`, where it executed the checks.

### Disposition

- **DB-03-R2: resolved on merged main (truth fix).** Artifact locking, storage,
  claims, reads and streaming use bounded workers. Cancellation retains capacity
  until underlying work and transport cleanup finish; health remains responsive.
- **DB-03-R3: resolved on merged main (truth fix).** Operation body receive and
  parsing occur only after admission; excess requests are rejected without
  reading body bytes. Declared-length 400/413 precedence, actual-byte limits
  and capacity recovery remain intact.
- **No new local-only prerelease blocker was found in the assessed scope.**
  R1 remains explicitly accepted and unfixed. This result does not change the
  trusted-IMAP-server boundary or grant broader deployment/security approval.

### Independent verification and limitations

DBB executed **195 focused existing tests (5.84 seconds)** and **four independent
probes (0.20 seconds)**. These covered real artifact-lock contention, valid and
invalid nonces, health responsiveness, shared admission, cancellation, capacity
recovery and one-time GET behavior. Existing regressions also cover real sockets,
streaming, body boundaries, malformed input, shutdown and file cleanup.

Restricted sandbox runs hung and were bounded/stopped; they are not passes.
The focused set passed with the required local thread/socket permissions.
Two mistakes in the initial probe assumptions were corrected; initial failing
and final passing evidence are both retained. The parent checked the final
JUnit counts, absence of failures/errors/skips, and report/probe/evidence hashes.

One behavior remains explicit: an admitted GET cancelled while waiting for the
artifact lock can later consume its own bearer capability after the lock clears,
without delivering content. It retains its worker slot until completion. DBB
found no new nonce-less consumption path and did not classify this as a blocker
under the existing one-time capability semantics. This is a documented behavior,
not a further code fix or a new owner-approved risk disposition.

The complete DBB report, source references, exact commands, hashes and limits
are in `temp/two-pr-delivery/dbb-merged-reassessment.md` (SHA256
`055c00d6d5d9b2b502fab1456a25a3ca519d6c15122a225e933b15e943b601b4`).
Fresh evidence is under `temp/two-pr-delivery/dbb-merged-reassessment/`.

No production code, model configuration, accepted risk, commit, PR, deployment,
live mail account or release was changed by this assessment. No full-suite,
Windows runtime, proxy/deployment penetration, fuzzing, destructive exhaustion,
dependency/provenance scan or general security guarantee is claimed for this
round. Permanently hung synchronous workers, slow admitted clients and external
launcher/transport limits remain as previously documented. Authentication and
mutual enrollment remain separate work; remote/shared deployment is unsupported.

## Remediation round 3 — R1 upstream response bounds

Date: 2026-10-16  
Change: [PR #32](https://github.com/omry/arbiter/pull/32)  
Candidate: `50e5fde9f7266a86b2baa0103d49bba13824f754`  
Base: merged main `a5f8e23d5237d43eed6b8f7aa2ff549bf29bf7ba`

### Implemented behavior

This is a truth fix for the demonstrated R1 allocation gaps on the PR revision.
The operator-owned IMAP setting `limits.upstream_server.max_response_bytes`
defaults to 64 MiB (67,108,864 bytes). It bounds greeting and command responses,
including protocol framing, literals and retained unsolicited responses.
Claimed literals are checked before reading; reads use bounded chunks. Cached
responses consume the next command's available budget, including empty entries.
Overflow closes the connection and returns `upstream_protocol_error` with domain
code `imap.response_too_large`, `transient: no` and `replay: unknown`: a mailbox
mutation may already have taken effect.

Full-message literals also share the existing 50 MiB message budget. Ambiguous
or duplicate body responses are rejected before MIME parsing. The existing
message limit, 16 KiB preview default and internal 50 MiB metadata budget remain.
Documentation and the `arbiter-imap` release fragment describe these distinctions.

### Verification

Four new aggregate/ambiguous-body regressions fail against the original merged
revision and pass with the fix. The 60 new unit cases and five new local-socket
cases cover framing, literals, retained cache, greetings, command coverage,
connection shutdown, exact boundaries and body parsing. Production tests use
both plain and TLS transport classes; local socket scenarios use no live account.

The full Python suite passes: **1,247 passed, 2 skipped**. All **403 IMAP tests**
pass separately on Python 3.10 and 3.14. Black (one worker), Pyrefly, Go tests and
the website build pass. The current executing agent reviewed the full diff;
corrections address Python 3.14 buffer consistency, empty-cache accounting and
ambiguous body selection. This local review is not an independent assessment.

Evidence: `temp/r1-remediation/baseline-and-scope.json`,
`red-green-baseline.json`, `new-regressions-verified-original.xml`,
`local-review.json` and `pr32-round1/result.json` in the same directory.
Failed first attempts remain recorded, including a wrong-source baseline run,
test fixture corrections and the Python 3.14 mixed-reader failure. They are not
reported as passes. Two full-suite skips require real Windows ACL checks.

### PR approval evidence

The focused PR is open, ready and approved at the exact candidate above.
[Codex review](https://github.com/omry/arbiter/pull/32#issuecomment-6208961621)
returned no findings; Gitar also found no issues. All **13 required CI checks**
pass: Python lint; Python 3.10, 3.11, 3.12 and 3.14; Linux, macOS and Windows
on x64 and arm64; Website; and Gitar. The workflow-only website deployment is
skipped on this PR. No remote-review correction or extra PR was needed.

The canonical approval state is
[comment 6208842605](https://github.com/omry/arbiter/pull/32#issuecomment-6208842605),
version 4, verified at 2026-10-16T05:50:11Z. Approval fingerprint:
`sha256:22f8b60a1aabeacc38d1753381ecb8860a48f55e39472be0fc8902e00e17cb9a`.
The complete review/CI/approval receipts are retained under
`/home/omry/.local/state/awd/swe-review-authority/runs/run_635d0225tzw543tm8m6mv3c5bp/pr32-cycle/`.
A CI observation changed during the first collection; the failed observation is
preserved and a fresh stable collection succeeded. The full local profile ran
on the published head; final reuse checked unchanged tracked source, command
results and log hashes. It did not substitute descendant-only evidence.

### Limitations and remaining action

These bounds close the demonstrated resource gaps; they do not establish general
hostile-server support or a process-memory ceiling. Configured IMAP servers
remain trusted and email content remains untrusted under the owner's existing
decision. Authentication and mutual enrollment remain separate. Remote/shared
deployment remains unsupported. No live mail accounts, destructive exhaustion,
fuzzing, production benchmarks or independent corrected-revision reassessment
were run by this remediation. Merge and independent reassessment remain separate
actions; this record does not grant release-wide security signoff.

## Findings round 4

Date: 2026-10-16  
Assessor: delegated GPT-6 Sol agent, independent of the implementing agent  
Assessed merged main: `19fb583c032abc6d013dbe6aa3f2342ff5a6e83f` (PR #32)

This round supersedes the pending-merge/reassessment status in Remediation round
3. Its historical approval evidence and all previous findings/dispositions are
preserved. The parent and assessor confirmed an empty tracked diff between the
approved head `50e5fde9f7266a86b2baa0103d49bba13824f754` and the merge. Tests ran
on the merged checkout; imported modules resolved to that checkout.

The requested `sol-dbb` configuration selects GPT-6 Sol with high reasoning.
The launcher did not expose that custom role by name, so the parent launched its
configured model with the same security instructions. Daybreak Blue session
enablement was not visible and is not claimed. This is the delegated agent's
independent assessment, not an external audit.

### Disposition

- **DB-03-R1: demonstrated response-byte allocation gaps resolved on merged
  source (truth fix).** The 64 MiB upstream budget covers greetings, complete
  command responses, framing, literals and retained unsolicited entries,
  including empty entries. The full-message guard enforces the aggregate
  50 MiB literal budget; ambiguous or duplicate bodies are rejected before
  MIME parsing. Overflows close the session before excessive literal reads.
- **DB-03-R2/R3: existing truth fixes remain present in the reviewed adjacent
  scope.** The focused artifact I/O, HTTP body admission and worker regressions
  pass on the merged revision. This round does not repeat every earlier probe.
- **No new local-only prerelease blocker was found in this bounded review.**
  No new risk disposition, product fix or release-wide signoff was made.

### Independent verification

| Executed check | Result |
| --- | --- |
| Full IMAP suite, Python 3.10.20 | 403 passed; 0 failed/errors/skipped; 1.931 s |
| Full IMAP suite, Python 3.13.12 | 403 passed; 0 failed/errors/skipped; 1.087 s |
| Full IMAP suite, Python 3.14.2 | 403 passed; 0 failed/errors/skipped; 1.544 s |
| Focused HTTP/artifact suite, Python 3.13.12 | 180 passed; 0 failed/errors/skipped; 4.165 s |
| Independently authored transcript/socket probe | 9 checks passed; 0.002 s |

The probe exercises APPEND continuation and final-response accounting, aggregate
full-message rejection before a second literal read, duplicate-body rejection,
empty cached replies, and oversized greeting rejection before CAPABILITY. Both
plain and TLS transport parser classes are exercised with in-memory streams;
the additional greeting probe uses a disposable plain loopback socket. This is
not evidence of a real TLS handshake or interception test. Earlier unit-only
compatibility runs passed 368 cases per version; full runs above supersede their
narrower coverage.

Restricted-sandbox socket failures and a stopped thread stall are environment
failures, not passes or product findings. The first full sandbox failure log
was overwritten by its successful rerun; a targeted socket-denial reproduction
is retained. The report states that evidence limitation. Successful executions
used local socket/thread permission on unchanged source. The parent verified
all retained artifact/source hashes, JUnit counts, independent probe output,
merged-source equivalence and unchanged tracked content before integration.

Full independent report: `temp/r1-merged-reassessment.md` (SHA256
`6b530f67ae0cef5925828fe8abdd283e42bc77d89010806f500a640af3f00cd7`).
Exact commands, source locations, logs, JUnit files and hashes:
`temp/r1-merged-reassessment/evidence-manifest.json` (SHA256
`e920a869bf2d739951a70bc854a5fe06d873f72d3e70affb52931417b8004b9d`).
Parent verification: `temp/r1-merged-reassessment/parent-verification.json`.

### Limits and remaining work

Configured IMAP servers remain trusted; email/MIME content remains untrusted.
The 64 MiB response limit is not a whole-process memory ceiling. Message bytes,
parsed MIME objects and concurrent operations can coexist; MIME nesting, part
count, decoding expansion and CPU are not bounded by this response limit.
These are stated limits of the fix, not new accepted risks or a claim of full
hostile-server containment.

This round did not run the full repository suite, lint/types, Go, website,
Windows runtime, proxy/deployment tests, live mail accounts, real TLS
interception, fuzzing, dependency/provenance scans, production load tests or
destructive exhaustion. Permanently hung workers and deployment-level
connection limits were not tested. No product code, policy, commit, PR, release,
deployment or live account was changed by the reassessment. Authentication and
mutual enrollment remain separate; remote/shared deployment is unsupported.

## Findings round 5

Date: 2026-10-16  
Assessor: actual DBB subagent, selected through `agent_type: dbb`  
Configured alias: `gpt-daybreak-blue-latest`, reasoning effort `high`  
Assessed merged main: `19fb583c032abc6d013dbe6aa3f2342ff5a6e83f`

The owner explicitly requested this assessment using the actual DBB alias after
the separate Sol review. The launcher selected the DBB role and its fixed model
alias; this is distinct from running Sol with security instructions. Attribution
records launcher selection, not independent inspection of provider internals.

### Decision

**Clean within the exact assessed R1 and adjacent R2/R3 scope.** DBB found no
concrete security finding. It independently inspected the actual merged source,
verified the reviewed-head-to-merge empty tracked diff, and checked module import
paths on Python 3.10, 3.13 and 3.14. It challenged accounting resets, framing,
continuations, retained empty responses, aggregate literals, ambiguous bodies
before MIME parsing, failed-session reuse and Python 3.14 buffer handling.

The demonstrated R1 byte-allocation and ambiguous-body gaps are resolved on this
merged revision (truth fixes). Adjacent R2/R3 controls remain present and their
focused regressions pass. This agrees with the independent Sol result; neither
review grants release-wide signoff or makes a new risk acceptance.

### Executed verification

| Check | Result |
| --- | --- |
| Independent transcript probes on Python 3.10 / 3.13 / 3.14 | 14 passed each; 42 checks across runs |
| Targeted IMAP regression suites on the same versions | 101 passed each; 0 failed/errors/skipped |
| Real loopback IMAP compatibility on Python 3.10 / 3.14 | 9 passed each; 0 failed/errors/skipped |
| Adjacent HTTP/artifact suite on Python 3.13 | 180 passed; 0 failed/errors/skipped; 4.179 s JUnit |

The independent probes exercise the real parser for both plain and TLS transport
classes using small in-memory streams. They verify bounded greeting reads,
exact/one-byte-short response boundaries, APPEND continuation plus final reply,
retained empty replies, rejection before a second excessive literal read,
ambiguous bodies rejected before an instrumented forbidden MIME parser, and no
later wire writes after overflow. Actual TLS handshakes were not tested.

A first HTTP attempt used a nonexistent test path and collected zero tests; it
is preserved as failed evidence. A corrected restricted-sandbox attempt stalled
and was bounded at 30 seconds; it is not a pass or a product finding. The distinct
successful HTTP run used local socket/thread permission. The parent verified
all 11 retained artifact hashes, source hashes, JUnit counts, the 42 captured
probe results, exact merged checkout and unchanged tracked files.

Independent report: `temp/dbb-r1-merged-actual.md`, SHA256
`38be94fa149bb2a92ecbd74108bee088ac563b4caaf6af44b526c3230fb16613`.
Evidence manifest: `temp/dbb-r1-merged-actual/evidence-manifest.json`, SHA256
`72fe9fa1723f244217141547f2a92004ce1566fb325b7aa866d88519d4d7b905`.
The report contains exact commands, source locations, times and untested limits.
Parent verification is `temp/dbb-r1-merged-actual/parent-verification.json`.

### Limits

Configured IMAP servers remain trusted; sender-controlled email/MIME content
remains untrusted. The per-response byte budget does not establish a process
memory ceiling, general hostile-server support or independent MIME/CPU limits.
Authentication and mutual enrollment remain separate; remote/shared deployment
is unsupported. This round did not run the full repository suite, lint/types,
Go, website, Windows runtime, live accounts, real TLS handshakes/interception,
proxy/deployment penetration, fuzzing, dependency/provenance scans, production
load tests, destructive exhaustion or recovery from permanently hung workers.
No product source, policy, commit, PR, release, deployment or live account was
changed by this review. Previous rounds and their limitations remain historical
evidence for their stated revisions.
