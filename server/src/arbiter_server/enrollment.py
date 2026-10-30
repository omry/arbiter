"""Bounded, ephemeral public enrollment requests. Registration grants no trust."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
import hashlib
import hmac
import json
import re
import threading
import time
import unicodedata
from uuid import UUID, uuid4

from cryptography import x509
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, x25519

from .errors import OperationError

MAX_ADVERTISEMENT_BYTES = 64 * 1024
ENROLLMENT_LIFETIME = 600
REGISTRATION_TIMEOUT = 10
LIST_PAGE_SIZE = 32


def _invalid() -> OperationError:
    return OperationError(
        "validation_error",
        "Invalid enrollment advertisement.",
        transient="no",
        replay="safe",
    )


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _decode64(value: object, size: int, *, url: bool = False) -> bytes:
    if not isinstance(value, str) or len(value) > 4 * ((size + 2) // 3):
        raise ValueError("invalid encoded field")
    raw = base64.b64decode(
        value + "=" * (-len(value) % 4), altchars=b"-_" if url else None, validate=True
    )
    encoded = (
        base64.urlsafe_b64encode(raw).decode().rstrip("=")
        if url
        else base64.b64encode(raw).decode()
    )
    if len(raw) != size or value != encoded:
        raise ValueError("noncanonical encoded field")
    return raw


@dataclass(frozen=True)
class PortableIdentityClaim:
    owner_id: str
    agent_id: str
    issuer_sha256: str
    certificate_der: bytes


def _portable_claim(
    value: object, portable_spki: bytes
) -> PortableIdentityClaim | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "owner_id",
        "agent_id",
        "issuer_sha256",
        "certificate_der",
    }:
        raise ValueError("portable claim shape")
    owner, agent = value["owner_id"], value["agent_id"]
    for identity in (owner, agent):
        if not isinstance(identity, str) or str(UUID(identity, version=4)) != identity:
            raise ValueError("portable identity")
    fingerprint, encoded = value["issuer_sha256"], value["certificate_der"]
    if (
        owner == agent
        or not isinstance(fingerprint, str)
        or re.fullmatch(r"[0-9a-f]{64}", fingerprint) is None
    ):
        raise ValueError("portable issuer fingerprint")
    if not isinstance(encoded, str) or len(encoded) > 5464:
        raise ValueError("portable certificate bound")
    der = base64.b64decode(encoded, validate=True)
    if not 0 < len(der) <= 4096 or base64.b64encode(der).decode() != encoded:
        raise ValueError("portable certificate encoding")
    certificate = x509.load_der_x509_certificate(der)
    if (
        certificate.public_bytes(serialization.Encoding.DER) != der
        or certificate.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        != portable_spki
    ):
        raise ValueError("portable certificate key")
    # Anonymous decoding supplies no issuer trust, profile or possession proof.
    return PortableIdentityClaim(owner, agent, fingerprint, der)


@dataclass(frozen=True)
class Advertisement:
    selector: str
    name: str
    access_spki: bytes
    portable_spki: bytes
    recipient_spki: bytes
    digest: str
    portable_identity: PortableIdentityClaim | None = None


def decode_advertisement(raw: bytes | bytearray, *, version: int = 1) -> Advertisement:
    try:
        if not raw or len(raw) > MAX_ADVERTISEMENT_BYTES:
            raise ValueError("advertisement size")
        text = raw.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            raise ValueError("advertisement must not contain a byte-order marker")
        value = json.loads(text, object_pairs_hook=_unique)
        fields = {
            "version",
            "selector",
            "name",
            "access_spki",
            "portable_spki",
            "recipient_spki",
        }
        if version == 2:
            fields.add("portable_identity")
        if (
            version not in (1, 2)
            or not isinstance(value, dict)
            or set(value) != fields
            or type(value["version"]) is not int
            or value["version"] != version
        ):
            raise ValueError("advertisement shape")
        selector, name = value["selector"], value["name"]
        _decode64(selector, 16, url=True)
        if (
            not isinstance(name, str)
            or not 1 <= len(name.encode("utf-8")) <= 256
            or name.strip() != name
            or any(unicodedata.category(c) == "Cc" for c in name)
        ):
            raise ValueError("invalid display name")
        keys = []
        for field, size in (
            ("access_spki", 91),
            ("portable_spki", 91),
            ("recipient_spki", 44),
        ):
            der = _decode64(value[field], size)
            key = serialization.load_der_public_key(der)
            if field == "recipient_spki":
                if not isinstance(key, x25519.X25519PublicKey):
                    raise ValueError("wrong recipient key")
            elif not isinstance(key, ec.EllipticCurvePublicKey) or not isinstance(
                key.curve, ec.SECP256R1
            ):
                raise ValueError("wrong signing key")
            if (
                key.public_bytes(
                    serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                != der
            ):
                raise ValueError("noncanonical public key")
            keys.append(der)
        if keys[0] == keys[1]:
            raise ValueError("signing keys must be separate")
        # Bind the exact accepted wire bytes. Both peers can compute this
        # without relying on language-specific JSON canonicalization.
        digest = hashlib.sha256(raw).hexdigest()
        claim = (
            _portable_claim(value["portable_identity"], keys[1])
            if version == 2
            else None
        )
        return Advertisement(selector, name, keys[0], keys[1], keys[2], digest, claim)
    except (
        ValueError,
        TypeError,
        UnicodeError,
        RecursionError,
        UnsupportedAlgorithm,
    ) as exc:
        raise _invalid() from exc


@dataclass(frozen=True)
class PendingRequest:
    advertisement: Advertisement
    revision: str
    created_at: float
    deadline: float


@dataclass(frozen=True)
class ApprovedRequest:
    request: PendingRequest
    grants: tuple[tuple[str, str], ...]
    secret: bytes = field(repr=False)


class EnrollmentRequests:
    """One instance per server lifetime; local administration alone opens it."""

    def __init__(
        self, max_pending: int = 20, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        if type(max_pending) is not int or max_pending < 1:
            raise ValueError(
                "arbiter.limits.max_pending_enrollments must be a positive integer"
            )
        self.max_pending = max_pending
        self._clock = clock
        self._lock = threading.Lock()
        self._window_deadline = 0.0
        self._epoch = 0
        self._requests: dict[str, PendingRequest] = {}
        self._approved: dict[str, ApprovedRequest] = {}
        self._committing: dict[str, ApprovedRequest] = {}

    def reset(self) -> None:
        with self._lock:
            self._window_deadline = 0.0
            self._epoch += 1
            self._requests.clear()
            self._approved.clear()

    def _prune(self, now: float) -> None:
        for selector, request in tuple(self._requests.items()):
            if request.deadline <= now:
                del self._requests[selector]
                self._approved.pop(selector, None)

    def approved(self, selector: str) -> ApprovedRequest | None:
        """Trusted pairing code only; never serialize a secret-bearing context."""
        with self._lock:
            self._prune(self._clock())
            return self._approved.get(selector)

    def approval_is_current(self, approval: ApprovedRequest) -> bool:
        """A retained secret-bearing snapshot is never live authorization."""
        with self._lock:
            self._prune(self._clock())
            return (
                self._approved.get(approval.request.advertisement.selector) is approval
            )

    def exhaust_approval(self, approval: ApprovedRequest) -> None:
        """Trusted pairing admission uses this only after five admitted failures."""
        with self._lock:
            selector = approval.request.advertisement.selector
            if self._approved.get(selector) is approval:
                self._approved.pop(selector)
                self._requests.pop(selector, None)

    def complete(
        self,
        approval: ApprovedRequest,
        commit: Callable[[Callable[[], None]], bytes],
    ) -> bytes:
        """Reserve an approval, then consume it at one live commit decision.

        That decision orders reset/expiry against activation without holding a
        registry lock during SQLite I/O. Later invalidation cannot undo an
        already authorized transaction. A failed write restores only the same
        still-live approval, never a request invalidated in the meantime.
        """
        decided = False
        with self._lock:
            selector = approval.request.advertisement.selector
            epoch = self._epoch

            def check() -> None:
                nonlocal decided
                with self._lock:
                    if (
                        decided
                        or self._approved.get(selector) is not approval
                        or self._requests.get(selector) is not approval.request
                        or self._clock() >= approval.request.deadline
                    ):
                        raise ValueError("Enrollment approval is no longer current")
                    # Only this transaction can consume the approval. Keep the
                    # request counted until the write settles or is invalidated.
                    self._approved.pop(selector)
                    decided = True

            if selector in self._committing:
                raise ValueError("Enrollment activation is in progress; try again")
            if (
                self._approved.get(selector) is not approval
                or self._clock() >= approval.request.deadline
            ):
                raise ValueError("Enrollment approval is no longer current")
            self._committing[selector] = approval
        try:
            result = commit(check)
            with self._lock:
                if self._requests.get(selector) is approval.request:
                    self._requests.pop(selector)
            return result
        except BaseException:
            with self._lock:
                if (
                    decided
                    and self._epoch == epoch
                    and self._requests.get(selector) is approval.request
                    and self._clock() < approval.request.deadline
                ):
                    self._approved[selector] = approval
            raise
        finally:
            with self._lock:
                self._committing.pop(selector, None)

    def _summary(self, request: PendingRequest, now: float) -> dict[str, object]:
        advertisement = request.advertisement
        approved = self._approved.get(advertisement.selector)
        return {
            "selector": advertisement.selector,
            "name": advertisement.name,
            "revision": request.revision,
            "request_digest": advertisement.digest,
            "key_fingerprints": {
                role: hashlib.sha256(key).hexdigest()
                for role, key in (
                    ("access", advertisement.access_spki),
                    ("portable", advertisement.portable_spki),
                    ("recipient", advertisement.recipient_spki),
                )
            },
            "status": "approved" if approved else "pending",
            "remaining_seconds": max(0, int(request.deadline - now)),
            "grant_count": len(approved.grants) if approved else 0,
        }

    def control(
        self,
        command: str,
        arguments: dict[str, object],
        accounts: set[tuple[str, str]],
    ) -> dict[str, object]:
        """Called only through the OS-authenticated local control dispatcher."""
        if command == "open":
            if arguments:
                raise ValueError("Invalid enrollment control arguments.")
            self.open_window()
            with self._lock:
                now = self._clock()
                self._prune(now)
                return {
                    "initialized": True,
                    "open": now < self._window_deadline,
                    "pending_count": len(self._requests),
                }
        if command == "list":
            fields = set(arguments)
            if fields not in (set(), {"cursor"}, {"selector"}):
                raise ValueError("Invalid enrollment control arguments.")
            cursor = arguments.get("cursor")
            selector = arguments.get("selector")
            if cursor is not None and not isinstance(cursor, str):
                raise ValueError("Invalid enrollment list cursor.")
            if selector is not None and not isinstance(selector, str):
                raise ValueError("Invalid enrollment selector.")
            with self._lock:
                now = self._clock()
                self._prune(now)
                requests = tuple(self._requests.values())
                if selector is not None:
                    request = self._requests.get(selector)
                    rows = [self._summary(request, now)] if request else []
                    next_cursor = None
                else:
                    start = 0
                    if cursor is not None:
                        boundary = cursor.split(":", 1)
                        if len(boundary) != 2:
                            raise ValueError(
                                "Enrollment list cursor is invalid; list again."
                            )
                        boundary_selector, boundary_revision = boundary
                        for index, request in enumerate(requests):
                            if request.advertisement.selector == boundary_selector:
                                if request.revision != boundary_revision:
                                    raise ValueError(
                                        "Enrollment list cursor is invalid; list again."
                                    )
                                start = index + 1
                                break
                        else:
                            raise ValueError(
                                "Enrollment list cursor is invalid; list again."
                            )
                    page = requests[start : start + LIST_PAGE_SIZE]
                    rows = [self._summary(request, now) for request in page]
                    next_cursor = (
                        f"{page[-1].advertisement.selector}:{page[-1].revision}"
                        if start + len(page) < len(requests)
                        else None
                    )
                return {
                    "initialized": True,
                    "open": now < self._window_deadline,
                    "pending_count": len(requests),
                    "requests": rows,
                    "next_cursor": next_cursor,
                }
        fields = {"selector", "revision", "request_digest"}
        if command == "approve":
            fields |= {"psk", "grants"}
        if command not in {"approve", "reject"} or set(arguments) != fields:
            raise ValueError("Invalid enrollment control arguments.")
        secret, grants = b"", ()
        if command == "approve":
            try:
                secret = _decode64(arguments["psk"], 32, url=True)
                names = arguments["grants"]
                if not isinstance(names, list) or not all(
                    isinstance(name, str) and name.count("/") == 1 for name in names
                ):
                    raise ValueError
                grants = tuple(sorted(tuple(name.split("/")) for name in names))
                if len(set(grants)) != len(grants) or any(
                    grant not in accounts for grant in grants
                ):
                    raise ValueError
            except (ValueError, TypeError, UnicodeError):
                raise ValueError(
                    "Invalid enrollment secret or account grants."
                ) from None
        selector = arguments["selector"]
        if not isinstance(selector, str):
            raise ValueError("Invalid enrollment selector.")
        with self._lock:
            now = self._clock()
            self._prune(now)
            request = self._requests.get(selector)
            if request is None:
                raise ValueError("Enrollment request is missing or expired.")
            if (
                request.revision != arguments["revision"]
                or request.advertisement.digest != arguments["request_digest"]
            ):
                raise ValueError("Enrollment selection changed; list requests again.")
            committing = self._committing.get(selector)
            if committing is not None and (
                command != "reject" or self._approved.get(selector) is not committing
            ):
                raise ValueError("Enrollment activation is in progress; try again")
            if command == "reject":
                self._approved.pop(selector, None)
                del self._requests[selector]
                return {"initialized": True, "rejected": selector}
            existing = self._approved.get(selector)
            if existing is not None:
                if existing.grants != grants or not hmac.compare_digest(
                    existing.secret, secret
                ):
                    raise ValueError("Enrollment approval is immutable; reject first.")
            else:
                self._approved[selector] = ApprovedRequest(request, grants, secret)
            return {"initialized": True, "request": self._summary(request, now)}

    def open_window(self) -> None:
        """Local-only primitive; reopening never rewrites existing requests."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            self._window_deadline = now + ENROLLMENT_LIFETIME

    def require_open(self) -> tuple[int, float]:
        with self._lock:
            if self._clock() >= self._window_deadline:
                raise OperationError(
                    "policy_denied",
                    "Enrollment is closed. Ask the server operator to open enrollment through local administration, then try again.",
                    transient="no",
                    replay="safe",
                )
            return self._epoch, self._window_deadline

    def pending(self) -> tuple[PendingRequest, ...]:
        with self._lock:
            self._prune(self._clock())
            return tuple(self._requests.values())

    def register(self, raw: bytes | bytearray) -> PendingRequest:
        epoch, window_deadline = self.require_open()
        advertisement = decode_advertisement(raw)
        with self._lock:
            if advertisement.selector in self._committing:
                raise _invalid()
            now = self._clock()
            if epoch != self._epoch or now >= min(
                window_deadline, self._window_deadline
            ):
                raise OperationError(
                    "policy_denied",
                    "Enrollment window closed; ask the operator to reopen it.",
                    transient="no",
                    replay="safe",
                )
            self._prune(now)
            existing = self._requests.get(advertisement.selector)
            if existing is not None:
                if existing.advertisement != advertisement:
                    raise OperationError(
                        "conflict",
                        "Enrollment selector already names a different request.",
                        transient="no",
                        replay="unsafe",
                    )
                return existing
            if len(self._requests) >= self.max_pending:
                raise OperationError(
                    "resource_exhausted",
                    "Enrollment capacity is full; try again later.",
                    transient="yes",
                    replay="safe",
                )
            request = PendingRequest(
                advertisement, uuid4().hex, now, now + ENROLLMENT_LIFETIME
            )
            self._requests[advertisement.selector] = request
            return request
