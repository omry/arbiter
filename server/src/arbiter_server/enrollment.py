"""Bounded, ephemeral public enrollment requests. Registration grants no trust."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import threading
import time
import unicodedata
from uuid import uuid4

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, x25519

from .errors import OperationError

MAX_ADVERTISEMENT_BYTES = 64 * 1024
ENROLLMENT_LIFETIME = 600
REGISTRATION_TIMEOUT = 10


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
class Advertisement:
    selector: str
    name: str
    access_spki: bytes
    portable_spki: bytes
    recipient_spki: bytes
    digest: str


def decode_advertisement(raw: bytes | bytearray) -> Advertisement:
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
        if (
            not isinstance(value, dict)
            or set(value) != fields
            or type(value["version"]) is not int
            or value["version"] != 1
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
        return Advertisement(selector, name, keys[0], keys[1], keys[2], digest)
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

    def reset(self) -> None:
        with self._lock:
            self._window_deadline = 0.0
            self._epoch += 1
            self._requests.clear()

    def _prune(self, now: float) -> None:
        for selector, request in tuple(self._requests.items()):
            if request.deadline <= now:
                del self._requests[selector]

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
