# DBB follow-up: authentication and enrollment residual repair

Reviewed 2026-10-20. This is my independent, targeted defensive architecture
assessment of the one open finding from the second DBB report and the related H1
and H3 clarifications. It is not a renewed broad review, protocol approval,
implementation audit, browser support certification, or release signoff.

## Verdict

**Finding 5 is resolved at design level.** A fresh unpredictable 128-bit
`.localhost` hostname and an already-reserved port create a new tuple origin for
each launch. Under the stated trusted client OS/user boundary, prior local
services cannot have installed controlling browser state for that unpredictable
origin, and a current local listener cannot claim the reserved endpoint. The
explicit bearer remains the authority; origin freshness supplies isolation only.

The repair does not assume universal `.localhost`, browser, resolver, SSH, or
dual-stack behavior. It makes platform validation a B3 gate and fails closed when
the required origin and binding cannot be provided. That is a defensible design
boundary. A successful platform result must mean that every address to which the
launch hostname can connect is exclusively owned by the reserved listener or
forward for the session, including relevant IPv4 and IPv6 paths. Binding one
address family while leaving another claimable is a failed gate, not a supported
configuration.

The H1 and H3 clarifications make the prior assessment explicit without changing
its result. H1 discloses the existing recovery signer's live authority. H3 now
requires stable verified bytes or a bounded immutable snapshot and correctly
rejects late rehashing as a substitute. I found no new design conflict in these
changes.

The six original findings now stand as follows: findings 1–5 are resolved at
design level; finding 6 remains the accepted operational limitation under the
cooperative-server, infrequent-migration model. B1–B5 evidence is still required
before implementation readiness.

## Reviewed input identities

I reviewed these exact bytes and compared all three document inputs byte-for-byte
with `temp/reviews/authentication-repair-2026-10-20/followup-inputs/`.

| Input | SHA-256 |
| --- | --- |
| `docs/authentication-and-enrollment.md` | `5ea29485808f8d9a3545d0a0f856f43965ab961091d6d2b4e26daa117d8c7339` |
| `docs/authentication-and-enrollment-security-contracts.md` | `cf531a3a0aa32a94f11e39d99b8957d4ad08f0da8b719c6c3288906460d83acd` |
| `docs/authentication-and-enrollment-dbb-rereview.md` | `3a10049323693d896675a76b1b85312b3af4a0c7fc1e0f990203efc0a2ea17b7` |
| `temp/reviews/authentication-repair-2026-10-20/followup-verification.json` | `956e03abf905a9edb9ba23d8e5cf657bede69799268771d3004525e113db036b` |

I inspected the H1, H3, and H5 contract deltas, their corresponding main-design
summaries, the prior DBB disposition, and the follow-up verification record. I
checked the standards properties relevant to the repair: an origin is separated
by scheme, host, and port; service-worker registrations persist and are matched
to their stored scope/origin; and RFC 6761 reserves names beneath `.localhost`
for loopback treatment. I did not reread the complete historical interview or
unchanged protocol sections.

## Finding 5 follow-up

The prior attack required a browser profile with a hostile service worker or
cached page at a reused origin. H5 now removes that prerequisite:

- The CLI reserves the listener or SSH local forward before it exposes a token.
- Every launch uses a fresh unpredictable hostname and does not reuse the prior
  origin for refresh or reconnection.
- The exact origin comes from trusted launch configuration rather than the
  incoming `Host` header.
- The bootstrap remains single-use and fragment-held. The resulting bearer is
  explicit, memory-only, bounded, and checked with exact `Host`, `Origin`, CSRF,
  method, and CORS rules.
- Unsupported resolution or binding fails with a diagnostic. There is no stable
  origin, random-port-only, public-DNS, hosts-file, CA, or certificate-warning
  fallback.
- CSP, `no-store`, and the ban on private-origin service workers are correctly
  labeled defense in depth. The design does not claim that response headers can
  evict a worker which already controls a navigation.

The fresh hostname is the essential correction. Browser origins compare the host
name, not the resolved loopback address, so a worker registered for an old random
hostname cannot control the new hostname merely because both resolve to loopback.
The [HTML origin model](https://html.spec.whatwg.org/multipage/browsers.html#concept-origin-tuple)
uses scheme, host, and port; the
[Service Workers specification](https://www.w3.org/TR/service-workers/#service-worker-registration-lifetime)
stores persistent registrations by storage key and serialized scope URL. The
[RFC 6761 localhost rules](https://www.rfc-editor.org/rfc/rfc6761.html#section-6.3)
support the no-public-DNS design but allow application and resolver variation,
which is why the explicit platform gate is necessary.

### Evidence still required under B3

This design disposition does not prove browser support. The existing B3 work must
demonstrate, for each supported browser/platform path:

- unpredictable hostname generation and exact tuple-origin separation from a
  previously poisoned origin;
- resolution only to loopback endpoints owned by the reserved listener/forward,
  with IPv4/IPv6 and wildcard/specific-bind behavior accounted for;
- no token display or browser launch before successful exclusive reservation;
- failed resolution/binding produces the promised diagnostic without a stable
  origin or public-network fallback;
- hostile old workers/caches, attempted origin preclaim, refresh, forwarding
  failure, Host rebinding, missing/foreign/null Origin, CSRF, replay, expiry, and
  restart cannot yield a protected request without the explicit bearer.

A failed platform check narrows support; it does not reopen ordinary loopback as
authority or justify weakening H5.

## H1 and H3 clarification assessment

H1 now says plainly that theft of the one protected recovery file can authorize
attacker-held replacement keys at registered live servers as well as decrypt
available backups. It keeps the signer purpose-separated, absent from agents,
servers, sync storage, and unattended backup jobs. It remains a candidate schema
extension inside the existing one operator-managed file and adds no second local
repair ceremony. This resolves the disclosure limitation in the prior report and
does not conflict with D44's simple local pairing path or D64's one-file operator
procedure. Finding 1 remains resolved at design level; its schema and transition
evidence remain B2.

H3 now states that retaining an open descriptor does not make bytes immutable.
The verified encrypted-temp mode must have no in-scope writable alias between
passes or create a bounded immutable process-owned snapshot before tool launch.
It correctly says that an end-of-pass rehash is too late after bytes have reached
the tool. It does not add persistent plaintext, archive recovery, signatures, or
an atomic tool-execution promise, and it preserves all four processing choices.
Finding 3 remains resolved at design level; backend and TOCTOU proof remain B4.

## Scope of signoff

I found no concrete new attack or contradiction introduced by the follow-up
repairs. Findings 1–5 are resolved only as architecture contracts. Finding 6 is
still an operational acceptance, not automated split-brain prevention. No
protocol, implementation, dependency, crash, admission, browser-platform, or
release evidence was produced by this follow-up.
