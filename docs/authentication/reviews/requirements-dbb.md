# DBB review: authentication requirements baseline

Reviewed 2026-10-20. This is my independent, bounded defensive-security
assessment of the requirements baseline. It is a requirements-level review,
not approval of a high-level design, protocol, implementation, dependency,
deployment, or release.

## Verdict

**Pass at requirements level.** The exact reviewed baseline is sufficiently
complete, internally consistent, faithful to the approved scope, and testable to
start high-level design. I found no blocking requirement-level security finding
and no material behavior, trust, or operating-cost decision that exceeds the
approved direction.

This verdict applies only to `docs/authentication/requirements.md` with SHA-256
`d56e92a7cb01f131ba499650c4a5d7b2c8d17adf2cdc7d612eee7c5f53d07929`.
A content change requires review of the changed bytes before a new verdict. This
record is the initial durable requirements-level review; it does not stamp either
of the later stages.

## Reviewed inputs

The target and contextual inputs matched the frozen copies listed in
`temp/reviews/authentication-requirements-2026-10-20/dbb-inputs.json`.

| Input | SHA-256 | Use in this review |
| --- | --- | --- |
| [Requirements baseline](../requirements.md) | `d56e92a7cb01f131ba499650c4a5d7b2c8d17adf2cdc7d612eee7c5f53d07929` | Normative target; reviewed in full |
| [Authentication overview](../README.md) | `30a59ab67e811053d141b1289a0f0fd79961d7a11277b5862919136cbcec5a1a` | Stage and document-status context |
| [Earlier synthesis](../design.md) | `fe8be1e3e35b8f2c31477951f555cb96e011b76cd0ebae46c9933ff036e4e382` | Historical/candidate design context only |
| [Decision and evidence index](../evidence.md) | `b96cff306e49ddaef9c509048de2c554324abebefbccf7d32fd8797449b993bd` | Provenance and supersession checks |

I also used the prior DBB reports to identify the security outcomes that the new
baseline must preserve. Those reports do not approve this baseline. I inspected
only targeted portions of the immutable interview for high-impact decisions on
owner recovery, portable identity, pairing, transport, lifecycle, backup,
restore, undo, migration, and server-key replacement. I did not read the full
historical archive.

## Requirements assessment

### Trust, identity, and recovery

R01–R06 define one trusted owner/server, explicitly accept agent-readable client
keys in lightweight mode, and keep owner/recovery secrets outside agent custody.
They separate durable owner, server, agent, enrollment, access-key, and grant
authority. Portable identity can establish who is asking but cannot enroll,
grant access, replace a server-local key, or clear a denial. Each server retains a
distinct access credential and independent grants.

The recovery outcome is complete enough for design: owner authorization can
replace keys while preserving the durable identity, grants, suspensions, and
revocations without a second server approval. Portable identity alone cannot do
so. Recovery after server-key compromise cannot depend only on the compromised
key. The single protected recovery file may authorize both backup decryption and
live recovery, and the requirements demand clear disclosure of that combined
power. Exact internal key roles remain a legitimate high-level-design choice.

### Enrollment and activation

R07–R09 retain one exact owner approval and one client-to-operator secret
handoff. The 32-byte secret, ten-minute lifetime, five PSK-authenticated admitted
exchanges, twenty-request default, separate unauthenticated resource accounting,
and lack of a general fairness promise are stated without conflation. Anonymous
traffic cannot spend a request's semantic attempt budget.

The baseline requires durable client state before effective server commitment,
coherent single activation, authenticated idempotent reconciliation, fixed
deadlines, and bounded cleanup. Its acceptance scenarios cover wrong secrets,
replay, concurrency, deadline boundaries, abandonment, crash, and a lost final
reply. The exact exchange transcript and state machine belong in B1; their
absence is not a requirements defect.

### Transport, administration, grants, and denial

R10–R14 require native client-to-Arbiter authentication and confidentiality that
does not trust a forwarding proxy with native plaintext, keys, or capabilities.
They preserve independent server identity across URL, proxy, and outer-certificate
changes and require verification before sending credentials. Private setup has
no public DNS, public CA, Certbot, or native CA-installation prerequisite.

Administrative authority remains separate from loopback location and SSH
transport. Future browser administration must have explicit bounded authority,
Host/Origin/CSRF defenses, and isolation from persistent browser state and other
local services. This carries the prior browser-origin finding as an outcome while
properly leaving the exact fresh-origin/session mechanism to B3. The public TCP
passthrough topology remains a recommendation rather than a mandatory second
delivery topology.

Account grants precede the existing account-policy flow without adding a second
client policy layer. Discovery and direct lookup cannot expose ungranted account
data. Revocation prevents new admission and further affected delivery across
connections while accurately excluding already delivered data and committed
upstream effects. The requirements also prohibit uncertain mutation and
consuming-GET replay. Exact transport limits remain B3 work; the unapproved
64-connection proposal is not adopted.

### Lifecycle and continuity

R15–R17 state the 30-day due threshold, 24-hour fixed grace from preparation,
and 180-day authenticated-contact cutoff as distinct clocks. Retry, candidate
withholding, ordinary old-key use, and incomplete restricted exchanges cannot
move those deadlines. Ordinary access becomes unavailable after grace while only
the exact pending completion remains reachable; expiry does not silently weaken
ordinary verification or force earlier repair. Revocation and owner repair take
precedence.

The current-plus-one-predecessor bound is narrow: one authentication-only fallback
after unambiguous rejection by the same verified server, with no use after denial,
uncertain results, or unverified identity. Planned server-key replacement needs a
verified transition, while compromise recovery cannot rely on the compromised
key. Certificate profiles and restricted-completion carriers are B2/B3 design
work, not missing outcomes.

### Artifacts and client processing

R18–R20 preserve creator identity plus nonce authorization, non-consuming HEAD,
one-time GET even on stream failure, server-readable retained encryption, and
recipient-encrypted client copies. They correctly state that encryption does not
prove origin and require the artifact, creator, recipient, expected content, and
authenticated server context to remain bound.

All four approved processing choices remain distinct. Immediate plaintext stdin
honestly permits an authentic partial prefix and irreversible tool effects.
Verified encrypted-temp-to-stdin, explicit plaintext-temp, and save-decrypted
require origin, content, and finalization checks before tool start or publication,
with stable verified bytes through processing. The baseline makes no atomic-tool
promise. It retains the 50 MiB, 2,000-artifact, 1 GiB, ten-minute idle, and
twenty-four-hour maximum limits and confines legacy deletion to server-managed
ownerless artifacts.

### Backup, restore, undo, and migration

R21–R25 require an external operator job and a coherent, authenticated, encrypted
application bundle containing the real authority state and configured secrets.
Verification cannot trust a key supplied only by the incoming bundle. Restore
works offline with the target stopped, does not silently merge state or reset
timers, and explicitly accepts that a chosen stale snapshot can roll back grants,
revocations, and deadlines.

The latest coherent pre-restore undo point has no automatic expiry and remains
usable after service use with strong loss warnings. It cannot undo upstream
effects. Migration uses the approved cooperative maintenance procedure and one
active destination. It preserves the authenticated old-URL handoff direction
without adding client authority generations, signed takeover, permanent
retirement, clustering, or automatic old-host restart. These are explicit
accepted limits rather than hidden security claims.

## Prior findings and stage separation

The baseline carries the required outcome of each prior design-level finding:

| Prior issue | Requirements-level disposition |
| --- | --- |
| Independent owner recovery authority | Preserved by R04–R06 and R21; exact signer/profile deferred to B2/B5 |
| Restricted late rotation without ordinary-verifier bypass | Preserved by R15–R17; carrier/profile deferred to B2/B3 |
| Artifact origin and stable verified bytes | Preserved by R18–R19; envelope/backend deferred to B4 |
| Pairing attempt accounting | Preserved by R08–R09; exchange/replay state deferred to B1 |
| Private browser origin isolation | Preserved by R11; session/origin design deferred to B3 |
| Duplicate writable migration authority | Explicitly retained as the accepted procedural limitation in R25 |

The B1–B5 table correctly identifies the next design decisions and evidence. It
does not claim that candidate Noise, TLS-over-WebSocket, age, JWS, SQLite,
certificate encodings, internal signers, browser-session strategy, or helper
libraries have been selected or validated. The high-level design must show that
its chosen mechanisms jointly satisfy these requirements. A later implementation
plan must then divide that design into reviewable, testable slices. Neither later
stage is approved here.

## Accepted limits and remaining uncertainty

The reviewed baseline expressly accepts the trusted operator/server, readable
lightweight client keys, partial immediate stdin, irreversible committed upstream
effects, deliberately stale restore, and cooperative procedural migration. I did
not reinterpret those accepted limits as findings.

Feasibility, interoperability, crash safety, admission ownership, cross-language
verification, storage behavior, and platform support remain unproved until the
relevant high-level designs and later implementation evidence exist. The existing
candidate contracts are useful inputs but are not binding selections. These are
stage limits, not a clean verdict on the eventual protocol or product.

## Checks performed and final identity

I read all 25 requirements and all eight acceptance groups; checked scope,
authority separation, attacker-controlled inputs, deadline and retry behavior,
revocation races, stale-state effects, resource bounds, and cross-section
consistency; traced high-impact decisions and supersessions through the evidence
index and targeted interview excerpts; and compared all four current inputs
byte-for-byte with their frozen snapshots. The traceability record covers D1–D104,
but I treated that as citation coverage rather than fidelity proof.

I ran no product, protocol, compatibility, or live-service tests; installed no
dependencies; performed no scan; and changed no product code. Immediately after
writing this report, I rechecked that the normative target remained unchanged at
SHA-256 `d56e92a7cb01f131ba499650c4a5d7b2c8d17adf2cdc7d612eee7c5f53d07929`.

No conclusion here certifies the complete protocol, implementation, dependencies,
deployment, or release.
